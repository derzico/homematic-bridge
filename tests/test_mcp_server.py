# SPDX-License-Identifier: Apache-2.0
"""MCP-Wrapper: JSON-RPC-Protokoll und Filter, ohne Netzwerk oder Geräte."""

import io
import sys
import json
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "mcp-server"))
import homematic_bridge_mcp.server as mcp  # noqa: E402

INVENTORY = {
    "rooms": [{"id": "r1", "label": "Küche", "devices": ["a"]}],
    "devices": [
        {"id": "a", "room": "Küche", "channels": [{"index": "1", "values": {"on": True}}]},
        {"id": "b", "room": "Bad", "channels": []},
    ],
}
MAPPING = {"count": 2, "variables": [
    {"variable": "hmip_a_ch1_on", "room": "Küche"},
    {"variable": "hmip_b_ch1_windowState", "room": "Bad"},
]}


class Calls(list):
    params = None


@pytest.fixture
def fake_get(monkeypatch):
    calls = Calls()

    def _get(path, params=None):
        calls.append(path)
        calls.params = params
        return json.loads(json.dumps({"/api/inventory": INVENTORY,
                                      "/api/loxone/mapping": MAPPING,
                                      "/api/diagnostics": {"version": "x"}}[path]))

    def _request(path, params=None):
        calls.append(path)
        return b'<VirtualInUdp><VirtualInUdpCmd Check="a@\\v"/></VirtualInUdp>'
    monkeypatch.setattr(mcp, "_get", _get)
    monkeypatch.setattr(mcp, "_request", _request)
    return calls


def rpc(method, params=None, msg_id=1):
    return mcp.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})


def test_initialize_and_list_tools(no_keys):
    init = rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26" and "tools" in init["capabilities"]
    names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
    assert names == {"hmip_summary", "hmip_inventory", "hmip_loxone_mapping",
                     "hmip_loxone_template", "hmip_diagnostics"}


def test_notifications_get_no_response():
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_and_tool():
    assert rpc("resources/list")["error"]["code"] == -32601
    assert rpc("tools/call", {"name": "nope"})["error"]["code"] == -32602


def test_inventory_room_filter(fake_get):
    result = rpc("tools/call", {"name": "hmip_inventory",
                                "arguments": {"room": "küche", "include_values": False}})["result"]
    data = json.loads(result["content"][0]["text"])
    assert [d["id"] for d in data["devices"]] == ["a"]
    assert "values" not in data["devices"][0]["channels"][0]
    assert fake_get == ["/api/inventory"]


def test_mapping_filter(fake_get):
    result = rpc("tools/call", {"name": "hmip_loxone_mapping",
                                "arguments": {"filter": "BAD", "room": "Bad", "limit": 5000,
                                              "sent_only": True}})["result"]
    data = json.loads(result["content"][0]["text"])
    assert data["count"] == 1 and data["variables"][0]["variable"] == "hmip_b_ch1_windowState"
    assert fake_get.params["room"] == "Bad" and fake_get.params["limit"] == mcp.MAX_LIMIT
    assert fake_get.params["sent"] == "1"


def test_mapping_default_page_and_compact(fake_get):
    MAPPING["variables"][0]["extra"] = "x"
    try:
        result = rpc("tools/call", {"name": "hmip_loxone_mapping"})["result"]
    finally:
        MAPPING["variables"][0].pop("extra")
    data = json.loads(result["content"][0]["text"])
    assert fake_get.params["limit"] == mcp.DEFAULT_LIMIT and fake_get.params["offset"] == 0
    assert "extra" not in data["variables"][0]


def test_summary_and_template(fake_get):
    summary = json.loads(rpc("tools/call", {"name": "hmip_summary"})["result"]["content"][0]["text"])
    assert summary["rooms"] == {"Küche": 1} and summary["loxone"]["variables_total"] == 2
    template = json.loads(rpc("tools/call", {"name": "hmip_loxone_template",
                                             "arguments": {"room": "Bad"}})["result"]["content"][0]["text"])
    assert template["commands"] == 1 and template["xml"].startswith("<VirtualInUdp")


def test_inventory_paging_and_type_filter(fake_get):
    data = json.loads(rpc("tools/call", {"name": "hmip_inventory", "arguments": {"limit": 1}})
                      ["result"]["content"][0]["text"])
    assert data["devices_total"] == 2 and len(data["devices"]) == 1


def test_force_utf8_reconfigures_streams(monkeypatch):
    calls = []

    class Stream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(mcp.sys, name, Stream())
    mcp._force_utf8()
    assert calls == [{"encoding": "utf-8"}] * 3


def test_only_get_paths_are_used(fake_get):
    for name in mcp.HANDLERS:
        rpc("tools/call", {"name": name})
    assert all(path.startswith("/api/") for path in fake_get)
    assert "/hmip" not in "".join(fake_get)


def test_http_errors_become_tool_errors(monkeypatch):
    def fail(path, params=None):
        raise urllib.error.HTTPError(path, 401, "unauthorized", None, None)
    monkeypatch.setattr(mcp, "_get", fail)
    result = rpc("tools/call", {"name": "hmip_diagnostics"})["result"]
    assert result["isError"] is True and "401" in result["content"][0]["text"]


def test_api_key_header(monkeypatch):
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["key"] = request.get_header("X-api-key")
        captured["method"] = request.get_method()
        return Response(b"{}")

    monkeypatch.setenv("BRIDGE_URL", "http://bridge.test:8080/")
    monkeypatch.setenv("BRIDGE_API_KEY", "test-key-not-real")
    monkeypatch.setattr(mcp.urllib.request, "urlopen", urlopen)
    assert mcp._get("/api/inventory") == {}
    assert captured == {"url": "http://bridge.test:8080/api/inventory",
                        "key": "test-key-not-real", "method": "GET"}


def test_stdio_loop(monkeypatch):
    stdin = io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\nnot json\n\n'
                       '{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    stdout = io.StringIO()
    monkeypatch.setattr(mcp.sys, "stdin", stdin)
    monkeypatch.setattr(mcp.sys, "stdout", stdout)
    mcp.main()
    lines = stdout.getvalue().splitlines()
    assert [json.loads(line) for line in lines] == [{"jsonrpc": "2.0", "id": 7, "result": {}}]


# ── Steuer-Tools (nur mit Steuer-Key) ─────────────────────────────────────────

CONTROL_NAMES = {"hmip_heating", "hmip_switch", "hmip_dimmer", "hmip_shutter", "hmip_light",
                 "hmip_loxone_resync"}


@pytest.fixture
def no_keys(monkeypatch):
    for name in ("BRIDGE_API_KEY", "BRIDGE_API_KEY_FILE", "BRIDGE_CONTROL_API_KEY",
                 "BRIDGE_CONTROL_API_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def posts(monkeypatch, no_keys):
    monkeypatch.setenv("BRIDGE_CONTROL_API_KEY", "control-test-key-not-real")
    sent = []

    def _post(path, body):
        sent.append((path, body))
        return {"status": "acknowledged"}
    monkeypatch.setattr(mcp, "_post", _post)
    return sent


def call(name, arguments=None):
    return rpc("tools/call", {"name": name, "arguments": arguments or {}})["result"]


def test_control_tools_hidden_and_blocked_without_control_key(no_keys, monkeypatch):
    monkeypatch.setenv("BRIDGE_API_KEY", "read-only-not-real")
    names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
    assert not names & CONTROL_NAMES
    result = call("hmip_switch", {"label": "Licht", "on": True})
    assert result["isError"] is True and "BRIDGE_CONTROL_API_KEY" in result["content"][0]["text"]


def test_control_tools_listed_with_key_and_annotated(posts):
    tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
    assert CONTROL_NAMES <= set(tools)
    assert tools["hmip_heating"]["annotations"]["readOnlyHint"] is False
    assert tools["hmip_summary"]["annotations"]["readOnlyHint"] is True
    assert "hmip_alarm" not in tools                       # Sirene bewusst nicht


def test_heating_by_room_passes_only_allowed_fields(posts):
    result = call("hmip_heating", {"room": "Bad 2OG", "action": "temperature", "temperature": 21.5,
                                   "evil": "x", "mode": "MANUAL"})
    assert "isError" not in result
    assert posts == [("/hmipHeating", {"room": "Bad 2OG", "temperature": 21.5, "action": "temperature"})]


@pytest.mark.parametrize("name,args,expected", [
    ("hmip_heating", {"room": "Bad", "action": "mode", "mode": "MANUAL"},
     ("/hmipHeating", {"room": "Bad", "mode": "MANUAL", "action": "mode"})),
    ("hmip_switch", {"label": "Licht", "room": "Küche", "on": False},
     ("/hmipSwitch", {"label": "Licht", "room": "Küche", "on": False})),
    ("hmip_dimmer", {"device": "D1", "dimLevel": 40, "channelIndex": 1},
     ("/hmipDimmer", {"device": "D1", "channelIndex": 1, "dimLevel": 40})),
    ("hmip_shutter", {"label": "Rollo", "action": "position", "level": 30},
     ("/hmipShutter", {"label": "Rollo", "level": 30, "action": "position"})),
    ("hmip_light", {"groupLabel": "Bad 2OG", "action": "dim", "dimLevel": 20},
     ("/hmipLight", {"groupLabel": "Bad 2OG", "dimLevel": 20, "action": "dim"})),
    ("hmip_loxone_resync", {}, ("/api/loxone/resync", {})),
])
def test_control_tools_map_to_bridge_endpoints(posts, name, args, expected):
    assert "isError" not in call(name, args)
    assert posts == [expected]


@pytest.mark.parametrize("name,args", [
    ("hmip_heating", {"action": "temperature", "temperature": 21}),       # kein Ziel
    ("hmip_heating", {"room": "Bad", "action": "valve"}),
    ("hmip_switch", {"label": "Licht"}),                                   # on fehlt
    ("hmip_switch", {"on": True}),                                         # kein Ziel
    ("hmip_shutter", {"label": "Rollo", "action": "open"}),
    ("hmip_light", {"label": "x", "action": "party"}),
])
def test_invalid_control_input_is_rejected_locally(posts, name, args):
    result = call(name, args)
    assert result["isError"] is True and posts == []


def test_bridge_error_details_are_returned(no_keys, monkeypatch):
    monkeypatch.setenv("BRIDGE_CONTROL_API_KEY", "control-test-key-not-real")
    body = json.dumps({"error": "mehrdeutig", "candidates": [{"id": "A"}, {"id": "B"}]}).encode()

    def fail(path, payload):
        raise urllib.error.HTTPError(path, 409, "conflict", None, io.BytesIO(body))
    monkeypatch.setattr(mcp, "_post", fail)
    text = call("hmip_switch", {"label": "Licht", "on": True})["content"][0]["text"]
    assert "409" in text and '"candidates"' in text


def test_post_uses_control_key_and_read_uses_read_key(no_keys, monkeypatch):
    monkeypatch.setenv("BRIDGE_URL", "http://bridge.test:8080")
    monkeypatch.setenv("BRIDGE_API_KEY", "read-only-not-real")
    monkeypatch.setenv("BRIDGE_CONTROL_API_KEY", "control-test-key-not-real")
    seen = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        seen.append((request.get_method(), request.full_url, request.get_header("X-api-key"),
                     request.data))
        return Response(b"{}")
    monkeypatch.setattr(mcp.urllib.request, "urlopen", urlopen)
    mcp._post("/hmipHeating", {"room": "Bad", "action": "boost", "boost": True})
    mcp._get("/api/diagnostics")
    assert seen[0][:3] == ("POST", "http://bridge.test:8080/hmipHeating", "control-test-key-not-real")
    assert json.loads(seen[0][3]) == {"room": "Bad", "action": "boost", "boost": True}
    assert seen[1][:3] == ("GET", "http://bridge.test:8080/api/diagnostics", "read-only-not-real")


def test_control_key_from_file(no_keys, monkeypatch, tmp_path):
    key_file = tmp_path / "api_key.txt"
    key_file.write_text("file-control-key-not-real\n", encoding="utf-8")
    monkeypatch.setenv("BRIDGE_CONTROL_API_KEY_FILE", str(key_file))
    assert mcp._control_key() == "file-control-key-not-real" and mcp.control_enabled()
    monkeypatch.setenv("BRIDGE_CONTROL_API_KEY_FILE", str(tmp_path / "missing.txt"))
    assert mcp.control_enabled() is False
