# SPDX-License-Identifier: Apache-2.0
"""Isolated energy tests: in-memory SQLite, fake cache/HTTP/clock and own Flask app."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from flask import Flask, render_template
from jinja2 import ChoiceLoader, DictLoader, StrictUndefined

from app import energy


def at(value):
    return datetime.fromisoformat(value).timestamp()


def device(**overrides):
    return dict({"id": "test-em", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.0.2.1",
                 "name": "Test meter", "online": True, "gen": 1,
                 "emeters": {"0": {"total_kwh": 999.99}}}, **overrides)


def meter(total=1000.125, returned=200.25, **overrides):
    return dict({"is_valid": True, "total": total, "total_returned": returned,
                 "power": 12.5, "voltage": 230.1, "current": 0.15, "pf": 0.8}, **overrides)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network/filesystem/device mutation is forbidden")

    # Even accidental calls to the real HTTP helper are blocked.
    monkeypatch.setattr(energy.shelly.requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(energy.Path, "mkdir", forbidden)
    for name in ("load_cached", "save_cache", "start_scan", "_run_scan", "_detect",
                 "refresh_device", "refresh_all", "set_relay", "trigger_update",
                 "check_all_updates", "_post", "_get_status_gen1", "_get_status_gen2"):
        if hasattr(energy.shelly, name):
            monkeypatch.setattr(energy.shelly, name, forbidden)
    monkeypatch.setattr(energy.shelly, "set_credentials", Mock())


@pytest.fixture
def rig(monkeypatch):
    class Rig:
        def __init__(self):
            self.now = at("2026-10-02T12:00:00+02:00")
            self.config = {"energy": {"enabled": True, "interval_seconds": 30},
                           "shelly": {"username": "fixture-user", "password": "fixture-password"}}
            self.cache = [device()]
            self.payload = {"emeters": [meter()]}
            self.get = Mock(side_effect=lambda *a, **kw: self.payload)
            self.instances = []
            monkeypatch.setattr(energy.shelly, "load_cached", lambda: self.cache)
            monkeypatch.setattr(energy.shelly, "_get", self.get)
            self.collector = self.make()

        def make(self, **kwargs):
            kwargs.setdefault("config", self.config)
            kwargs.setdefault("db_path", ":memory:")
            kwargs.setdefault("clock", lambda: self.now)
            instance = energy.EnergyCollector(**kwargs)
            self.instances.append(instance)
            return instance

        def sample(self, total=1000.125, returned=200.25, *, seconds=30, **overrides):
            self.now += seconds
            self.payload = {"emeters": [meter(total, returned, **overrides)]}
            return self.collector.sample_once()

    result = Rig()
    yield result
    for instance in result.instances:
        assert instance.close()


def test_disabled_default_has_no_io(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("must not open DB"))
    instance = energy.EnergyCollector({}, connection_factory=forbidden)
    monkeypatch.setattr(energy.shelly, "_get", forbidden)
    assert instance.start() is False
    assert instance.sample_once() == []
    assert instance.summary()["days"] == []
    status = instance.status()
    assert status["enabled"] is False
    assert status["interval_seconds"] == 60
    assert status["retention_days"] == 90
    assert status["timezone"] == "Europe/Berlin"
    assert status["db_path"] == "data/energy.sqlite3"
    assert instance.stop() is True
    forbidden.assert_not_called()


@pytest.mark.parametrize("setting,value", [
    ("enabled", "true"), ("enabled", 1),
    ("interval_seconds", 29), ("interval_seconds", True),
    ("interval_seconds", 30.5), ("interval_seconds", "60"),
    ("retention_days", 0), ("retention_days", 731),
    ("retention_days", False), ("retention_days", 1.0),
    ("timezone", "No/Such_Zone"), ("timezone", "../UTC"), ("timezone", None),
])
def test_invalid_configuration_rejected_without_io(setting, value):
    instance = energy.EnergyCollector({"energy": {setting: value}})
    with pytest.raises(ValueError):
        instance.start()
    with pytest.raises(ValueError):
        instance.sample_once()
    assert instance._db is None


@pytest.mark.parametrize("days", [1, 730])
def test_valid_configuration_bounds(rig, days):
    rig.config["energy"]["retention_days"] = days
    assert rig.collector.status()["retention_days"] == days


def test_full_configuration_provider_and_default_state(rig, monkeypatch):
    monkeypatch.setattr(energy.state, "config", rig.config)
    instance = rig.make(config=None)
    instance.sample_once()
    assert instance.status()["enabled"] is True
    dynamic = rig.make(config=lambda: rig.config)
    rig.config["energy"]["enabled"] = False
    assert dynamic.sample_once() == []
    assert dynamic._db is None


def test_only_online_cached_emeters_and_status_with_config_credentials(rig):
    rig.cache = [device(), device(id="offline", online=False), device(id="unknown", online=None),
                 device(id="truthy", online=1), device(id="switch", emeters={}),
                 device(id="missing-ip", ip=None), device(id="", mac=""), "invalid"]
    before = json.dumps(rig.cache)
    readings = rig.collector.sample_once()
    assert len(readings) == 1
    rig.get.assert_called_once_with("192.0.2.1", "/status", timeout=3.0)
    energy.shelly.set_credentials.assert_called_once_with("fixture-user", "fixture-password")
    assert json.dumps(rig.cache) == before
    assert readings[0]["import_total_wh"] == 1000.125
    assert readings[0]["export_total_wh"] == 200.25
    assert "fixture-password" not in json.dumps(rig.collector.status())
    assert "192.0.2.1" not in json.dumps(rig.collector.summary())


def test_reported_cache_mix_only_two_online_shem_devices_are_read(rig):
    rig.cache = [device(id=f"switch-{i}", emeters={}, model="SHSW-1") for i in range(7)]
    rig.cache += [device(id=f"em-{i}", ip=f"192.0.2.{i + 2}", model="SHEM") for i in range(2)]
    rig.cache += [device(id="plus", gen=2, emeters={}, model="Plus1"),
                  device(id="plug", emeters={}, model="SHPLG-S"),
                  device(id="offline-3em", online=False, model="SHEM-3"),
                  device(id="offline-em", online=False, model="SHEM")]
    readings = rig.collector.sample_once()
    assert {r["device_id"] for r in readings} == {"em-0", "em-1"}
    assert rig.get.call_count == 2
    assert {m["device_id"] for m in rig.collector.summary()["meters"]} == {"em-0", "em-1"}


def test_first_reading_unknown_deltas_then_raw_wh_precision(rig):
    first = rig.collector.sample_once()[0]
    assert first["import_wh"] is None and first["export_wh"] is None
    assert rig.collector.summary()["today"]["import_wh"] is None
    second = rig.sample(1000.375, 200.375)[0]
    assert second["import_wh"] == 0.25
    assert second["export_wh"] == 0.125
    summary = rig.collector.summary()
    assert summary["today"]["import_kwh"] == 0.00025
    assert summary["month"]["export_kwh"] == 0.000125
    assert summary["meters"][0]["today"] == dict(period="2026-10-02", **energy._totals(0.25, 0.125))
    # An unchanged valid counter really is zero consumption, unlike unknown.
    assert rig.sample(1000.375, 200.375)[0]["import_wh"] == 0.0


@pytest.mark.parametrize("invalid", [None, False, True, float("nan"), float("inf"),
                                    float("-inf"), -1, "200", 10**400])
def test_invalid_counter_skipped_independently_without_zero(rig, invalid):
    rig.collector.sample_once()
    sample = rig.sample(invalid, 201)[0]
    assert sample["import_total_wh"] is None
    assert sample["import_wh"] is None
    assert sample["export_wh"] == 0.75
    assert rig.collector.summary()["today"]["import_wh"] is None
    # Keep the valid old baseline through a missing direction.
    recovery = rig.sample(1001.125, 202)[0]
    assert recovery["import_wh"] == 1
    assert recovery["import_start_at"] == rig.now - 60
    assert recovery["gap"] is True


@pytest.mark.parametrize("valid", [False, None, 0, 1, "true", float("nan")])
def test_invalid_is_valid_skips_entire_meter(rig, valid):
    assert rig.sample(is_valid=valid) == []
    assert rig.collector.summary()["meters"] == []


def test_missing_validity_or_counters_and_malformed_meters(rig):
    rig.payload = {"emeters": [None, [], {}, {"is_valid": True},
                               {"total": 12}, meter(None, None)]}
    assert rig.collector.sample_once() == []
    assert rig.collector.summary()["days"] == []


def test_missing_export_not_fabricated_and_telemetry_sanitized(rig):
    rig.payload = {"emeters": [{"is_valid": True, "total": 12.25, "power": float("nan"),
                               "voltage": True, "current": "bad", "pf": float("inf")}]}
    sample = rig.collector.sample_once()[0]
    assert sample["export_total_wh"] is None and sample["export_wh"] is None
    assert all(sample[key] is None for key in ("power_w", "voltage", "current", "pf"))
    json.dumps(sample, allow_nan=False)
    json.dumps(rig.collector.summary(), allow_nan=False)


@pytest.mark.parametrize("payload", [None, {}, [], {"emeters": {}}, {"emeters": None}])
def test_bad_status_preserves_existing_baseline(rig, payload):
    rig.collector.sample_once()
    rig.payload = payload
    rig.now += 30
    assert rig.collector.sample_once() == []
    assert rig.collector.status()["last_error"] == "collection_failed"
    assert rig.sample(1001.125)[0]["import_wh"] == 1
    assert rig.collector.status()["last_error"] is None


def test_one_device_failure_does_not_block_others_or_leak_error(rig):
    rig.cache.append(device(id="second", ip="192.0.2.2"))
    rig.get.side_effect = [RuntimeError("fixture-password private URL"), rig.payload]
    samples = rig.collector.sample_once()
    assert len(samples) == 1 and samples[0]["device_id"] == "second"
    assert rig.collector.status()["last_error"] == "collection_failed"
    assert "fixture-password" not in json.dumps(rig.collector.status())


def test_stable_id_survives_ip_change_and_duplicate_cache_entries(rig):
    first = rig.collector.sample_once()[0]
    rig.cache = [device(ip="192.0.2.22"), device(ip="192.0.2.23")]
    second = rig.sample(1001.125)[0]
    assert first["source_id"] == second["source_id"] == "shelly:id:test-em:emeter:0"
    assert second["import_wh"] == 1
    assert rig.get.call_count == 2


@pytest.mark.parametrize("identifier", ["", None, "192.0.2.1"])
def test_mac_fallback_is_normalized_not_ip(rig, identifier):
    rig.cache = [device(id=identifier)]
    first = rig.collector.sample_once()[0]
    rig.cache = [device(id=identifier, ip="192.0.2.3", mac="aa-bb-cc-dd-ee-ff")]
    second = rig.sample(1001.125)[0]
    assert first["source_id"] == second["source_id"] == "shelly:mac:AABBCCDDEEFF:emeter:0"
    assert second["import_wh"] == 1


def test_independent_phases_and_devices(rig):
    rig.cache.append(device(id="second", ip="192.0.2.2"))
    rig.payload = {"emeters": [meter(100, 10), meter(200, 20), meter(300, 30)]}
    assert len(rig.collector.sample_once()) == 6
    rig.now += 30
    rig.payload = {"emeters": [meter(101, 11), meter(202, 22), meter(303, 33)]}
    samples = rig.collector.sample_once()
    assert len({s["source_id"] for s in samples}) == 6
    assert rig.collector.summary()["today"]["import_wh"] == 12
    assert rig.collector.summary()["today"]["export_wh"] == 12


@pytest.mark.parametrize("reset_import,reset_export", [(True, False), (False, True), (True, True)])
def test_resets_rebaseline_only_affected_direction(rig, reset_import, reset_export):
    rig.collector.sample_once()
    total = 2 if reset_import else 1001.125
    returned = 3 if reset_export else 201.25
    sample = rig.sample(total, returned)[0]
    assert sample["import_reset"] is reset_import
    assert sample["export_reset"] is reset_export
    assert sample["import_wh"] == (None if reset_import else 1)
    assert sample["export_wh"] == (None if reset_export else 1)
    next_sample = rig.sample(total + 0.5, returned + 0.25)[0]
    assert next_sample["import_wh"] == 0.5
    assert next_sample["export_wh"] == 0.25
    daily = rig.collector.summary()["days"][0]
    assert daily["import_resets"] == int(reset_import)
    assert daily["export_resets"] == int(reset_export)


def test_duplicate_and_out_of_order_do_not_change_baseline_or_callback(rig):
    pushed = Mock()
    rig.collector.on_sample = pushed
    rig.collector.sample_once()
    assert rig.sample(5000, 500, seconds=0) == []
    assert rig.sample(1, 1, seconds=-1) == []
    sample = rig.sample(1001.125, 201.25, seconds=31)[0]
    assert sample["import_wh"] == 1 and not sample["import_reset"]
    assert pushed.call_count == 2
    assert rig.collector._db.execute("SELECT COUNT(*) FROM energy_samples").fetchone()[0] == 2


def test_sqlite_failure_rolls_back_counters_and_does_not_push(rig):
    rig.collector.sample_once()
    callback = Mock()
    rig.collector.on_sample = callback
    db = rig.collector._db
    db.execute("CREATE TRIGGER reject_energy BEFORE INSERT ON energy_samples "
               "BEGIN SELECT RAISE(ABORT, 'isolated failure'); END")
    assert rig.sample(1001.125) == []
    assert rig.collector.status()["last_error"] == "collection_failed"
    callback.assert_not_called()
    assert db.execute("SELECT total_wh FROM energy_baselines WHERE direction='import'").fetchone()[0] == 1000.125
    assert db.execute("SELECT import_total_wh FROM energy_sources").fetchone()[0] == 1000.125
    db.execute("DROP TRIGGER reject_energy")
    assert rig.sample(1002.125)[0]["import_wh"] == 2
    assert callback.call_count == 1


def test_baselines_persist_across_collector_restart_and_retention(rig):
    # A keeper connection makes a named SQLite memory DB persist across reconnects.
    uri = "file:energy-test-persistence?mode=memory&cache=shared"
    with closing(sqlite3.connect(uri, uri=True)):
        def connect(_path, **kwargs):
            return sqlite3.connect(uri, uri=True, **kwargs)

        instance = rig.make(connection_factory=connect)
        instance.sample_once()
        instance.close()
        rig.now += 86400 * 3
        rig.config["energy"]["retention_days"] = 1
        restarted = rig.make(connection_factory=connect)
        assert restarted.summary()["days"] == []
        assert len(restarted.summary()["meters"]) == 1
        assert restarted.summary()["meters"][0]["stale"] is True
        rig.payload = {"emeters": [meter(1002.125, 203.25)]}
        sample = restarted.sample_once()[0]
        assert sample["import_wh"] == 2
        assert sample["export_wh"] == 3
        assert sample["gap"] and sample["crosses_midnight"]
        assert len(restarted.summary()["days"]) == 1


def test_retention_prunes_even_offline_and_bounds_local_calendar_days(rig):
    rig.config["energy"]["retention_days"] = 2
    rig.now = at("2026-09-30T23:59:00+02:00")
    rig.collector.sample_once()
    rig.now = at("2026-10-01T00:00:00+02:00")
    rig.sample(1001.125, seconds=0)
    rig.now = at("2026-10-02T00:01:00+02:00")
    rig.sample(1002.125, seconds=0)
    assert [r["period"] for r in rig.collector.summary()["days"]] == ["2026-10-01", "2026-10-02"]
    assert rig.collector._db.execute("SELECT COUNT(*) FROM energy_baselines").fetchone()[0] == 2
    rig.cache = []
    rig.now = at("2026-10-04T00:00:00+02:00")
    rig.collector.sample_once()
    assert rig.collector._db.execute("SELECT COUNT(*) FROM energy_samples").fetchone()[0] == 0
    assert len(rig.collector.summary()["meters"]) == 1


def test_midnight_month_end_and_outage_attributed_to_interval_end(rig):
    rig.now = at("2026-09-30T23:59:50+02:00")
    rig.collector.sample_once()
    rig.now = at("2026-10-01T00:00:20+02:00")
    sample = rig.sample(1001.125, 202.25, seconds=0)[0]
    assert sample["crosses_midnight"] is True
    assert sample["gap"] is False
    summary = rig.collector.summary()
    assert summary["today"]["import_wh"] == 1
    assert summary["month"]["export_wh"] == 2
    assert summary["months"][0]["import_wh"] is None
    assert summary["months"][1]["import_wh"] == 1
    rig.now = at("2026-10-03T08:00:00+02:00")
    rig.sample(1101.125, 202.25, seconds=0)
    assert rig.collector.summary()["today"]["import_wh"] == 100
    assert "2026-10-02" not in [r["period"] for r in rig.collector.summary()["days"]]
    assert rig.collector.summary()["days"][-1]["gap"] is True
    assert rig.collector.summary()["approximate"] is True


@pytest.mark.parametrize("start,end", [
    ("2026-03-29T01:59:50+01:00", "2026-03-29T03:00:20+02:00"),
    ("2026-10-25T02:59:50+02:00", "2026-10-25T02:00:20+01:00"),
])
def test_dst_uses_elapsed_time_not_wall_clock_and_correct_berlin_day(rig, start, end):
    rig.now = at(start)
    rig.collector.sample_once()
    rig.now = at(end)
    sample = rig.sample(1001.125, seconds=0)[0]
    assert sample["gap"] is False
    assert sample["crosses_midnight"] is False
    summary = rig.collector.summary()
    assert summary["today"]["period"] == end[:10]
    assert summary["today"]["import_wh"] == 1


def test_timezone_configuration_changes_grouping_not_counters(rig):
    rig.now = at("2026-10-01T21:59:30+00:00")
    rig.collector.sample_once()
    rig.now = at("2026-10-01T22:00:00+00:00")
    rig.sample(1001.125, seconds=0)
    assert rig.collector.summary()["today"]["period"] == "2026-10-02"
    rig.config["energy"]["timezone"] = "UTC"
    summary = rig.collector.summary()
    assert summary["today"]["period"] == "2026-10-01"
    assert summary["today"]["import_wh"] == 1
    assert summary["meters"][0]["sampled_at_local"].endswith("+00:00")


def test_summary_excludes_future_samples(rig):
    rig.collector.sample_once()
    previous = rig.now
    rig.sample(1001.125)
    assert rig.collector.summary(now=previous)["today"]["import_wh"] is None


def test_callback_sees_committed_data_and_runs_outside_db_lock(rig):
    received = []

    def callback(sample):
        # A second thread must be able to read while the callback is executing.
        with ThreadPoolExecutor(max_workers=1) as pool:
            summary = pool.submit(rig.collector.summary).result(timeout=2)
        assert summary["meters"][0]["sampled_at"] == sample["sampled_at"]
        received.append(sample.copy())
        sample["source_id"] = "callback mutation"

    rig.collector.on_sample = callback
    sample = rig.collector.sample_once()[0]
    assert sample["channel"] == 0
    assert sample["power_w"] == 12.5
    assert sample["import_kwh"] == 1.000125
    assert sample["export_kwh"] == 0.20025
    assert sample["source_id"] == received[0]["source_id"]
    assert sample["source_id"] != "callback mutation"
    assert rig.sample(seconds=0) == []
    assert len(received) == 1


def test_callback_failure_does_not_undo_commit_or_replay(rig):
    rig.collector.on_sample = Mock(side_effect=RuntimeError("private details"))
    assert len(rig.collector.sample_once()) == 1
    assert rig.collector.status()["last_error"] == "callback_failed"
    assert rig.sample(seconds=0) == []
    assert rig.collector.on_sample.call_count == 1
    assert len(rig.collector.summary()["meters"]) == 1


def test_sqlite_worker_and_concurrent_summary_and_sampling_are_safe(rig):
    rig.collector.sample_once()
    rig.payload = {"emeters": [meter(1001.125, 201.25)]}
    rig.now += 30
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(rig.collector.sample_once) for _ in range(10)]
        futures += [pool.submit(rig.collector.summary) for _ in range(20)]
        results = [future.result(timeout=5) for future in futures]
    assert sum(len(result) for result in results[:10]) == 1
    assert rig.collector.summary()["today"]["import_wh"] == 1


def test_start_stop_idempotent_interruptible_and_restart(rig):
    sampled = threading.Event()
    rig.collector.on_sample = lambda sample: sampled.set()
    assert rig.collector.start() is True
    assert sampled.wait(2)
    assert rig.collector.start() is False
    assert rig.collector.status()["running"] is True
    assert rig.collector.stop(timeout=2) is True
    assert energy.state.stop_event.is_set() is False
    assert rig.collector.status()["running"] is False
    assert rig.collector.stop() is True
    sampled.clear()
    rig.now += 30
    rig.payload = {"emeters": [meter(1001.125)]}
    assert rig.collector.start() is True
    assert sampled.wait(2)
    assert rig.collector.stop(timeout=2) is True
    assert rig.collector.summary()["today"]["import_wh"] == 1


def test_global_shutdown_ends_worker_without_clearing_or_setting_shared_event(rig):
    sampled = threading.Event()
    rig.collector.on_sample = lambda sample: sampled.set()
    assert rig.collector.start() is True
    assert sampled.wait(2)
    energy.state.stop_event.set()
    rig.collector._thread.join(timeout=2)
    assert not rig.collector._thread.is_alive()
    assert energy.state.stop_event.is_set() is True


def test_update_config_enables_disables_and_validates_before_apply(rig):
    sampled = threading.Event()
    rig.collector.on_sample = lambda sample: sampled.set()
    config = {"energy": {"enabled": True, "interval_seconds": 45, "retention_days": 1},
              "shelly": {"username": "new-user", "password": "fixture-secret"}}
    status = rig.collector.update_config(config)
    assert status["enabled"] and status["running"]
    assert status["interval_seconds"] == 45
    assert sampled.wait(2)
    energy.shelly.set_credentials.assert_called_with("new-user", "fixture-secret")
    with pytest.raises(ValueError):
        rig.collector.update_config({"energy": {"enabled": True, "interval_seconds": 1}})
    assert rig.collector.status()["interval_seconds"] == 45
    status = rig.collector.update_config({"energy": {"enabled": False}})
    assert not status["enabled"] and not status["running"]
    assert not energy.state.stop_event.is_set()


def test_update_config_interval_wakes_and_reuses_worker(rig):
    sampled = threading.Event()
    rig.collector.on_sample = lambda sample: sampled.set()
    rig.collector.start()
    assert sampled.wait(2)
    worker = rig.collector._thread
    status = rig.collector.update_config({"energy": {"enabled": True, "interval_seconds": 90}})
    assert status["interval_seconds"] == 90
    assert rig.collector._thread is worker
    assert rig.collector.stop(timeout=2)


def test_stop_timeout_does_not_allow_overlapping_worker(rig):
    entered, release = threading.Event(), threading.Event()

    def blocked_get(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return rig.payload

    rig.get.side_effect = blocked_get
    try:
        assert rig.collector.start() is True
        assert entered.wait(2)
        assert rig.collector.stop(timeout=0) is False
        assert rig.collector.start() is False
    finally:
        release.set()
        assert rig.collector.stop(timeout=2) is True


def test_runtime_disable_prevents_more_io_and_summary_hides_history(rig):
    rig.collector.sample_once()
    calls = rig.get.call_count
    rig.config["energy"]["enabled"] = False
    assert rig.sample() == []
    assert rig.collector.summary()["meters"] == []
    assert rig.get.call_count == calls
    rig.config["energy"]["enabled"] = True
    assert rig.sample(1001.125)[0]["import_wh"] == 1


@pytest.fixture
def energy_app():
    # Own app/base fixtures: no production routes, authentication or nav required.
    template_dir = str(Path(__file__).resolve().parents[1] / "templates")
    app = Flask("energy-isolated-test", template_folder=template_dir)
    app.config["TESTING"] = True
    app.jinja_env.undefined = StrictUndefined
    app.jinja_loader = ChoiceLoader([
        DictLoader({"base.html": "<!doctype html><title>{% block title %}{% endblock %}</title>"
                                 "<main>{% block content %}{% endblock %}</main>"}),
        app.jinja_loader,
    ])
    app.jinja_env.globals["_"] = lambda value: value
    return app


@pytest.mark.parametrize("enabled", [False, True])
def test_template_enabled_disabled_is_read_only_and_escapes_names(rig, energy_app, enabled):
    rig.cache = [device(name='<script>alert("bad")</script>')]
    rig.collector.sample_once()
    rig.sample(1001.125, 200.375)
    rig.config["energy"]["enabled"] = enabled
    with energy_app.test_request_context("/energy"):
        html = render_template("energy.html", energy_status=rig.collector.status(),
                               energy_summary=rig.collector.summary())
    assert "Energy history" in html
    # Local collection toggle is not a device command; rendering still does no I/O.
    assert 'id="energyToggle"' in html
    assert '/control/' not in html and '/relay/' not in html and '/update' not in html
    if enabled:
        assert "Daily totals" in html and "Monthly totals" in html and "Meter details" in html
        assert "interval end" in html and "across midnight" in html
        assert "&lt;script&gt;" in html and '<script>alert("bad")' not in html
        assert "1001.1250 Wh" in html
    else:
        assert "Disabled" in html and "disabled by default" in html
        assert "Daily totals" not in html


def test_template_unknown_values_are_dashes_not_zero(rig, energy_app):
    rig.payload = {"emeters": [{"is_valid": True, "total": 1}]}
    rig.collector.sample_once()
    with energy_app.test_request_context("/energy"):
        html = render_template("energy.html", energy_status=rig.collector.status(),
                               energy_summary=rig.collector.summary())
    assert "– kWh" in html
    assert "0.0000" not in html
    assert "1.0000 Wh" in html
