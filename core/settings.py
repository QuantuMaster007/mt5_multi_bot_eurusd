"""
Settings loader.

Reads all YAML config files and .env overrides into a single
immutable-ish Settings object. This is the single source of truth
for runtime configuration.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

from core.exceptions import ConfigError
from core.utils import ensure_dir

# Load .env once at import time
load_dotenv()

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f) or {}


class Settings:
    """
    Aggregated configuration object.

    Access sections as attributes: settings.risk, settings.mt5, etc.
    Individual strategy configs are available via settings.strategy_config(name).
    """

    def __init__(self, config_dir: Path = _CONFIG_DIR):
        self._dir = config_dir
        self._load_all()

    def _load_all(self) -> None:
        self.general   = _load_yaml(self._dir / "general_config.yaml").get("general", {})
        self.mt5       = self._load_mt5()
        self.risk      = _load_yaml(self._dir / "risk_config.yaml")
        self.execution = _load_yaml(self._dir / "execution_config.yaml").get("execution", {})
        self.policy    = _load_yaml(self._dir / "policy_config.yaml").get("policy", {})
        self.logging   = _load_yaml(self._dir / "logging_config.yaml").get("logging", {})
        self.symbol    = _load_yaml(self._dir / "symbol_eurusd.yaml")

        # Resolve run mode: env overrides yaml
        env_mode = os.environ.get("RUN_MODE", "").strip()
        self.run_mode: str = env_mode if env_mode else self.general.get("run_mode", "paper")

        # Ensure data directories exist
        data_base = Path(os.environ.get("DATA_DIR", "./data"))
        for sub in ("logs", "heartbeats", "state", "trades", "metrics", "events", "reports"):
            ensure_dir(data_base / sub)
        self.data_dir = data_base

    def _load_mt5(self) -> Dict[str, Any]:
        cfg = _load_yaml(self._dir / "mt5_config.yaml").get("mt5", {})
        # .env overrides
        cfg["login"]    = int(os.environ.get("MT5_LOGIN", cfg.get("login", 0)))
        cfg["password"] = os.environ.get("MT5_PASSWORD", cfg.get("password", ""))
        cfg["server"]   = os.environ.get("MT5_SERVER",   cfg.get("server", ""))
        cfg["path"]     = os.environ.get("MT5_PATH",     cfg.get("path", ""))
        return cfg

    def strategy_config(self, name: str) -> Dict[str, Any]:
        """Load a strategy-specific config file. Returns {} if not found."""
        path = self._dir / "strategies" / f"{name}.yaml"
        if not path.exists():
            return {}
        return _load_yaml(path)

    def get(self, *keys: str, default: Any = None) -> Any:
        """Nested key lookup. get('risk', 'max_daily_loss_fraction')."""
        node: Any = self
        for k in keys:
            if isinstance(node, dict):
                node = node.get(k)
            else:
                node = getattr(node, k, None)
            if node is None:
                return default
        return node


# Module-level singleton — import this in all modules
settings = Settings()
