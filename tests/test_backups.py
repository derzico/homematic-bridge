import fcntl
import importlib.util
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tarfile
from unittest.mock import patch

import pytest

SOURCE = Path(__file__).parents[1] / "ops/backup.py"
spec = importlib.util.spec_from_file_location("bridge_backup", SOURCE)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "bridge"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "main.py").write_text("# application\n")
    (root / "config/config.yaml").write_text("homematic_token: private-test-token\n")
    (root / "data/api_key.txt").write_text("private-test-key")
    return root, tmp_path / "backups"


def test_protected_backup_verifies_and_preserves_originals(installation):
    root, dest = installation
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    meta = backup.create(root, dest)
    manifest = backup.verify(meta)
    assert manifest["contains_secrets"] is True
    assert manifest["image_included"] is False
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    for path in (meta, meta.with_name(manifest["archive"])):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(dest.glob(".partial-*"))
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    with tarfile.open(meta.with_name(manifest["archive"])) as archive:
        assert archive.extractfile("homematic-bridge/data/api_key.txt").read() == b"private-test-key"


def test_backup_excludes_logs_git_and_virtualenv(installation):
    root, dest = installation
    for folder in [".git", ".venv", "logs", "__pycache__"]:
        (root / folder).mkdir()
        (root / folder / "ignored").write_text("ignore")
    paths = [r["path"] for r in backup.verify(backup.create(root, dest))["files"]]
    assert len(paths) == 3


def test_sqlite_wal_is_consistent_and_not_checkpointed(installation, tmp_path):
    root, dest = installation
    path = root / "data/energy.sqlite3"
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE readings (value INTEGER)")
        conn.execute("INSERT INTO readings VALUES (42)")
        conn.commit()
        before = path.read_bytes()
        manifest = backup.verify(backup.create(root, dest))
        assert not any(r["path"].endswith(("-shm", "-wal")) for r in manifest["files"])
        with tarfile.open(dest / manifest["archive"]) as archive:
            restored = tmp_path / "restored.sqlite3"
            restored.write_bytes(archive.extractfile("homematic-bridge/data/energy.sqlite3").read())
        with sqlite3.connect(restored) as read:
            assert read.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert read.execute("SELECT value FROM readings").fetchone()[0] == 42
        assert path.read_bytes() == before
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_retention_preserves_latest_and_old_rollback(installation):
    root, dest = installation
    dest.mkdir()
    original = dest / "20261002-original"
    original.mkdir()
    (original / "source-config-data.tar.gz").write_bytes(b"legacy")
    manifests = [backup.create(root, dest, keep=2) for _ in range(4)]
    assert not manifests[0].exists()
    assert not manifests[1].exists()
    assert manifests[2].exists() and manifests[3].exists()
    assert (original / "source-config-data.tar.gz").read_bytes() == b"legacy"
    assert len(list(dest.glob("*.tar.gz"))) == 2


def test_failed_copy_does_not_prune_or_publish(installation):
    root, dest = installation
    previous = backup.create(root, dest)
    with patch.object(backup, "_copy_file", side_effect=OSError("private-secret-error")):
        with pytest.raises(OSError):
            backup.create(root, dest, keep=1)
    assert previous.exists()
    backup.verify(previous)
    assert len(list(dest.glob("*.tar.gz"))) == 1
    assert not list(dest.glob(".partial-*"))


def test_retention_failure_preserves_new_verified_copy(installation):
    root, dest = installation
    with patch.object(backup, "_prune", side_effect=OSError("no cleanup")):
        with pytest.raises(OSError):
            backup.create(root, dest)
    meta = next(dest.glob("*.json"))
    backup.verify(meta)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_symlink_in_installation_rejected(installation, tmp_path, kind):
    root, dest = installation
    target = tmp_path / "outside"
    if kind == "file":
        target.write_text("not in scope")
    else:
        target.mkdir()
    (root / "link").symlink_to(target)
    with pytest.raises(backup.BackupError):
        backup.create(root, dest)


def test_destination_cannot_recurse_into_source(installation):
    root, _ = installation
    with pytest.raises(backup.BackupError):
        backup.create(root, root / "backups")


def test_lock_prevents_parallel_backup(installation):
    root, dest = installation
    dest.mkdir()
    with (dest / ".backup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(backup.BackupError, match="Another"):
            backup.create(root, dest)


@pytest.mark.parametrize("keep", [0, 366, True, "14"])
def test_invalid_retention_rejected(installation, keep):
    root, dest = installation
    with pytest.raises(backup.BackupError):
        backup.create(root, dest, keep)


def test_insufficient_space_leaves_existing_backup(installation):
    root, dest = installation
    previous = backup.create(root, dest)
    with patch.object(backup.shutil, "disk_usage", return_value=type("Usage", (), {"free": 1})()):
        with pytest.raises(backup.BackupError, match="space"):
            backup.create(root, dest)
    assert previous.exists()


def test_archive_tampering_detected(installation):
    root, dest = installation
    meta = backup.create(root, dest)
    archive = meta.with_name(json.loads(meta.read_text())["archive"])
    with archive.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(backup.BackupError, match="checksum"):
        backup.verify(meta)


def test_manifest_path_traversal_rejected(installation):
    root, dest = installation
    meta = backup.create(root, dest)
    manifest = json.loads(meta.read_text())
    manifest["files"][0]["path"] = "homematic-bridge/../../outside"
    meta.write_text(json.dumps(manifest))
    with pytest.raises(backup.BackupError, match="Unsafe"):
        backup.verify(meta)


def test_prune_does_not_follow_tampered_archive_path(installation, tmp_path):
    root, dest = installation
    meta = backup.create(root, dest)
    manifest = json.loads(meta.read_text())
    outside = tmp_path / manifest["archive"]
    outside.write_bytes(b"must remain")
    manifest["archive"] = "../" + outside.name
    meta.write_text(json.dumps(manifest))
    backup._prune(dest, 0)
    assert outside.read_bytes() == b"must remain"


def test_cli_never_prints_credentials(installation):
    root, dest = installation
    result = subprocess.run([sys.executable, str(SOURCE), "create", "--root", str(root),
                             "--destination", str(dest)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "BACKUP_OK" in result.stdout
    assert "private-test-token" not in result.stdout + result.stderr
    assert "private-test-key" not in result.stdout + result.stderr


def test_runtime_metadata_whitelists_environment():
    responses = [subprocess.CompletedProcess([], 0, "sha256:" + "a" * 64),
                 subprocess.CompletedProcess([], 0, json.dumps(["BRIDGE_BUILD_SHA=" + "b" * 40, "SECRET=do-not-save"]))]
    with patch.object(backup.subprocess, "run", side_effect=responses):
        metadata = backup._runtime("bridge")
    assert metadata == {"image": "sha256:" + "a" * 64, "build_commit": "b" * 40}
    assert "do-not-save" not in json.dumps(metadata)


def test_closed_wal_readonly_fallback_is_stable(installation):
    root, dest = installation
    source = root / "data/energy.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE readings(value INTEGER)")
        conn.execute("INSERT INTO readings VALUES(42)")
    conn.close()
    original = source.read_bytes()
    copy_sqlite = backup._copy_sqlite
    def readonly_mount(source, target, *, immutable=False):
        if not immutable:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return copy_sqlite(source, target, immutable=immutable)
    with patch.object(backup, "_copy_sqlite", side_effect=readonly_mount):
        meta = backup.create(root, dest)
    backup.verify(meta)
    assert source.read_bytes() == original
    assert not backup._sidecars_exist(source)


def test_sqlite_fallback_never_ignores_active_wal(installation):
    root, dest = installation
    source = root / "data/energy.sqlite3"
    conn = sqlite3.connect(source)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE readings(value INTEGER)")
        conn.commit()
        assert backup._sidecars_exist(source)
        with patch.object(backup, "_copy_sqlite", side_effect=sqlite3.OperationalError("readonly")) as copy:
            with pytest.raises(sqlite3.OperationalError):
                backup.create(root, dest)
        assert copy.call_count == 1
        assert not list(dest.glob("*.json"))
    finally:
        conn.close()


def test_unstable_offline_sqlite_fallback_is_not_published(installation):
    root, dest = installation
    source = root / "data/energy.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE readings(value INTEGER)")
    conn.close()
    with patch.object(backup, "_copy_sqlite", side_effect=[sqlite3.OperationalError("readonly"), None]), \
         patch.object(backup, "_signature", side_effect=[(1, 2, 3, 4), (1, 2, 5, 6)]):
        with pytest.raises(backup.BackupError, match="changed"):
            backup.create(root, dest)
    assert not list(dest.glob("*.json"))
