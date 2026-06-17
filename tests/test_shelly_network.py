# SPDX-License-Identifier: Apache-2.0
# tests/test_shelly_network.py - Isolierte Tests der Shelly-Netzwerkpfade

from unittest.mock import MagicMock, patch

import pytest
import requests

import app.adapters.shelly_adapter as shelly


@pytest.fixture(autouse=True)
def _reset_shelly_globals():
    shelly.set_credentials(None, None)
    with shelly._scan_lock:
        shelly._scan_running = False
        shelly._scan_error = None
        shelly._scan_started = None
    yield
    shelly.set_credentials(None, None)


def _response(status: int, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload
    return response


def test_get_retries_without_credentials_after_unauthorized():
    shelly.set_credentials("admin", "secret")
    unauthorized = _response(401)
    success = _response(200, {"app": "Plus1PM"})

    with patch("app.adapters.shelly_adapter.requests.get", side_effect=[unauthorized, success]) as get:
        result = shelly._get("192.168.1.20", "/shelly", timeout=1.5)

    assert result == {"app": "Plus1PM"}
    assert get.call_count == 2
    assert get.call_args_list[0].kwargs["auth"] == ("admin", "secret")
    assert "auth" not in get.call_args_list[1].kwargs


def test_get_returns_none_on_network_timeout():
    with patch(
        "app.adapters.shelly_adapter.requests.get",
        side_effect=requests.Timeout("offline"),
    ):
        assert shelly._get("192.168.1.21", "/shelly", timeout=0.1) is None


def test_detect_falls_back_from_gen2_to_gen1():
    with patch(
        "app.adapters.shelly_adapter._get",
        side_effect=[None, {"type": "SHSW-1", "mac": "AABBCCDDEEFF"}],
    ) as get:
        detected = shelly._detect("192.168.1.22", timeout=1.0)

    assert detected == {
        "gen": 1,
        "info": {"type": "SHSW-1", "mac": "AABBCCDDEEFF"},
    }
    assert [call.args[1] for call in get.call_args_list] == [
        "/rpc/Shelly.GetDeviceInfo",
        "/shelly",
    ]


def test_scan_rejects_large_network_before_starting_workers():
    with patch("app.adapters.shelly_adapter.ThreadPoolExecutor") as executor, \
         patch("app.adapters.shelly_adapter.save_cache") as save_cache:
        shelly._run_scan("10.0.0.0/8", timeout_sec=0.1, include_mdns=False)

    executor.assert_not_called()
    save_cache.assert_not_called()
    assert "maximal 4096" in shelly.scan_status()["error"]


def test_scan_saves_discovered_and_marks_missing_devices_offline():
    found = {
        "ip": "192.168.1.1",
        "gen": 2,
        "id": "shelly-plus",
        "name": "Kitchen",
    }
    old = {
        "ip": "192.168.1.99",
        "gen": 1,
        "id": "shelly-old",
        "name": "Garage",
        "online": True,
    }

    def probe(ip: str, _timeout: float):
        return found.copy() if ip == "192.168.1.1" else None

    with patch("app.adapters.shelly_adapter._probe_ip", side_effect=probe), \
         patch("app.adapters.shelly_adapter.load_cached", return_value=[old]), \
         patch("app.adapters.shelly_adapter.save_cache") as save_cache:
        shelly._run_scan("192.168.1.0/30", timeout_sec=0.1, include_mdns=False)

    saved = save_cache.call_args.args[0]
    assert [device["ip"] for device in saved] == ["192.168.1.1", "192.168.1.99"]
    assert saved[0]["online"] is True
    assert saved[1]["online"] is False


@pytest.mark.parametrize(
    ("generation", "expected_path", "expected_kwargs"),
    [
        (1, "/relay/2", {"data": {"turn": "off"}}),
        (2, "/rpc/Switch.Set", {"json": {"id": 2, "on": False}}),
    ],
)
def test_set_relay_uses_generation_specific_endpoint(generation, expected_path, expected_kwargs):
    with patch("app.adapters.shelly_adapter._post", return_value=_response(200)) as post:
        assert shelly.set_relay("192.168.1.23", generation, channel=2, on=False) is True

    post.assert_called_once_with(
        "192.168.1.23",
        expected_path,
        timeout=5,
        **expected_kwargs,
    )
