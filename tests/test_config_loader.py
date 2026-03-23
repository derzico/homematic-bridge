# SPDX-License-Identifier: Apache-2.0
# tests/test_config_loader.py – Tests for config loading

import yaml

from config.loader import load_yaml


class TestLoadYaml:
    def test_returns_dict(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nnested:\n  a: 1\n", encoding="utf-8")
        result = load_yaml(str(f))
        assert result == {"key": "value", "nested": {"a": 1}}

    def test_missing_file(self, tmp_path):
        result = load_yaml(str(tmp_path / "nonexistent.yaml"))
        assert result == {}

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        result = load_yaml(str(f))
        assert result == {}

    def test_invalid_yaml(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("key: [invalid\n  broken:", encoding="utf-8")
        try:
            load_yaml(str(f))
            # If it doesn't raise, that's a bug we document
            assert False, "Expected yaml.YAMLError"
        except yaml.YAMLError:
            pass  # expected
