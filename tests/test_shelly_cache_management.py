# SPDX-License-Identifier: Apache-2.0
# tests/test_shelly_cache_management.py – Löschung, Neu-Scan und parallele Refreshes

from unittest.mock import patch

import pytest
from flask import Flask

import app.adapters.shelly_adapter as shelly
import app.state as state
from app import i18n
from app.routes import bp


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(shelly, "_SHELLY_CACHE", str(tmp_path / "shelly.json"))
    monkeypatch.setattr(shelly, "_scan_running", False)
    monkeypatch.setattr(shelly, "_scan_error", None)
    monkeypatch.setattr(shelly, "_scan_started", None)
    monkeypatch.setattr(shelly, "_credentials", None)


def device(ip="192.168.1.1", name="Current"):
    return {"ip": ip, "gen": 1, "name": name, "online": True, "channels": {"0": {"on": False}}}


def authenticate(client):
    state.REQUIRE_API_KEY = True
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["csrf_token"] = "cache-test-token"
    return {"X-CSRF-Token": "cache-test-token"}


def test_delete_removes_only_selected_device_and_persists():
    shelly.save_cache([device(), device("192.168.1.99", "Retired")])
    assert shelly.delete_cached_device("192.168.1.99") is True
    assert shelly.load_cached() == [device()]
    assert shelly.delete_cached_device("192.168.1.99") is False


def test_delete_during_scan_is_rejected_without_changing_cache():
    shelly.save_cache([device()])
    shelly._scan_running = True
    with pytest.raises(shelly.ScanInProgressError):
        shelly.delete_cached_device("192.168.1.1")
    assert shelly.load_cached() == [device()]


@pytest.mark.parametrize("replace_cache", [False, True])
def test_rescan_retains_missing_devices_only_in_normal_mode(replace_cache):
    shelly.save_cache([device("192.168.1.99", "Retired")])
    with patch.object(shelly, "_probe_ip", side_effect=lambda ip, _: device(ip) if ip == "192.168.1.1" else None):
        shelly._run_scan("192.168.1.0/30", 0.1, False, replace_cache)
    saved = shelly.load_cached()
    assert saved[0] == device()
    if replace_cache:
        assert len(saved) == 1
    else:
        assert saved[1]["ip"] == "192.168.1.99"
        assert saved[1]["online"] is False


def test_empty_successful_rescan_clears_retired_devices():
    shelly.save_cache([device()])
    with patch.object(shelly, "_probe_ip", return_value=None):
        shelly._run_scan("192.168.1.0/30", 0.1, False, True)
    assert shelly.load_cached() == []
    assert shelly.scan_status()["error"] is None


@pytest.mark.parametrize("subnet", ["invalid", "10.0.0.0/8"])
def test_invalid_rescan_preserves_cache_and_does_not_probe(subnet):
    shelly.save_cache([device()])
    with patch.object(shelly, "_mdns_scan") as mdns, patch.object(shelly, "_probe_ip") as probe:
        shelly._run_scan(subnet, 0.1, True, True)
    assert shelly.load_cached() == [device()]
    assert shelly.scan_status()["error"]
    mdns.assert_not_called()
    probe.assert_not_called()


def test_failed_rescan_preserves_original_cache():
    shelly.save_cache([device()])
    with patch.object(shelly, "_probe_ip", side_effect=RuntimeError("simulated scanner failure")):
        shelly._run_scan("192.168.1.0/30", 0.1, False, True)
    assert shelly.load_cached() == [device()]
    assert "simulated scanner failure" in shelly.scan_status()["error"]
    assert shelly.scan_status()["running"] is False


@pytest.mark.parametrize("refresh_all", [False, True])
def test_delete_survives_in_flight_status_refresh(refresh_all):
    shelly.save_cache([device()])
    def status(ip, timeout):
        assert shelly.delete_cached_device(ip)
        return {"channels": {"0": {"on": True}}, "emeters": {}, "rssi": -50}
    with patch.object(shelly, "_get_status_gen1", side_effect=status):
        if refresh_all:
            assert shelly.refresh_all_devices() == 0
        else:
            assert shelly.refresh_device("192.168.1.1", 1) is None
    assert shelly.load_cached() == []


def test_refresh_does_not_discard_concurrent_changes_to_another_device():
    shelly.save_cache([device(), device("192.168.1.2", "Second")])
    def status(ip, timeout):
        latest = shelly.load_cached()
        latest[1]["name"] = "Changed during request"
        shelly.save_cache(latest)
        return {"channels": {"0": {"on": True}}, "emeters": {}, "rssi": -50}
    with patch.object(shelly, "_get_status_gen1", side_effect=status):
        shelly.refresh_device("192.168.1.1", 1)
    cached = shelly.load_cached()
    assert cached[0]["channels"]["0"]["on"] is True
    assert cached[1]["name"] == "Changed during request"


def test_refresh_does_not_restore_devices_removed_by_full_rescan():
    shelly.save_cache([device()])
    def status(ip, timeout):
        with patch.object(shelly, "_probe_ip", return_value=None):
            shelly._run_scan("192.168.1.0/30", 0.1, False, True)
        return {"channels": {}, "emeters": {}, "rssi": None}
    with patch.object(shelly, "_get_status_gen1", side_effect=status):
        shelly.refresh_all_devices()
    assert shelly.load_cached() == []


def test_scan_worker_receives_replace_flag_and_parallel_scan_is_rejected():
    with patch.object(shelly.threading, "Thread") as thread:
        assert shelly.start_scan("192.168.1.0/24", replace_cache=True)
        assert not shelly.start_scan("192.168.1.0/24")
    assert thread.call_args.kwargs["args"] == ("192.168.1.0/24", 1.5, True, True)
    thread.return_value.start.assert_called_once()


def test_delete_route_removes_cached_device(flask_client):
    headers = authenticate(flask_client)
    shelly.save_cache([device()])
    response = flask_client.post("/shelly/192.168.1.1/delete", headers=headers)
    assert response.status_code == 200
    assert response.json["success"] is True
    assert shelly.load_cached() == []
    assert flask_client.post("/shelly/192.168.1.1/delete", headers=headers).status_code == 404


def test_delete_route_requires_login_and_post(flask_client):
    state.REQUIRE_API_KEY = True
    shelly.save_cache([device()])
    assert flask_client.post("/shelly/192.168.1.1/delete").status_code == 302
    authenticate(flask_client)
    assert flask_client.get("/shelly/192.168.1.1/delete").status_code == 405
    assert shelly.load_cached() == [device()]


def test_delete_route_rejects_scan_conflict(flask_client):
    headers = authenticate(flask_client)
    shelly.save_cache([device()])
    shelly._scan_running = True
    assert flask_client.post("/shelly/192.168.1.1/delete", headers=headers).status_code == 409
    assert shelly.load_cached() == [device()]


@pytest.mark.parametrize("ip", ["hostname", "999.1.1.1", "::1"])
def test_delete_route_rejects_invalid_ip(flask_client, ip):
    headers = authenticate(flask_client)
    assert flask_client.post(f"/shelly/{ip}/delete", headers=headers).status_code == 400


@pytest.mark.parametrize("replace_cache", [False, True])
def test_scan_route_selects_requested_mode(flask_client, replace_cache):
    headers = authenticate(flask_client)
    state.config = {"shelly": {"enabled": True, "subnet": "192.168.1.0/24"}}
    with patch.object(shelly, "start_scan", return_value=True) as start:
        response = flask_client.post("/shelly/scan", json={"replace_cache": replace_cache}, headers=headers)
    assert response.status_code == 202
    assert response.json["status"] == "started"
    start.assert_called_once_with("192.168.1.0/24", timeout_sec=1.5, replace_cache=replace_cache)


def test_legacy_scan_without_body_keeps_normal_mode(flask_client):
    headers = authenticate(flask_client)
    state.config = {"shelly": {"enabled": True, "subnet": "192.168.1.0/24"}}
    with patch.object(shelly, "start_scan", return_value=True) as start:
        response = flask_client.post("/shelly/scan", headers=headers)
    assert response.status_code == 202
    assert start.call_args.kwargs["replace_cache"] is False


@pytest.mark.parametrize("body", [{"replace_cache": "true"}, {"replace_cache": 1}, ["invalid"]])
def test_scan_route_rejects_invalid_replacement_request(flask_client, body):
    headers = authenticate(flask_client)
    state.config = {"shelly": {"enabled": True, "subnet": "192.168.1.0/24"}}
    with patch.object(shelly, "start_scan") as start:
        response = flask_client.post("/shelly/scan", json=body, headers=headers)
    assert response.status_code == 400
    start.assert_not_called()


@pytest.mark.parametrize("language,delete_label,rescan_label", [
    ("de", "Löschen", "Geräte vollständig neu einlesen"),
    ("en", "Delete", "Rebuild device list"),
])
def test_shelly_page_renders_translated_cache_actions(language, delete_label, rescan_label):
    app = Flask(__name__, template_folder="../templates")
    app.secret_key = "cache-test-secret"
    app.register_blueprint(bp)
    i18n.init_app(app)
    state.REQUIRE_API_KEY = False
    state.config = {"shelly": {"enabled": True, "subnet": "192.168.1.0/24"}}
    shelly.save_cache([device("192.168.1.99", 'Retired <device> "quoted"')])
    response = app.test_client().get("/shelly", headers={"Accept-Language": language})
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert delete_label in page
    assert rescan_label in page
    assert 'data-ip="192.168.1.99"' in page
    assert 'data-name="Retired &lt;device&gt; &#34;quoted&#34;"' in page
