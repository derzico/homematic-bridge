# SPDX-License-Identifier: Apache-2.0
# tests/test_config_validation.py – Tests for config schema validation

from config.loader import validate_config, validate_internal_config


def _valid_config():
    return {
        "homematic_hcu": "hcu1.local",
        "homematic_token": "abc123",
        "plugin_id": "my-plugin",
        "friendly_name": {"en": "Bridge", "de": "Brücke"},
        "ssl_verify": False,
        "require_api_key": True,
    }


class TestValidateConfig:
    def test_valid_config_no_errors(self):
        assert validate_config(_valid_config()) == []

    def test_minimal_config_passes(self):
        cfg = {"homematic_hcu": "h", "homematic_token": "t", "plugin_id": "p"}
        assert validate_config(cfg) == []

    def test_missing_homematic_hcu(self):
        cfg = _valid_config()
        del cfg["homematic_hcu"]
        errors = validate_config(cfg)
        assert any("homematic_hcu" in e for e in errors)

    def test_missing_token(self):
        cfg = _valid_config()
        del cfg["homematic_token"]
        errors = validate_config(cfg)
        assert any("homematic_token" in e for e in errors)

    def test_missing_plugin_id(self):
        cfg = _valid_config()
        del cfg["plugin_id"]
        errors = validate_config(cfg)
        assert any("plugin_id" in e for e in errors)

    def test_invalid_loxone_udp_port(self):
        cfg = _valid_config()
        cfg["loxone"] = {"udp_port": "not-a-number"}
        errors = validate_config(cfg)
        assert any("loxone.udp_port" in e for e in errors)

    def test_shelly_section_invalid_type(self):
        cfg = _valid_config()
        cfg["shelly"] = True
        errors = validate_config(cfg)
        assert any("shelly" in e and "Dict" in e for e in errors)

    def test_shelly_valid(self):
        cfg = _valid_config()
        cfg["shelly"] = {
            "enabled": True,
            "subnet": "192.168.1.0/24",
            "timeout_sec": 1.5,
            "scan_on_startup": False,
            "scan_interval_hours": 0,
        }
        assert validate_config(cfg) == []

    def test_not_a_dict(self):
        errors = validate_config("not a dict")
        assert len(errors) == 1
        assert "Mapping" in errors[0]

    def test_friendly_name_wrong_type(self):
        cfg = _valid_config()
        cfg["friendly_name"] = "just a string"
        errors = validate_config(cfg)
        assert any("friendly_name" in e for e in errors)


class TestValidateInternalConfig:
    def test_valid(self):
        cfg = {"system_state_path": "data/state.json", "log_level": "info"}
        assert validate_internal_config(cfg) == []

    def test_missing_system_state_path(self):
        errors = validate_internal_config({})
        assert any("system_state_path" in e for e in errors)

    def test_invalid_log_level(self):
        cfg = {"system_state_path": "x", "log_level": "verbose"}
        errors = validate_internal_config(cfg)
        assert any("log_level" in e for e in errors)

    def test_not_a_dict(self):
        errors = validate_internal_config("string")
        assert len(errors) == 1
