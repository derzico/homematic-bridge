# SPDX-License-Identifier: Apache-2.0
# tests/test_csrf.py – Tests for CSRF protection

from flask import Flask, session

from app.auth import generate_csrf_token, require_csrf, require_web_auth, validate_csrf_token
import app.state as state


def _make_app():
    """App with a CSRF-protected POST route."""
    app = Flask(__name__)
    app.secret_key = b"test-key"
    app.config["TESTING"] = True

    @app.route("/form", methods=["GET", "POST"])
    @require_csrf
    def form_view():
        if __import__("flask").request.method == "GET":
            return f"token={generate_csrf_token()}", 200
        return "ok", 200

    return app


class TestCsrfToken:
    def test_generate_creates_token_in_session(self):
        app = _make_app()
        with app.test_request_context():
            with app.test_client() as c:
                resp = c.get("/form")
                assert resp.status_code == 200
                assert b"token=" in resp.data

    def test_post_without_token_returns_403(self):
        app = _make_app()
        with app.test_client() as c:
            # GET to establish session
            c.get("/form")
            # POST without CSRF token
            resp = c.post("/form", data={"foo": "bar"})
            assert resp.status_code == 403

    def test_post_with_valid_token_passes(self):
        app = _make_app()
        with app.test_client() as c:
            # GET to get CSRF token
            resp = c.get("/form")
            token = resp.data.decode().split("token=")[1]
            # POST with valid token
            resp = c.post("/form", data={"csrf_token": token})
            assert resp.status_code == 200

    def test_post_with_wrong_token_returns_403(self):
        app = _make_app()
        with app.test_client() as c:
            c.get("/form")
            resp = c.post("/form", data={"csrf_token": "wrong-token"})
            assert resp.status_code == 403

    def test_post_with_header_token_passes(self):
        app = _make_app()
        with app.test_client() as c:
            resp = c.get("/form")
            token = resp.data.decode().split("token=")[1]
            resp = c.post("/form", headers={"X-CSRF-Token": token})
            assert resp.status_code == 200

    def test_get_request_not_checked(self):
        app = _make_app()
        with app.test_client() as c:
            resp = c.get("/form")
            assert resp.status_code == 200
