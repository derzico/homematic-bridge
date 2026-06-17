# SPDX-License-Identifier: Apache-2.0
# tests/test_routes_security.py - Regressionstests fuer Web-Sicherheitskontrollen

from unittest.mock import patch

from flask import Flask

import app.state as state
from app import i18n
from app.routes import bp


def _authenticate(client, csrf_token: str = "csrf-test-token") -> None:
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["csrf_token"] = csrf_token


class TestWebPostCsrf:
    def test_alarm_post_requires_csrf(self, flask_client):
        state.REQUIRE_API_KEY = True
        _authenticate(flask_client)

        resp = flask_client.post("/alarm/test-smoke")

        assert resp.status_code == 403

    def test_alarm_post_accepts_valid_csrf(self, flask_client):
        state.REQUIRE_API_KEY = True
        _authenticate(flask_client)
        state.conn = None

        resp = flask_client.post("/alarm/test-smoke", headers={"X-CSRF-Token": "csrf-test-token"})

        assert resp.status_code == 503

    def test_shelly_actions_require_csrf(self, flask_client):
        state.REQUIRE_API_KEY = True
        state.config = {"shelly": {"enabled": False}}
        _authenticate(flask_client)

        endpoints = [
            ("/shelly/scan", None),
            ("/shelly/refresh-status", None),
            ("/shelly/check-updates", None),
            ("/shelly/192.168.1.2/update", None),
            ("/shelly/192.168.1.2/relay/0", {"on": True}),
        ]

        for path, body in endpoints:
            resp = flask_client.post(path, json=body) if body is not None else flask_client.post(path)
            assert resp.status_code == 403, path


class TestStateChangingRoutes:
    def test_switch_get_is_not_allowed_even_from_loopback(self, flask_client):
        state.REQUIRE_API_KEY = False

        resp = flask_client.get(
            "/hmipSwitch?device=test-device&on=true",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        assert resp.status_code == 405


class TestShellyWebUiIsolation:
    def test_redirects_to_device_origin_instead_of_proxying_html(self, flask_client):
        state.REQUIRE_API_KEY = True
        _authenticate(flask_client)

        with patch("app.shelly_proxy.shelly_mod.load_cached", return_value=[{"ip": "192.168.1.2"}]):
            resp = flask_client.get("/shelly/192.168.1.2/webui/settings?tab=wifi")

        assert resp.status_code == 302
        assert resp.headers["Location"] == "http://192.168.1.2/settings?tab=wifi"

    def test_rejects_uncached_redirect_targets(self, flask_client):
        state.REQUIRE_API_KEY = True
        _authenticate(flask_client)

        with patch("app.shelly_proxy.shelly_mod.load_cached", return_value=[]):
            resp = flask_client.get("/shelly/192.168.1.2/webui/")

        assert resp.status_code == 404


class TestSafeRedirects:
    def test_login_rejects_external_next_url(self, flask_client):
        state.REQUIRE_API_KEY = True
        state.API_KEY = "secret"
        with flask_client.session_transaction() as sess:
            sess["csrf_token"] = "csrf-test-token"

        resp = flask_client.post(
            "/login?next=https://example.invalid/phish",
            data={"password": "secret", "csrf_token": "csrf-test-token"},
        )

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

    def test_login_allows_internal_next_url(self, flask_client):
        state.REQUIRE_API_KEY = True
        state.API_KEY = "secret"
        with flask_client.session_transaction() as sess:
            sess["csrf_token"] = "csrf-test-token"

        resp = flask_client.post(
            "/login?next=/devices/status",
            data={"password": "secret", "csrf_token": "csrf-test-token"},
        )

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/devices/status"

    def test_language_rejects_external_next_url(self, flask_client):
        resp = flask_client.get("/lang/de?next=https://example.invalid/phish")

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"


class TestTemplateRendering:
    def test_dashboard_renders_js_i18n_strings(self, tmp_path):
        app = Flask(__name__, template_folder="../templates")
        app.secret_key = b"test-secret-key"
        app.config["TESTING"] = True
        app.register_blueprint(bp)
        i18n.init_app(app)
        snapshot_path = tmp_path / "system_state.json"
        snapshot_path.write_text(
            '{"body":{"body":{"home":{"weather":{},"functionalHomes":{}},"groups":{},"devices":{}}}}',
            encoding="utf-8",
        )
        state.REQUIRE_API_KEY = False
        state.config_internal = {"system_state_path": str(snapshot_path)}

        # Force English so the assertion can match the source string verbatim.
        resp = app.test_client().get("/", headers={"Accept-Language": "en"})

        assert resp.status_code == 200
        assert b"test_confirm" in resp.data
        assert b"{s} seconds" in resp.data
