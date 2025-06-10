import yaml
import os
import sys

def load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_config() -> dict:
    return load_yaml("config/config.yaml")

def load_internal_config() -> dict:
    return load_yaml("config/internal_config.yaml")