# SPDX-License-Identifier: Apache-2.0
# tests/test_utils_merge.py – Tests for state merging logic in app/utils.py

import json
from unittest.mock import patch

from app.utils import (
    _find_device_in_list,
    _locate_devices_container,
    _merge_device,
    _merge_functional_channels,
    _merge_group,
    save_system_state,
)


# ── _merge_functional_channels ────────────────────────────────────────────────

class TestMergeFunctionalChannels:
    def test_adds_new_key(self):
        current = {"0": {"on": True}}
        incoming = {"1": {"dimLevel": 0.5}}
        result = _merge_functional_channels(current, incoming)
        assert result["0"] == {"on": True}
        assert result["1"] == {"dimLevel": 0.5}

    def test_overwrites_existing(self):
        current = {"0": {"on": True, "dimLevel": 1.0}}
        incoming = {"0": {"on": False}}
        result = _merge_functional_channels(current, incoming)
        assert result["0"] == {"on": False, "dimLevel": 1.0}

    def test_preserves_other_indices(self):
        current = {"0": {"on": True}, "1": {"dimLevel": 0.8}}
        incoming = {"0": {"on": False}}
        result = _merge_functional_channels(current, incoming)
        assert result["1"] == {"dimLevel": 0.8}

    def test_handles_none_current(self):
        result = _merge_functional_channels(None, {"0": {"on": True}})
        assert result == {"0": {"on": True}}

    def test_handles_none_incoming(self):
        current = {"0": {"on": True}}
        result = _merge_functional_channels(current, None)
        assert result == {"0": {"on": True}}

    def test_handles_both_none(self):
        result = _merge_functional_channels(None, None)
        assert result == {}


# ── _merge_device ─────────────────────────────────────────────────────────────

class TestMergeDevice:
    def test_shallow_fields_overwritten(self):
        current = {"id": "d1", "label": "Old", "type": "SWITCH"}
        incoming = {"id": "d1", "label": "New"}
        result = _merge_device(current, incoming)
        assert result["label"] == "New"
        assert result["type"] == "SWITCH"

    def test_deep_channel_merge(self):
        current = {
            "id": "d1",
            "functionalChannels": {"0": {"on": True, "dimLevel": 1.0}},
        }
        incoming = {
            "id": "d1",
            "functionalChannels": {"0": {"on": False}},
        }
        result = _merge_device(current, incoming)
        assert result["functionalChannels"]["0"]["on"] is False
        assert result["functionalChannels"]["0"]["dimLevel"] == 1.0

    def test_handles_none_current(self):
        incoming = {"id": "d1", "label": "New"}
        result = _merge_device(None, incoming)
        assert result == {"id": "d1", "label": "New"}

    def test_handles_none_incoming(self):
        current = {"id": "d1", "label": "Old"}
        result = _merge_device(current, None)
        assert result == {"id": "d1", "label": "Old"}

    def test_incoming_without_channels_preserves_current(self):
        current = {
            "id": "d1",
            "functionalChannels": {"0": {"on": True}},
        }
        incoming = {"id": "d1", "label": "Updated"}
        result = _merge_device(current, incoming)
        assert result["functionalChannels"] == {"0": {"on": True}}
        assert result["label"] == "Updated"


# ── _merge_group ──────────────────────────────────────────────────────────────

class TestMergeGroup:
    def test_overwrites_fields(self):
        current = {"id": "g1", "label": "Old", "on": True}
        incoming = {"id": "g1", "label": "New"}
        result = _merge_group(current, incoming)
        assert result["label"] == "New"
        assert result["on"] is True

    def test_handles_none_current(self):
        result = _merge_group(None, {"id": "g1"})
        assert result == {"id": "g1"}


# ── _locate_devices_container ────────────────────────────────────────────────

class TestLocateDevicesContainer:
    def test_body_devices(self):
        snap = {"body": {"devices": [{"id": "d1"}]}}
        container, hint = _locate_devices_container(snap)
        assert container == [{"id": "d1"}]
        assert "body.devices" in hint

    def test_body_home_devices(self):
        snap = {"body": {"home": {"devices": {"d1": {"id": "d1"}}}}}
        container, hint = _locate_devices_container(snap)
        assert container == {"d1": {"id": "d1"}}

    def test_body_body_devices(self):
        snap = {"body": {"body": {"devices": [{"id": "d1"}]}}}
        container, hint = _locate_devices_container(snap)
        assert isinstance(container, list)

    def test_not_found(self):
        snap = {"something": "else"}
        container, hint = _locate_devices_container(snap)
        assert container is None
        assert hint == "not found"


# ── _find_device_in_list ─────────────────────────────────────────────────────

class TestFindDeviceInList:
    def test_found(self):
        devs = [{"id": "d1"}, {"id": "d2"}, {"id": "d3"}]
        dev, idx = _find_device_in_list(devs, "d2")
        assert dev == {"id": "d2"}
        assert idx == 1

    def test_not_found(self):
        devs = [{"id": "d1"}]
        dev, idx = _find_device_in_list(devs, "d99")
        assert dev is None
        assert idx is None


# ── save_system_state ────────────────────────────────────────────────────────

class TestSaveSystemState:
    def test_full_snapshot(self, tmp_snapshot):
        msg = {"type": "HMIP_SYSTEM_RESPONSE", "body": {"devices": {"d1": {}}}}
        with patch("app.utils.SNAPSHOT_PATH", tmp_snapshot):
            save_system_state(msg)
        with open(tmp_snapshot, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["type"] == "HMIP_SYSTEM_RESPONSE"
        assert saved["body"]["devices"]["d1"] == {}

    def test_delta_merge(self, tmp_snapshot):
        # Write base snapshot first
        base = {
            "type": "HMIP_SYSTEM_RESPONSE",
            "body": {
                "body": {
                    "devices": {"d1": {"id": "d1", "label": "Old", "functionalChannels": {"0": {"on": True}}}},
                    "groups": {},
                },
            },
        }
        with open(tmp_snapshot, "w", encoding="utf-8") as f:
            json.dump(base, f)

        event = {
            "type": "HMIP_SYSTEM_EVENT",
            "body": {
                "eventTransaction": {
                    "events": {
                        "0": {"device": {"id": "d1", "label": "New", "functionalChannels": {"0": {"on": False}}}}
                    }
                }
            },
        }
        with patch("app.utils.SNAPSHOT_PATH", tmp_snapshot):
            save_system_state(event)

        with open(tmp_snapshot, "r", encoding="utf-8") as f:
            saved = json.load(f)
        dev = saved["body"]["body"]["devices"]["d1"]
        assert dev["label"] == "New"
        assert dev["functionalChannels"]["0"]["on"] is False

    def test_event_without_snapshot(self, tmp_snapshot):
        event = {"type": "HMIP_SYSTEM_EVENT", "body": {"eventTransaction": {"events": {}}}}
        with patch("app.utils.SNAPSHOT_PATH", tmp_snapshot):
            save_system_state(event)  # should not crash

    def test_non_dict_msg(self):
        save_system_state("not a dict")  # should not crash
