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
def _reset_state():
    """Save and restore state module globals around each test."""
    saved = {
        "API_KEY": state.API_KEY,
        "REQUIRE_API_KEY": state.REQUIRE_API_KEY,
        "API_KEY_FILE": state.API_KEY_FILE,
        "config": state.config,
        "config_internal": state.config_internal,
        "pending": state.pending.copy(),
    }
    yield
    state.API_KEY = saved["API_KEY"]
    state.REQUIRE_API_KEY = saved["REQUIRE_API_KEY"]
    state.API_KEY_FILE = saved["API_KEY_FILE"]
    state.config = saved["config"]
    state.config_internal = saved["config_internal"]
    with state.pending_lock:
        state.pending.clear()
        state.pending.update(saved["pending"])
