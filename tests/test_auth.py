# SPDX-License-Identifier: Apache-2.0
# tests/test_auth.py – Tests for auth decorators and API key management

import os
from unittest.mock import patch

import app.state as state
from app.auth import _ensure_api_key


class TestRequireApiKey:
    def test_passes_with_correct_key(self, auth_app):
        state.API_KEY = "test-secret"
        state.REQUIRE_API_KEY = True
        with auth_app.test_client() as c:
            resp = c.get("/api-test", headers={"X-API-Key": "test-secret"})
            assert resp.status_code == 200

    def test_rejects_wrong_key(self, auth_app):
        state.API_KEY = "test-secret"
        state.REQUIRE_API_KEY = True
        with auth_app.test_client() as c:
            resp = c.get("/api-test", headers={"X-API-Key": "wrong"})
            assert resp.status_code == 401

    def test_rejects_missing_key(self, auth_app):
        state.API_KEY = "test-secret"
        state.REQUIRE_API_KEY = True
        with auth_app.test_client() as c:
            resp = c.get("/api-test")
            assert resp.status_code == 401

    def test_disabled(self, auth_app):
        state.REQUIRE_API_KEY = False
        with auth_app.test_client() as c:
            resp = c.get("/api-test")
            assert resp.status_code == 200

    def test_server_misconfigured(self, auth_app):
        state.REQUIRE_API_KEY = True
        state.API_KEY = None
        with auth_app.test_client() as c:
            resp = c.get("/api-test", headers={"X-API-Key": "anything"})
            assert resp.status_code == 503


class TestRequireWebAuth:
    def test_redirects_unauthenticated(self, auth_app):
        state.REQUIRE_API_KEY = True
        with auth_app.test_client() as c:
            resp = c.get("/web-test")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_passes_authenticated(self, auth_app):
        state.REQUIRE_API_KEY = True
        with auth_app.test_client() as c:
            with c.session_transaction() as sess:
                sess["authenticated"] = True
            resp = c.get("/web-test")
            assert resp.status_code == 200

    def test_disabled(self, auth_app):
        state.REQUIRE_API_KEY = False
        with auth_app.test_client() as c:
            resp = c.get("/web-test")
            assert resp.status_code == 200


class TestEnsureApiKey:
    def test_generates_file(self, tmp_path):
        key_file = str(tmp_path / "api_key.txt")
        state.API_KEY = None
        state.REQUIRE_API_KEY = True
        state.API_KEY_FILE = key_file
        _ensure_api_key()
        assert state.API_KEY is not None
        assert len(state.API_KEY) > 10
        assert os.path.exists(key_file)
        with open(key_file, "r") as f:
            assert f.read().strip() == state.API_KEY

    def test_reads_existing_file(self, tmp_path):
        key_file = str(tmp_path / "api_key.txt")
        with open(key_file, "w") as f:
            f.write("my-existing-key")
        state.API_KEY = None
        state.REQUIRE_API_KEY = True
        state.API_KEY_FILE = key_file
        _ensure_api_key()
        assert state.API_KEY == "my-existing-key"

    def test_skips_when_already_set(self):
        state.API_KEY = "already-set"
        _ensure_api_key()
        assert state.API_KEY == "already-set"
