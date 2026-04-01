"""
State Store

Simple file-backed key-value store for durable state that survives
process restarts (e.g. cooldown timers, strategy enable flags).

NOT a database — designed for small, infrequently-written state objects.
Each key maps to a JSON-serialisable value stored in a single JSON file
per namespace (strategy name).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core.logger import get_logger
from core.utils import ensure_dir

log = get_logger("state_store")

_STATE_DIR = Path("data/state")
ensure_dir(_STATE_DIR)


class StateStore:
    """
    Per-namespace (strategy) state store backed by a JSON file.

    Usage::

        store = StateStore("mean_reversion")
        store.set("consecutive_losses", 3)
        losses = store.get("consecutive_losses", default=0)
    """

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace
        self._path = _STATE_DIR / f"{namespace}.json"
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._load()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
            self._save()

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception as exc:
                log.warning("StateStore load failed for %s: %s", self._namespace, exc)
        return {}

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as exc:
            log.warning("StateStore save failed for %s: %s", self._namespace, exc)
