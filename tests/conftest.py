# SPDX-License-Identifier: Apache-2.0
# tests/conftest.py – Shared fixtures for pytest

import pytest
from flask import Flask

import app.state as state
from app.auth import require_api_key, require_web_auth
from app.routes import bp as routes_bp


@pytest.fixture()
def tmp_snapshot(tmp_path):
    """Provides a temporary snapshot file path."""
    return str(tmp_path / "test_state.json")


@pytest.fixture()
def flask_app():
    """Minimal Flask app with routes blueprint for testing."""
    test_app = Flask(__name__)
    test_app.secret_key = b"test-secret-key-for-testing-only"
    test_app.config["TESTING"] = True
    test_app.register_blueprint(routes_bp)
    return test_app


@pytest.fixture()
def flask_client(flask_app):
    """Flask test client."""
    return flask_app.test_client()


@pytest.fixture()
def auth_app():
    """Minimal Flask app with dummy routes for auth decorator testing."""
    test_app = Flask(__name__)
    test_app.secret_key = b"test-secret-key"
    test_app.config["TESTING"] = True

    @test_app.route("/api-test")
    @require_api_key
    def api_test():
        return "ok", 200

    @test_app.route("/web-test")
    @require_web_auth
    def web_test():
        return "ok", 200

    return test_app


@pytest.fixture(autouse=True)
def _fresh_command_guard():
    """Funk-Schutz ist Modulzustand: je Test frisch und ohne echtes Warten."""
    import app.command_guard as command_guard
    saved = command_guard.guard
    command_guard.guard = command_guard.CommandGuard(sleep=lambda _seconds: None)
    yield
    command_guard.guard = saved


@pytest.fixture(autouse=True)
def _reset_loxone_options():
    """Loxone-Optionen und Topologie sind Modulzustand: nach jedem Test zurücksetzen."""
    from app import loxone_udp
    loxone_udp.configure(None)
    loxone_udp.update_topology({"devices": {}, "groups": {}})
    loxone_udp._last_aggregates = None
    yield
    loxone_udp.configure(None)
    loxone_udp.update_topology({"devices": {}, "groups": {}})
    loxone_udp._last_aggregates = None


@pytest.fixture(autouse=True)
def _reset_state():
    """Save and restore state module globals around each test."""
    state.stop_event.clear()
    saved = {
        "API_KEY": state.API_KEY,
        "REQUIRE_API_KEY": state.REQUIRE_API_KEY,
        "API_KEY_FILE": state.API_KEY_FILE,
        "API_KEY_READONLY": state.API_KEY_READONLY,
        "API_KEY_READONLY_FILE": state.API_KEY_READONLY_FILE,
        "CONTROL_ALLOWED_NETS": state.CONTROL_ALLOWED_NETS,
        "config": state.config,
        "config_internal": state.config_internal,
        "pending": state.pending.copy(),
        "conn": state.conn,
        "ACK_TIMEOUT_SEC": state.ACK_TIMEOUT_SEC,
        "WS_CONNECTED_AT": state.WS_CONNECTED_AT,
        "WS_LAST_MESSAGE_AT": state.WS_LAST_MESSAGE_AT,
        "WS_LAST_ERROR": state.WS_LAST_ERROR,
        "WS_RECONNECTS": state.WS_RECONNECTS,
        "WS_READY": state.WS_READY,
        "WS_PLUGIN_ID": state.WS_PLUGIN_ID,
        "adapter_registry": state.adapter_registry,
        "runtime_services": state.runtime_services,
        "LOXONE_HOST": state.LOXONE_HOST,
        "LOXONE_UDP_PORT": state.LOXONE_UDP_PORT,
    }
    yield
    state.API_KEY = saved["API_KEY"]
    state.REQUIRE_API_KEY = saved["REQUIRE_API_KEY"]
    state.API_KEY_FILE = saved["API_KEY_FILE"]
    state.API_KEY_READONLY = saved["API_KEY_READONLY"]
    state.API_KEY_READONLY_FILE = saved["API_KEY_READONLY_FILE"]
    state.CONTROL_ALLOWED_NETS = saved["CONTROL_ALLOWED_NETS"]
    state.config = saved["config"]
    state.config_internal = saved["config_internal"]
    state.conn = saved["conn"]
    state.ACK_TIMEOUT_SEC = saved["ACK_TIMEOUT_SEC"]
    state.WS_CONNECTED_AT = saved["WS_CONNECTED_AT"]
    state.WS_LAST_MESSAGE_AT = saved["WS_LAST_MESSAGE_AT"]
    state.WS_LAST_ERROR = saved["WS_LAST_ERROR"]
    state.WS_RECONNECTS = saved["WS_RECONNECTS"]
    state.WS_READY = saved["WS_READY"]
    state.WS_PLUGIN_ID = saved["WS_PLUGIN_ID"]
    state.adapter_registry = saved["adapter_registry"]
    state.runtime_services = saved["runtime_services"]
    state.LOXONE_HOST = saved["LOXONE_HOST"]
    state.LOXONE_UDP_PORT = saved["LOXONE_UDP_PORT"]
    state.stop_event.clear()
    with state.pending_lock:
        state.pending.clear()
        state.pending.update(saved["pending"])
