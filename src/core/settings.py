"""配置加载：config.yaml + model_policy.yaml，提供全局单例访问。"""
import os
from typing import Any, Dict

import yaml

from .paths import app_root

_config: Dict[str, Any] = {}
_policy: Dict[str, Any] = {}

def _load(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def config() -> Dict[str, Any]:
    global _config
    if not _config:
        _config = _load(os.path.join(app_root(), "config.yaml"))
    return _config

def model_policy() -> Dict[str, Any]:
    global _policy
    if not _policy:
        _policy = _load(os.path.join(app_root(), "model_policy.yaml"))
    return _policy

def reload():
    global _config, _policy
    _config, _policy = {}, {}

def save_config():
    with open(os.path.join(app_root(), "config.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(_config, f, allow_unicode=True, sort_keys=False)
