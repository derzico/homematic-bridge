#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Protected, verified application backups; never stop/control the bridge.

Backups contain secrets. No automatic extraction or production restore is offered.
SQLite databases are copied through their read-only backup API, including WAL.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid

PREFIX = "bridge-backup-"
NAME = re.compile(r"bridge-backup-\d{8}T\d{6}Z-[0-9a-f]{8}\.tar\.gz\Z")
EXCLUDED_DIRS = {".git", ".venv", "logs", "__pycache__", ".pytest_cache", "htmlcov", ".claude", ".codex", ".agents"}
EXCLUDED_FILES = {".DS_Store", ".coverage"}


class BackupError(Exception):
    pass


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _files(root):
    result = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for directory_name in dirs:
            if (Path(directory) / directory_name).is_symlink():
                raise BackupError("Symlink directories are not supported")
        for name in sorted(names):
            if name in EXCLUDED_FILES or name.endswith(".pyc"):
                continue
            path = Path(directory) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise BackupError("Non-regular installation files are not supported")
            result.append(path)
    return result


def _is_sqlite(path):
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def _signature(path):
    info = path.stat()
    return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _sidecars_exist(source):
    return any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def _copy_sqlite(source, target, *, immutable=False):
    uri = source.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5)) as original:
        with contextlib.closing(sqlite3.connect(target)) as copy:
            deadline = time.monotonic() + 30
            def progress(_status, _remaining, _total):
                if time.monotonic() >= deadline:
                    raise BackupError("SQLite backup exceeded deadline")
            original.backup(copy, pages=256, sleep=0.05, progress=progress)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("SQLite backup failed integrity check")


def _copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if _is_sqlite(source):
        # No checkpoint or mode changes to the source database. An offline WAL
        # database with no sidecars may still try to create an index on RO mount.
        try:
            _copy_sqlite(source, target)
        except sqlite3.OperationalError:
            if _sidecars_exist(source):
                raise  # Never bypass locks/ignore WAL for an active database.
            before = _signature(source)
            _copy_sqlite(source, target, immutable=True)
            if _sidecars_exist(source) or before != _signature(source):
                raise BackupError("Offline SQLite database changed during backup")
        shutil.copystat(source, target)
        return
    # Atomic replacement / concurrent modification must not produce a torn copy.
    for _ in range(3):
        before = source.stat()
        shutil.copy2(source, target)
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns):
            return
    raise BackupError("Installation changed repeatedly during backup; retry later")


def _runtime(container):
    if not container:
        return {}
    try:
        image = subprocess.run(["docker", "inspect", "--format", "{{.Image}}", container],
                               capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        env = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Env}}", container],
                             capture_output=True, text=True, check=True, timeout=15)
        # Never persist other environment variables, process stderr or raw inspect.
        values = json.loads(env.stdout) or []
        commit = next((v.split("=", 1)[1] for v in values if v.startswith("BRIDGE_BUILD_SHA=")), "unknown")
        if not re.fullmatch(r"[0-9a-f]{40}|unknown", commit):
            commit = "unknown"
        return {"image": image if re.fullmatch(r"sha256:[0-9a-f]{64}", image) else "unknown", "build_commit": commit}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"available": False}


def verify(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    name = manifest.get("archive")
    if not isinstance(name, str) or not NAME.fullmatch(name) or manifest_path.name != name + ".json":
        raise BackupError("Invalid backup manifest name")
    archive_path = manifest_path.with_name(name)
    if archive_path.is_symlink() or manifest_path.is_symlink():
        raise BackupError("Backup symlinks are not supported")
    if digest(archive_path) != manifest.get("sha256"):
        raise BackupError("Archive checksum mismatch")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise BackupError("Empty or invalid file manifest")
    expected = {}
    for record in records:
        name = record.get("path") if isinstance(record, dict) else None
        if not isinstance(name, str) or name in expected:
            raise BackupError("Invalid or duplicate manifest file")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or len(path.parts) < 2 or path.parts[0] != "homematic-bridge":
            raise BackupError("Unsafe archive path")
        expected[name] = record
    found = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.name not in expected or member.name in found:
                raise BackupError("Unexpected, unsafe or duplicate archive entry")
            record = expected[member.name]
            with archive.extractfile(member) as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != record.get("sha256") or member.size != record.get("size"):
                raise BackupError("File checksum or size mismatch")
            found.add(member.name)
    if found != set(expected):
        raise BackupError("Missing archive entries")
    return manifest


def _prune(destination, keep):
    # Only this tool's completed, validated pairs; never old rollback directories.
    pairs = []
    for path in destination.glob(PREFIX + "*.tar.gz.json"):
        if path.is_symlink() or not NAME.fullmatch(path.name.removesuffix(".json")):
            continue
        try:
            manifest = json.loads(path.read_text())
            name = manifest.get("archive")
            if not isinstance(name, str) or not NAME.fullmatch(name) or name + ".json" != path.name:
                continue
            archive = destination / name
            if archive.is_symlink() or not archive.is_file():
                continue
            created = dt.datetime.fromisoformat(manifest["created_at"])
            if created.tzinfo is None:
                continue
            pairs.append((created.timestamp(), path, archive))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for _, manifest, archive in sorted(pairs, reverse=True)[keep:]:
        archive.unlink()
        manifest.unlink()


def create(root, destination, keep=14, container=None):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if type(keep) is not int or not 1 <= keep <= 365:
        raise BackupError("keep must be 1..365")
    if not root.is_dir() or not (root / "main.py").is_file() or not (root / "config").is_dir():
        raise BackupError("Not a bridge installation")
    if destination.is_symlink() or destination.resolve().is_relative_to(root):
        raise BackupError("Backup destination must be separate from installation")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.stat().st_uid != os.geteuid():
        raise BackupError("Backup directory must belong to current user")
    os.chmod(destination, 0o700)
    lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    lock_fd = os.open(destination / ".backup.lock", lock_flags, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("Another backup is running") from None
        files = _files(root)
        sqlite_files = {path for path in files if _is_sqlite(path)}
        files = [path for path in files if not any(str(path) == str(db) + suffix
                 for db in sqlite_files for suffix in ("-wal", "-shm", "-journal"))]
        estimate = sum(path.stat().st_size for path in files)
        if shutil.disk_usage(destination).free < estimate * 2 + 128 * 1024 * 1024:
            raise BackupError("Insufficient free space for a safe backup")
        created = dt.datetime.now(dt.timezone.utc)
        name = PREFIX + created.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".tar.gz"
        published = []
        try:
            with tempfile.TemporaryDirectory(prefix=".partial-", dir=destination) as temporary:
                stage = Path(temporary)
                records = []
                for source in files:
                    relative = source.relative_to(root)
                    target = stage / "snapshot" / relative
                    _copy_file(source, target)
                    records.append({"path": "homematic-bridge/" + relative.as_posix(),
                                    "sha256": digest(target), "size": target.stat().st_size})
                archive = stage / name
                with tarfile.open(archive, "w:gz") as tar:
                    for record in records:
                        tar.add(stage / "snapshot" / Path(record["path"]).relative_to("homematic-bridge"),
                                arcname=record["path"], recursive=False)
                os.chmod(archive, 0o600)
                manifest = {"format": 1, "created_at": created.isoformat(), "archive": name,
                            "sha256": digest(archive), "files": records, "runtime": _runtime(container),
                            "contains_secrets": True, "image_included": False}
                meta = stage / (name + ".json")
                meta.write_text(json.dumps(manifest, indent=2) + "\n")
                os.chmod(meta, 0o600)
                verify(meta)
                for item in (archive, meta):
                    with item.open("rb") as stream:
                        os.fsync(stream.fileno())
                    final = destination / item.name
                    os.replace(item, final)
                    published.append(final)
                verify(published[-1])
                directory_fd = os.open(destination, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            for path in published:
                path.unlink(missing_ok=True)
            raise
        # Keep the new verified backup even when retention cleanup fails.
        _prune(destination, keep)
        return published[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("create")
    backup.add_argument("--root", required=True)
    backup.add_argument("--destination", required=True)
    backup.add_argument("--keep", type=int, default=14)
    backup.add_argument("--container")
    check = commands.add_parser("verify")
    check.add_argument("manifest")
    args = parser.parse_args()
    previous_umask = os.umask(0o077)
    try:
        if args.command == "create":
            path = create(args.root, args.destination, args.keep, args.container)
            print("BACKUP_OK", path)
        else:
            verify(args.manifest)
            print("VERIFY_OK")
    except (BackupError, OSError, ValueError, sqlite3.Error, tarfile.TarError):
        # Unexpected device/config contents or paths must not leak to journal.
        print("BACKUP_FAILED: validation, file access or storage error; no device actions performed")
        return 1
    finally:
        os.umask(previous_umask)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
