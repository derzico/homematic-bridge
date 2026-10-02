import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

import app.state as state
import app.runtime_services as services
from config.loader import validate_config


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    state.config = {"homematic_hcu": "hcu.test", "homematic_token": "test", "plugin_id": "private.test",
                    "loxone": {"miniserver_ip": "192.0.2.1", "udp_port": 7777}}
    state.config_internal = {"system_state_path": str(tmp_path / "snapshot.json")}
    state.WS_READY = True
    monkeypatch.setattr(services, "ENERGY_SETTINGS_PATH", str(tmp_path / "energy.yaml"))
    monkeypatch.setattr(services, "EnergyCollector", MagicMock())
    return services.RuntimeServices()


def snapshot():
    return {"body": {"body": {"devices": {"private-d": {}}, "groups": {}, "home": {}}}}


def test_runtime_snapshot_sends_only_state(runtime):
    with patch.object(services, "push_system_state") as push:
        assert runtime.sync_snapshot(snapshot()) > 0
    push.assert_called_once_with("192.0.2.1", 7777, snapshot())
    assert runtime.last_sync_error is None


def test_runtime_does_not_sync_before_ready(runtime):
    state.WS_READY = False
    with patch.object(services, "push_system_state") as push, pytest.raises(ValueError):
        runtime.sync_snapshot(snapshot())
    push.assert_not_called()


def test_runtime_does_not_sync_without_configured_loxone(runtime):
    state.config["loxone"] = {}
    with patch.object(services, "push_system_state") as push, pytest.raises(ValueError):
        runtime.sync_snapshot(snapshot())
    push.assert_not_called()


def test_runtime_serializes_full_sync(runtime):
    runtime._sync_lock.acquire()
    try:
        with pytest.raises(ValueError, match="bereits"):
            runtime.sync_snapshot(snapshot())
    finally:
        runtime._sync_lock.release()


def test_runtime_loads_snapshot_at_sync_time(runtime):
    with open(state.config_internal["system_state_path"], "w") as stream:
        json.dump(snapshot(), stream)
    with patch.object(services, "push_system_state") as push:
        runtime.sync_snapshot()
    push.assert_called_once_with("192.0.2.1", 7777, snapshot())


def test_runtime_persists_only_energy_settings(runtime):
    runtime.set_energy_enabled(True)
    config = yaml.safe_load(open(services.ENERGY_SETTINGS_PATH))
    assert config == {"enabled": True}
    assert "homematic_token" not in config
    assert runtime.effective_config()["energy"]["enabled"] is True
    runtime.energy.update_config.assert_called_once()


def test_runtime_toggle_is_explicit_boolean(runtime):
    with pytest.raises(ValueError):
        runtime.set_energy_enabled("true")


@pytest.mark.parametrize("contents", ["enabled: not-a-boolean", "[broken", "enabled: true\ninterval_seconds: 1"])
def test_invalid_optional_energy_settings_do_not_break_runtime(runtime, contents):
    from pathlib import Path
    Path(services.ENERGY_SETTINGS_PATH).write_text(contents)
    assert "energy" not in runtime.effective_config()


def test_runtime_stop_does_not_stop_global_bridge(runtime):
    runtime.stop()
    runtime.energy.stop.assert_called_once()
    assert not state.stop_event.is_set()


def test_runtime_energy_forwarding_does_not_disclose_credentials(runtime):
    sample = {"source_id": "shelly:id:private:emeter:0", "channel": 0, "power_w": 42}
    with patch.object(services, "push_shelly_energy") as push:
        runtime.forward_energy(sample)
    push.assert_called_once_with("192.0.2.1", 7777, sample)


@pytest.mark.parametrize("section", [
    {"energy": []}, {"energy": {"enabled": "true"}}, {"energy": {"interval_seconds": True}},
    {"energy": {"interval_seconds": 1}}, {"energy": {"retention_days": 731}},
    {"energy": {"timezone": "not/a/zone"}}, {"loxone": {"resync_interval_seconds": 1}},
    {"loxone": {"resync_interval_seconds": True}},
])
def test_background_options_validated(section):
    assert validate_config({"homematic_hcu": "test", "homematic_token": "test", "plugin_id": "private", **section})


def test_energy_route_requires_session_and_csrf(flask_client):
    state.REQUIRE_API_KEY = True
    state.runtime_services = MagicMock()
    assert flask_client.post("/energy/settings", json={"enabled": True}).status_code == 302
    with flask_client.session_transaction() as session:
        session["authenticated"] = True
    assert flask_client.post("/energy/settings", json={"enabled": True}).status_code == 403
    state.runtime_services.set_energy_enabled.assert_not_called()


def test_resync_route_requires_key(flask_client):
    state.REQUIRE_API_KEY = True
    state.API_KEY = "private-test-key"
    state.runtime_services = MagicMock()
    assert flask_client.post("/api/loxone/resync").status_code == 401
    state.runtime_services.sync_snapshot.assert_not_called()


def test_resync_api_does_not_claim_delivery(flask_client):
    state.REQUIRE_API_KEY = True
    state.API_KEY = "private-test-key"
    state.runtime_services = MagicMock()
    state.runtime_services.sync_snapshot.return_value = 123
    response = flask_client.post("/api/loxone/resync", headers={"X-API-Key": "private-test-key"})
    assert response.status_code == 200
    assert "not acknowledged" in response.json["delivery"]


def test_diagnostics_excludes_private_configuration(flask_client):
    state.REQUIRE_API_KEY = True
    state.API_KEY = "private-key-must-not-appear"
    state.config = {"homematic_token": "private-token-must-not-appear", "shelly": {"password": "private-password-must-not-appear"}}
    state.config_internal = {"system_state_path": "missing"}
    state.runtime_services = None
    response = flask_client.get("/api/diagnostics", headers={"X-API-Key": state.API_KEY})
    assert response.status_code == 200
    for secret in (state.API_KEY, state.config["homematic_token"], state.config["shelly"]["password"]):
        assert secret not in response.get_data(as_text=True)


def test_initial_snapshot_is_forwarded_once_per_connection(monkeypatch):
    from app.adapters import hmip_websocket as ws
    state.config = {"homematic_hcu": "test", "homematic_token": "test", "plugin_id": "private"}
    state.LOXONE_HOST = "192.0.2.1"
    state.runtime_services = MagicMock()
    connection = MagicMock()
    calls = [0]
    def receive():
        calls[0] += 1
        with state.pending_lock:
            rid = next(iter(state.pending), "unrelated")
        if calls[0] == 2:
            state.stop_event.set()
        return json.dumps({"id": rid, "type": "HMIP_SYSTEM_RESPONSE", "body": {"code": 200, "body": {"devices": {}}}})
    connection.recv.side_effect = receive
    with patch.object(ws.websocket, "create_connection", return_value=connection), patch.object(ws, "save_system_state", return_value=True):
        ws.ws_loop()
    state.runtime_services.sync_snapshot.assert_called_once()
