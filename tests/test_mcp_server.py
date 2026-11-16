# SPDX-License-Identifier: Apache-2.0
"""MCP-Wrapper: JSON-RPC-Protokoll und Filter, ohne Netzwerk oder Geräte."""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "ops/mcp_server.py"
spec = importlib.util.spec_from_file_location("bridge_mcp", SOURCE)
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)

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


@pytest.fixture
def fake_get(monkeypatch):
    calls = []

    def _get(path):
        calls.append(path)
        return json.loads(json.dumps({"/api/inventory": INVENTORY,
                                      "/api/loxone/mapping": MAPPING,
                                      "/api/diagnostics": {"version": "x"}}[path]))
    monkeypatch.setattr(mcp, "_get", _get)
    return calls


def rpc(method, params=None, msg_id=1):
    return mcp.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})


def test_initialize_and_list_tools():
    init = rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26" and "tools" in init["capabilities"]
    names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
    assert names == {"hmip_inventory", "hmip_loxone_mapping", "hmip_diagnostics"}


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
    result = rpc("tools/call", {"name": "hmip_loxone_mapping", "arguments": {"filter": "BAD"}})["result"]
    data = json.loads(result["content"][0]["text"])
    assert data["count"] == 1 and data["variables"][0]["variable"] == "hmip_b_ch1_windowState"


def test_only_get_paths_are_used(fake_get):
    for name in mcp.HANDLERS:
        rpc("tools/call", {"name": name})
    assert all(path.startswith("/api/") for path in fake_get)
    assert "/hmip" not in "".join(fake_get)


def test_http_errors_become_tool_errors(monkeypatch):
    def fail(path):
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
