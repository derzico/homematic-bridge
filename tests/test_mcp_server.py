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


def test_initialize_and_list_tools():
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
