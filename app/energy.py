# SPDX-License-Identifier: Apache-2.0
"""Private, opt-in Shelly energy history. Importing this module does no I/O.

Integration: ``collector.start()`` / ``collector.stop()``; render energy.html with
``energy_status=collector.status(), energy_summary=collector.summary()``.
``on_sample(dict)`` is called once per committed meter reading, outside DB locks.
Raw counters/deltas are Wh; callback import_kwh/export_kwh are cumulative kWh.
Missing/invalid/reset deltas are None, never zero.
The first reading establishes a baseline, not historical consumption.
"""

import ipaddress
import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import app.state as state
from app.adapters import shelly_adapter as shelly

log = logging.getLogger("bridge-ws")
DEFAULT_DB_PATH = "data/energy.sqlite3"
APPROXIMATION = (
    "Deltas are assigned to the interval end. Daily and monthly totals are "
    "approximate during outages or across midnight; resets and unknown values "
    "are excluded. The first reading only establishes a baseline."
)


def _config_snapshot():
    with state.config_lock:
        return {
            "energy": dict(state.config.get("energy") or {}),
            "shelly": dict(state.config.get("shelly") or {}),
        }


def _options(config):
    if not isinstance(config, dict):
        raise ValueError("config must be a mapping containing energy settings")
    cfg = config.get("energy", {})
    if not isinstance(cfg, dict):
        raise ValueError("energy must be a mapping")
    enabled = cfg.get("enabled", False)
    interval = cfg.get("interval_seconds", 60)
    retention = cfg.get("retention_days", 90)
    timezone = cfg.get("timezone", "Europe/Berlin")
    if not isinstance(enabled, bool):
        raise ValueError("energy.enabled must be a boolean")
    if type(interval) is not int or interval < 30:
        raise ValueError("energy.interval_seconds must be an integer >= 30")
    if type(retention) is not int or not 1 <= retention <= 730:
        raise ValueError("energy.retention_days must be an integer in 1..730")
    try:
        if not isinstance(timezone, str):
            raise ValueError("energy.timezone must be an IANA timezone")
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("energy.timezone must be an IANA timezone") from exc
    return dict(enabled=enabled, interval_seconds=interval,
                retention_days=retention, timezone=timezone)


def _number(value, *, counter=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(value) or (counter and value < 0):
        return None
    return value


def _identity(device):
    identifier = device.get("id")
    if isinstance(identifier, str) and identifier.strip():
        identifier = identifier.strip()
        try:
            ipaddress.ip_address(identifier)
        except ValueError:
            return "id:" + identifier, identifier
    mac = device.get("mac")
    if isinstance(mac, str):
        mac = mac.replace(":", "").replace("-", "").strip().upper()
        if len(mac) == 12 and all(c in "0123456789ABCDEF" for c in mac):
            return "mac:" + mac, mac
    return None


def _cutoff(now, options):
    # Retain at most N local calendar dates, including today, also across DST.
    local = datetime.fromtimestamp(now, ZoneInfo(options["timezone"]))
    first = local.date() - timedelta(days=options["retention_days"] - 1)
    return datetime.combine(first, datetime.min.time(), local.tzinfo).timestamp()


def _totals(import_wh=None, export_wh=None):
    return {
        "import_wh": import_wh, "export_wh": export_wh,
        "import_kwh": None if import_wh is None else import_wh / 1000,
        "export_kwh": None if export_wh is None else export_wh / 1000,
    }


def _add(total, row):
    for direction in ("import", "export"):
        field = direction + "_wh"
        if row[field] is not None:
            total[field] = (total[field] or 0.0) + row[field]
            total[direction + "_kwh"] = total[field] / 1000


class EnergyCollector:
    """One instance per DB/process. No scans, refreshes, updates or commands.

    config: full config mapping or zero-argument provider (default: state.config).
    db_path: local SQLite path; tests can use ':memory:'.
    on_sample: optional callable receiving the same dicts as sample_once().
    clock/connection_factory: injectable for isolated tests.
    Config is re-read each cycle. update_config(full_config) validates settings,
    wakes interval waits and starts/stops this collector according to enabled.
    stop(timeout=5) returns False if a request/callback has not finished yet;
    do not start a replacement until it returns True. close() releases SQLite.
    """

    def __init__(self, config=None, db_path=DEFAULT_DB_PATH, on_sample=None, *,
                 clock=time.time, connection_factory=sqlite3.connect):
        self._config = _config_snapshot if config is None else config
        self.db_path = str(db_path)
        self.on_sample = on_sample
        self._clock = clock
        self._connect = connection_factory
        self._config_lock = threading.Lock()
        self._db = None
        self._db_lock = threading.RLock()
        self._sample_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._last_sample_at = None
        self._last_error = None

    def _configuration(self):
        with self._config_lock:
            config = self._config() if callable(self._config) else self._config
        return config, _options(config)

    def update_config(self, config):
        """Apply full config (energy + shelly), no persistence; return status().

        Enabled starts/wakes the worker; disabled requests local stop and joins
        for up to 5s. A running=True result after disabling means a request or
        callback is still finishing; never launch a replacement at that point.
        The caller owns atomic persistence, authentication and browser controls.
        """
        options = _options(config)
        snapshot = dict(config)
        snapshot["energy"] = dict(config.get("energy") or {})
        snapshot["shelly"] = dict(config.get("shelly") or {})
        with self._config_lock:
            self._config = snapshot
        self._wake.set()
        if options["enabled"]:
            self.start()
        else:
            self.stop()
        return self.status()

    def _database(self):
        # Only called while holding _db_lock. No database/file access disabled.
        if self._db is None:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            db = self._connect(self.db_path, timeout=5, check_same_thread=False)
            try:
                db.row_factory = sqlite3.Row
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS energy_sources (
                        source_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                        name TEXT NOT NULL, meter_index INTEGER NOT NULL,
                        sampled_at REAL NOT NULL, import_total_wh REAL,
                        export_total_wh REAL, power_w REAL, voltage REAL,
                        current REAL, pf REAL
                    );
                    CREATE TABLE IF NOT EXISTS energy_baselines (
                        source_id TEXT NOT NULL, direction TEXT NOT NULL,
                        sampled_at REAL NOT NULL, total_wh REAL NOT NULL,
                        PRIMARY KEY (source_id, direction)
                    );
                    CREATE TABLE IF NOT EXISTS energy_samples (
                        source_id TEXT NOT NULL, sampled_at REAL NOT NULL,
                        import_wh REAL, export_wh REAL,
                        import_start_at REAL, export_start_at REAL,
                        import_reset INTEGER NOT NULL, export_reset INTEGER NOT NULL,
                        gap INTEGER NOT NULL, crosses_midnight INTEGER NOT NULL,
                        PRIMARY KEY (source_id, sampled_at)
                    );
                    CREATE INDEX IF NOT EXISTS energy_samples_time
                        ON energy_samples(sampled_at);
                """)
            except Exception:
                db.close()
                raise
            self._db = db
        return self._db

    def start(self):
        """Start one daemon worker; returns True iff a new worker was started."""
        _, options = self._configuration()
        if not options["enabled"]:
            return False
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._wake.clear()
            self._thread = threading.Thread(target=self._run, name="energy-collector", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=5):
        """Interrupt wait and join worker. Does not change shared stop flags."""
        with self._lifecycle_lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread is threading.current_thread():
            return False
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def close(self):
        """Stop worker and release DB; can be reopened by a later summary/start."""
        if not self.stop():
            return False
        with self._sample_lock, self._db_lock:
            if self._db is not None:
                self._db.close()
                self._db = None
        return True

    def _run(self):
        while not self._stop.is_set() and not state.stop_event.is_set():
            try:
                self.sample_once()
            except Exception:
                # No URLs, credentials or device payloads in logs/status errors.
                with self._db_lock:
                    self._last_error = "collection_failed"
                log.warning("Energy collection failed")
            waited_since = time.monotonic()
            # Local stop/config updates wake immediately. Poll global shutdown
            # at most once a second without ever setting/clearing its event.
            while not self._stop.is_set() and not state.stop_event.is_set():
                try:
                    _, options = self._configuration()
                    if not options["enabled"]:
                        return
                    interval = options["interval_seconds"]
                except Exception:
                    interval = 60
                remaining = interval - (time.monotonic() - waited_since)
                if remaining <= 0:
                    break
                self._wake.wait(min(remaining, 1.0))
                self._wake.clear()

    def sample_once(self):
        """Read eligible cached devices once; return committed meter dicts.

        Reads *only* shelly._get(ip, '/status', timeout=3.0); cache online must
        be exactly True and emeters nonempty. Timestamp is request completion.
        Each direction is independently validated/baselined. Callback failures
        cannot undo a commit or cause duplicate notification on replay.
        Returned/callback dicts include source_id, channel (zero-based), power_w,
        import_kwh/export_kwh (unrounded cumulative counters, nullable), and
        import_wh/export_wh (interval deltas, nullable). No internal UDP sender.
        """
        with self._sample_lock:
            config, options = self._configuration()
            if not options["enabled"]:
                return []
            cfg = config.get("shelly") or {}
            shelly.set_credentials(cfg.get("username"), cfg.get("password"))
            readings = []
            seen = set()
            had_error = False
            for device in shelly.load_cached():
                if self._stop.is_set() or state.stop_event.is_set():
                    break
                if not isinstance(device, dict) or device.get("online") is not True:
                    continue
                if (not isinstance(device.get("emeters"), (dict, list))
                        or not device["emeters"] or not device.get("ip")):
                    continue
                identity = _identity(device)
                if identity is None or identity[0] in seen:
                    continue
                seen.add(identity[0])
                try:
                    payload = shelly._get(device["ip"], "/status", timeout=3.0)
                    timestamp = _number(self._clock(), counter=True)
                    if timestamp is None:
                        raise ValueError("invalid clock")
                    if not isinstance(payload, dict) or not isinstance(payload.get("emeters"), list):
                        had_error = True
                        continue
                    for index, meter in enumerate(payload["emeters"]):
                        if not isinstance(meter, dict) or meter.get("is_valid") is not True:
                            continue
                        import_total = _number(meter.get("total"), counter=True)
                        export_total = _number(meter.get("total_returned"), counter=True)
                        if import_total is None and export_total is None:
                            continue
                        reading = dict(
                            source_id=f"shelly:{identity[0]}:emeter:{index}",
                            device_id=identity[1], name=str(device.get("name") or identity[1]),
                            meter_index=index, sampled_at=timestamp,
                            import_total_wh=import_total, export_total_wh=export_total,
                            **{key: _number(meter.get(raw)) for key, raw in (
                                ("power_w", "power"), ("voltage", "voltage"),
                                ("current", "current"), ("pf", "pf"))},
                        )
                        saved = self._record(reading, options)
                        if saved is not None:
                            readings.append(saved)
                except Exception:
                    had_error = True
            # Retention runs even if all devices are offline/invalid.
            with self._db_lock:
                db = self._database()
                with db:
                    db.execute("DELETE FROM energy_samples WHERE sampled_at < ?",
                               (_cutoff(self._clock(), options),))
                self._last_error = "collection_failed" if had_error else None
            # Outside the DB lock: callbacks may call status()/summary().
            for reading in readings:
                if self.on_sample is not None:
                    try:
                        self.on_sample(dict(reading))
                    except Exception:
                        with self._db_lock:
                            self._last_error = "callback_failed"
                        log.warning("Energy sample callback failed")
            return readings

    def _record(self, reading, options):
        source, timestamp = reading["source_id"], reading["sampled_at"]
        with self._db_lock:
            db = self._database()
            with db:
                previous = db.execute(
                    "SELECT sampled_at FROM energy_sources WHERE source_id = ?", (source,)
                ).fetchone()
                if previous is not None and timestamp <= previous["sampled_at"]:
                    return None
                result = dict(reading, gap=False, crosses_midnight=False, approximate=True)
                result["channel"] = reading["meter_index"]
                result["import_kwh"] = (None if reading["import_total_wh"] is None
                                        else reading["import_total_wh"] / 1000)
                result["export_kwh"] = (None if reading["export_total_wh"] is None
                                        else reading["export_total_wh"] / 1000)
                zone = ZoneInfo(options["timezone"])
                end_date = datetime.fromtimestamp(timestamp, zone).date()
                for direction in ("import", "export"):
                    counter = reading[direction + "_total_wh"]
                    delta, start, reset = None, None, False
                    baseline = db.execute(
                        "SELECT sampled_at, total_wh FROM energy_baselines "
                        "WHERE source_id = ? AND direction = ?", (source, direction)
                    ).fetchone()
                    if counter is not None:
                        if baseline is not None:
                            start = baseline["sampled_at"]
                            reset = counter < baseline["total_wh"]
                            if not reset:
                                delta = counter - baseline["total_wh"]
                            result["gap"] |= timestamp - start > options["interval_seconds"] * 1.5
                            result["crosses_midnight"] |= datetime.fromtimestamp(start, zone).date() != end_date
                        db.execute(
                            "INSERT INTO energy_baselines VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(source_id, direction) DO UPDATE SET "
                            "sampled_at=excluded.sampled_at, total_wh=excluded.total_wh",
                            (source, direction, timestamp, counter),
                        )
                    result[direction + "_wh"] = delta
                    result[direction + "_start_at"] = start
                    result[direction + "_reset"] = reset
                db.execute(
                    "INSERT INTO energy_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id) DO UPDATE SET name=excluded.name, "
                    "sampled_at=excluded.sampled_at, import_total_wh=excluded.import_total_wh, "
                    "export_total_wh=excluded.export_total_wh, power_w=excluded.power_w, "
                    "voltage=excluded.voltage, current=excluded.current, pf=excluded.pf",
                    tuple(reading[key] for key in (
                        "source_id", "device_id", "name", "meter_index", "sampled_at",
                        "import_total_wh", "export_total_wh", "power_w", "voltage", "current", "pf")),
                )
                db.execute(
                    "INSERT INTO energy_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(result[key] for key in (
                        "source_id", "sampled_at", "import_wh", "export_wh",
                        "import_start_at", "export_start_at", "import_reset", "export_reset",
                        "gap", "crosses_midnight")),
                )
            self._last_sample_at = timestamp
            return result

    def status(self):
        """JSON-safe settings/lifecycle state, no credentials or IP addresses."""
        _, options = self._configuration()
        with self._db_lock:
            return dict(options, running=self._thread is not None and self._thread.is_alive(),
                        last_sample_at=self._last_sample_at, last_error=self._last_error,
                        db_path=self.db_path, approximate=True, approximation=APPROXIMATION)

    def summary(self, *, now=None):
        """JSON-safe retained totals in configured timezone (default Berlin).

        today/month: period + import/export Wh/kWh (None without known deltas).
        days/months: ascending observed periods, never synthetic zero buckets.
        meters: latest raw reading, stale flag and today's/month's totals.
        Retention counts local calendar dates including today; baselines and
        latest source metadata survive pruning. Disabled summary does no DB I/O.
        """
        _, options = self._configuration()
        now = self._clock() if now is None else now
        local = datetime.fromtimestamp(now, ZoneInfo(options["timezone"]))
        day, month = local.strftime("%Y-%m-%d"), local.strftime("%Y-%m")
        result = dict(timezone=options["timezone"], approximate=True,
                      approximation=APPROXIMATION, days=[], months=[], meters=[],
                      today=dict(period=day, **_totals()), month=dict(period=month, **_totals()))
        if not options["enabled"]:
            return result
        with self._db_lock:
            db = self._database()
            cutoff = _cutoff(now, options)
            with db:
                db.execute("DELETE FROM energy_samples WHERE sampled_at < ?", (cutoff,))
            db.create_function("energy_day", 1, lambda ts: datetime.fromtimestamp(
                ts, local.tzinfo).strftime("%Y-%m-%d"))
            rows = db.execute(
                "SELECT source_id, energy_day(sampled_at) AS period, "
                "SUM(import_wh) AS import_wh, SUM(export_wh) AS export_wh, "
                "SUM(import_reset) AS import_resets, SUM(export_reset) AS export_resets, "
                "MAX(gap) AS gap, MAX(crosses_midnight) AS crosses_midnight "
                "FROM energy_samples WHERE sampled_at >= ? AND sampled_at <= ? "
                "GROUP BY source_id, period ORDER BY period, source_id", (cutoff, now)
            ).fetchall()
            sources = db.execute("SELECT * FROM energy_sources ORDER BY source_id").fetchall()
        days, months, meters = {}, {}, {}
        for source in sources:
            meter = dict(source)
            meter["sampled_at_local"] = datetime.fromtimestamp(source["sampled_at"], local.tzinfo).isoformat()
            meter["stale"] = now - source["sampled_at"] > options["interval_seconds"] * 1.5
            meter["today"] = dict(period=day, **_totals())
            meter["month"] = dict(period=month, **_totals())
            meters[source["source_id"]] = meter
        for row in rows:
            period = row["period"]
            for buckets, key in ((days, period), (months, period[:7])):
                if key not in buckets:
                    buckets[key] = dict(period=key, approximate=True, gap=False,
                                        crosses_midnight=False, import_resets=0, export_resets=0,
                                        **_totals())
                bucket = buckets[key]
                _add(bucket, row)
                bucket["gap"] |= bool(row["gap"])
                bucket["crosses_midnight"] |= bool(row["crosses_midnight"])
                bucket["import_resets"] += row["import_resets"]
                bucket["export_resets"] += row["export_resets"]
            meter = meters[row["source_id"]]
            if period == day:
                _add(meter["today"], row)
            if period[:7] == month:
                _add(meter["month"], row)
        result.update(days=list(days.values()), months=list(months.values()), meters=list(meters.values()))
        if day in days:
            result["today"] = dict(days[day])
        if month in months:
            result["month"] = dict(months[month])
        return result


# Lazy singleton. Parent may set collector.on_sample before start().
collector = EnergyCollector()
