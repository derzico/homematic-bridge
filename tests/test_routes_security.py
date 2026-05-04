# SPDX-License-Identifier: Apache-2.0
# tests/test_routes_security.py - Regressionstests fuer Web-Sicherheitskontrollen

import app.state as state


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
