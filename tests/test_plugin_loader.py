"""
Tests for PluginLoader — uses temp files and mocks, no MT5 required.
"""
from __future__ import annotations

import sys
import types
import textwrap
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base_strategy import BaseStrategy, StrategyMetadata
from orchestration.plugin_loader import PluginLoader
from orchestration.plugin_validator import PluginValidator
from core.exceptions import PluginMetadataError, PluginLoadError


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _write_strategy_file(tmpdir: Path, content: str) -> Path:
    path = tmpdir / "dynamic_strategy.py"
    path.write_text(textwrap.dedent(content))
    return path


def _mock_settings(cfg: dict):
    m = MagicMock()
    m.strategy_config.return_value = cfg
    m.execution.get.return_value   = 200000
    return m


# ─── PluginLoader.discover_all ────────────────────────────────────────────────

class TestPluginLoaderDiscoverAll:

    def test_discovers_valid_strategy(self, tmp_path):
        code = """
        from typing import ClassVar, Dict, Any, List, Optional
        from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

        class ValidStrategy(BaseStrategy):
            metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
                name="dynamic_strategy",
                version="1.0.0",
                description="A test strategy for discovery",
                symbols=["EURUSD"],
                timeframes=["M15"],
                regime_tags=["ranging"],
                magic_offset=501,
            )

            def generate_signal(self, df, indicators, regimes, spread_pips, tick):
                return None
        """
        _write_strategy_file(tmp_path, code)

        loader = PluginLoader(strategies_dir=tmp_path)

        cfg = {
            "enabled": True,
            "symbol":  "EURUSD",
            "timeframe": "M15",
            "magic_offset": 501,
        }
        with patch("orchestration.plugin_loader.settings", _mock_settings(cfg)), \
             patch("orchestration.plugin_validator.settings", _mock_settings(cfg)):
            discovered = loader.discover_all()

        assert "dynamic_strategy" in discovered

    def test_skips_malformed_file(self, tmp_path):
        bad = tmp_path / "bad_strategy.py"
        bad.write_text("this is not valid python !!!@#$")

        loader = PluginLoader(strategies_dir=tmp_path)
        cfg = {"enabled": True, "symbol": "EURUSD", "timeframe": "M15", "magic_offset": 502}

        with patch("orchestration.plugin_loader.settings", _mock_settings(cfg)), \
             patch("orchestration.plugin_validator.settings", _mock_settings(cfg)):
            discovered = loader.discover_all()

        assert len(discovered) == 0  # bad file skipped, nothing loaded

    def test_skips_disabled_strategy(self, tmp_path):
        code = """
        from typing import ClassVar, Dict, Any, List, Optional
        from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

        class DisabledStrategy(BaseStrategy):
            metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
                name="disabled_strat",
                version="1.0.0",
                description="Should be skipped",
                symbols=["EURUSD"],
                timeframes=["M15"],
                regime_tags=["ranging"],
                magic_offset=503,
            )
            def generate_signal(self, *a): return None
        """
        _write_strategy_file(tmp_path, code)
        loader = PluginLoader(strategies_dir=tmp_path)

        cfg = {"enabled": False, "symbol": "EURUSD", "timeframe": "M15", "magic_offset": 503}
        with patch("orchestration.plugin_loader.settings", _mock_settings(cfg)), \
             patch("orchestration.plugin_validator.settings", _mock_settings(cfg)):
            discovered = loader.discover_all()

        assert "disabled_strat" not in discovered

    def test_skips_template_files(self, tmp_path):
        tmpl = tmp_path / "template_something.py"
        tmpl.write_text("# template file — should be skipped")

        loader = PluginLoader(strategies_dir=tmp_path)
        cfg = {"enabled": True, "symbol": "EURUSD", "timeframe": "M15", "magic_offset": 504}
        with patch("orchestration.plugin_loader.settings", _mock_settings(cfg)), \
             patch("orchestration.plugin_validator.settings", _mock_settings(cfg)):
            discovered = loader.discover_all()

        assert len(discovered) == 0  # template file skipped

    def test_skips_base_strategy_file(self, tmp_path):
        # Copy base_strategy.py into tmp dir — it must be skipped
        import shutil
        src = Path(__file__).parent.parent / "strategies" / "base_strategy.py"
        shutil.copy(src, tmp_path / "base_strategy.py")

        loader = PluginLoader(strategies_dir=tmp_path)
        cfg = {"enabled": True, "symbol": "EURUSD", "timeframe": "M15", "magic_offset": 505}
        with patch("orchestration.plugin_loader.settings", _mock_settings(cfg)), \
             patch("orchestration.plugin_validator.settings", _mock_settings(cfg)):
            discovered = loader.discover_all()

        assert len(discovered) == 0


# ─── PluginLoader._import_strategy_class ─────────────────────────────────────

class TestImportStrategyClass:

    def test_returns_none_for_no_subclass(self, tmp_path):
        py = tmp_path / "plain.py"
        py.write_text("x = 42\n")

        loader = PluginLoader(strategies_dir=tmp_path)
        result = loader._import_strategy_class(py)
        assert result is None

    def test_raises_on_syntax_error(self, tmp_path):
        py = tmp_path / "broken.py"
        py.write_text("def bad syntax (((")

        loader = PluginLoader(strategies_dir=tmp_path)
        with pytest.raises(PluginLoadError, match="Import error"):
            loader._import_strategy_class(py)

    def test_finds_subclass(self, tmp_path):
        code = """
from typing import ClassVar, Dict, Any, List, Optional
from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

class FoundStrategy(BaseStrategy):
    metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
        name="found_strategy", version="1.0.0",
        description="Found", symbols=["EURUSD"],
        timeframes=["M15"], regime_tags=["ranging"],
        magic_offset=506,
    )
    def generate_signal(self, *a): return None
"""
        py = tmp_path / "found_strategy.py"
        py.write_text(textwrap.dedent(code))

        loader = PluginLoader(strategies_dir=tmp_path)
        cls = loader._import_strategy_class(py)
        assert cls is not None
        assert cls.metadata.name == "found_strategy"


# ─── DiscoveryResult startup table smoke test ─────────────────────────────────

class TestStartupTable:

    def test_print_startup_table_does_not_raise(self):
        from orchestration.plugin_loader import DiscoveryResult
        results = [
            DiscoveryResult(
                filename="test.py", status="accepted",
                name="test_strategy", version="1.0.0",
                symbol="EURUSD", timeframe="M15",
                magic=200400,
            ),
            DiscoveryResult(
                filename="bad.py", status="rejected",
                reason="Missing metadata",
            ),
        ]
        loader = PluginLoader()
        loader._print_startup_table(results)  # should not raise
