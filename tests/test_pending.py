# SPDX-License-Identifier: Apache-2.0
# tests/test_pending.py – Tests for pending request registry

import time

import app.state as state
from app.websocket_handler import _cleanup_pending, _register_pending, _resolve_pending


class TestPendingRegistry:
    def test_register_and_resolve(self):
        _register_pending("req-1", "/hmip/home/getSystemState")
        meta = _resolve_pending("req-1")
        assert meta is not None
        assert meta["path"] == "/hmip/home/getSystemState"
        assert "ts" in meta

    def test_resolve_removes_entry(self):
        _register_pending("req-2", "/test")
        _resolve_pending("req-2")
        assert _resolve_pending("req-2") is None

    def test_resolve_unknown_id(self):
        assert _resolve_pending("nonexistent") is None

    def test_resolve_none_id(self):
        assert _resolve_pending(None) is None

    def test_register_none_id(self):
        _register_pending(None, "/test")
        assert len(state.pending) == 0


class TestCleanupPending:
    def test_removes_old_entries(self):
        with state.pending_lock:
            state.pending["old-req"] = {"path": "/test", "ts": time.time() - 200}
        _cleanup_pending()
        with state.pending_lock:
            assert "old-req" not in state.pending

    def test_keeps_recent_entries(self):
        _register_pending("fresh-req", "/test")
        _cleanup_pending()
        with state.pending_lock:
            assert "fresh-req" in state.pending

    def test_mixed_old_and_new(self):
        with state.pending_lock:
            state.pending["old"] = {"path": "/a", "ts": time.time() - 200}
        _register_pending("new", "/b")
        _cleanup_pending()
        with state.pending_lock:
            assert "old" not in state.pending
            assert "new" in state.pending
