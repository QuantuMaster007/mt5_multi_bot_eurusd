"""
Tests for PluginValidator — runs without MT5.

Covers: metadata checks, config schema validation, conflict detection,
        graceful rejection of malformed plugins.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Stub MT5 before any project import
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base_strategy import BaseStrategy, StrategyMetadata
from orchestration.plugin_validator import PluginValidator, ConfigField
from core.exceptions import (
    PluginMetadataError, PluginConfigError, PluginConflictError
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_class(
    name="test_valid",
    version="1.0.0",
    description="A test strategy",
    symbols=None,
    timeframes=None,
    regime_tags=None,
    risk_profile="medium",
    magic_offset=500,
    schema=None,
):
    """Build a minimal BaseStrategy subclass for testing."""
    meta = StrategyMetadata(
        name=name,
        version=version,
        description=description,
        symbols=symbols or ["EURUSD"],
        timeframes=timeframes or ["M15"],
        regime_tags=regime_tags or ["ranging"],
        risk_profile=risk_profile,
        magic_offset=magic_offset,
    )

    cls_dict = {
        "metadata": meta,
        "generate_signal": lambda self, *a, **k: None,
    }
    if schema is not None:
        cls_dict["CONFIG_SCHEMA"] = schema

    return type("DynamicStrategy", (BaseStrategy,), cls_dict)


def _validator_with_cfg(cfg_dict):
    """Return a validator whose settings.strategy_config returns cfg_dict."""
    v = PluginValidator()
    with patch("orchestration.plugin_validator.settings") as mock_settings:
        mock_settings.strategy_config.return_value = cfg_dict
        yield v, mock_settings


# ─── Metadata structure tests ─────────────────────────────────────────────────

class TestMetadataStructure:

    def test_valid_class_passes(self):
        v = PluginValidator()
        cls = _make_class()
        with patch("orchestration.plugin_validator.settings") as ms:
            ms.strategy_config.return_value = {
                "symbol": "EURUSD", "timeframe": "M15",
                "magic_offset": 500, "enabled": True,
            }
            warnings = v.validate(cls, "test_valid.py")
        # Should not raise; warnings about missing CONFIG_SCHEMA are OK
        assert isinstance(warnings, list)

    def test_missing_metadata_raises(self):
        cls = type("NoMeta", (BaseStrategy,), {
            "generate_signal": lambda self, *a, **k: None
        })
        v = PluginValidator()
        with pytest.raises(PluginMetadataError, match="no 'metadata' class variable"):
            v._check_metadata_structure(cls, "no_meta.py")

    def test_wrong_metadata_type_raises(self):
        cls = type("WrongMeta", (BaseStrategy,), {
            "metadata": {"name": "wrong"},
            "generate_signal": lambda self, *a, **k: None
        })
        v = PluginValidator()
        with pytest.raises(PluginMetadataError, match="StrategyMetadata instance"):
            v._check_metadata_structure(cls, "wrong_meta.py")

    def test_empty_name_raises(self):
        cls = _make_class(name="")
        v = PluginValidator()
        with pytest.raises(PluginMetadataError):
            v._check_metadata_structure(cls, "empty_name.py")


# ─── Metadata value tests ─────────────────────────────────────────────────────

class TestMetadataValues:

    def test_invalid_name_format_raises(self):
        v = PluginValidator()
        meta = _make_class(name="MyStrategy").metadata
        with pytest.raises(PluginMetadataError, match="invalid"):
            v._check_metadata_values(meta, "x.py")

    def test_name_with_capital_raises(self):
        v = PluginValidator()
        meta = _make_class(name="My_Strategy").metadata
        with pytest.raises(PluginMetadataError):
            v._check_metadata_values(meta, "x.py")

    def test_valid_snake_case_passes(self):
        v = PluginValidator()
        meta = _make_class(name="my_strategy_v2").metadata
        warnings = v._check_metadata_values(meta, "x.py")
        assert isinstance(warnings, list)

    def test_invalid_version_raises(self):
        v = PluginValidator()
        meta = _make_class(version="v1.0").metadata
        with pytest.raises(PluginMetadataError, match="semver"):
            v._check_metadata_values(meta, "x.py")

    def test_invalid_timeframe_raises(self):
        v = PluginValidator()
        meta = _make_class(timeframes=["HOURLY"]).metadata
        with pytest.raises(PluginMetadataError, match="timeframe"):
            v._check_metadata_values(meta, "x.py")

    def test_invalid_risk_profile_raises(self):
        v = PluginValidator()
        meta = _make_class(risk_profile="extreme").metadata
        with pytest.raises(PluginMetadataError, match="risk_profile"):
            v._check_metadata_values(meta, "x.py")

    def test_zero_magic_offset_warns(self):
        v = PluginValidator()
        meta = _make_class(magic_offset=0).metadata
        warnings = v._check_metadata_values(meta, "x.py")
        assert any("magic_offset" in w for w in warnings)


# ─── Config schema tests ──────────────────────────────────────────────────────

class TestConfigSchema:

    def test_valid_config_passes(self):
        schema = {
            "fast_ema": ConfigField(int, required=True, default=9, description="Fast EMA"),
            "slow_ema": ConfigField(int, required=True, default=21, description="Slow EMA"),
        }
        cls = _make_class(schema=schema)
        v = PluginValidator()
        cfg = {"fast_ema": 9, "slow_ema": 21, "symbol": "EURUSD",
               "timeframe": "M15", "magic_offset": 500}
        warnings = v._check_config_schema(cls, "test_valid", cfg)
        assert isinstance(warnings, list)

    def test_missing_required_field_raises(self):
        schema = {
            "adx_threshold": ConfigField(float, required=True, description="ADX min"),
        }
        cls = _make_class(schema=schema)
        v = PluginValidator()
        cfg = {"symbol": "EURUSD", "timeframe": "M15", "magic_offset": 500}
        # adx_threshold is required but absent
        with pytest.raises(PluginConfigError, match="adx_threshold"):
            v._check_config_schema(cls, "test", cfg)

    def test_wrong_type_raises(self):
        schema = {
            "period": ConfigField(int, required=True, description="Period"),
        }
        cls = _make_class(schema=schema)
        v = PluginValidator()
        cfg = {"period": "not_an_int", "symbol": "EURUSD",
               "timeframe": "M15", "magic_offset": 500}
        with pytest.raises(PluginConfigError, match="expected int"):
            v._check_config_schema(cls, "test", cfg)

    def test_below_min_raises(self):
        schema = {
            "period": ConfigField(int, required=True, min_val=5, description="Period"),
        }
        cls = _make_class(schema=schema)
        v = PluginValidator()
        cfg = {"period": 2, "symbol": "EURUSD", "timeframe": "M15", "magic_offset": 500}
        with pytest.raises(PluginConfigError, match="below minimum"):
            v._check_config_schema(cls, "test", cfg)

    def test_above_max_raises(self):
        schema = {
            "spread": ConfigField(float, required=True, max_val=5.0, description="Spread"),
        }
        cls = _make_class(schema=schema)
        v = PluginValidator()
        cfg = {"spread": 99.0, "symbol": "EURUSD", "timeframe": "M15", "magic_offset": 500}
        with pytest.raises(PluginConfigError, match="exceeds maximum"):
            v._check_config_schema(cls, "test", cfg)

    def test_invalid_choice_raises(self):
        schema = {
            "mode": ConfigField(str, required=True,
                                choices=["aggressive", "conservative"],
                                description="Trade mode"),
        }
        cls = _make_class(schema=schema)
        v = PluginValidator()
        cfg = {"mode": "ultra", "symbol": "EURUSD", "timeframe": "M15", "magic_offset": 500}
        with pytest.raises(PluginConfigError, match="not one of"):
            v._check_config_schema(cls, "test", cfg)

    def test_optional_field_absent_passes(self):
        schema = {
            "optional_thing": ConfigField(int, required=False, default=10, description="opt"),
        }
        cls = _make_class(schema=schema)
        v = PluginValidator()
        cfg = {"symbol": "EURUSD", "timeframe": "M15", "magic_offset": 500}
        warnings = v._check_config_schema(cls, "test", cfg)
        assert isinstance(warnings, list)

    def test_no_schema_emits_warning(self):
        cls = _make_class(schema=None)
        v = PluginValidator()
        cfg = {"symbol": "EURUSD", "timeframe": "M15", "magic_offset": 500}
        warnings = v._check_config_schema(cls, "test", cfg)
        assert any("CONFIG_SCHEMA" in w for w in warnings)

    def test_invalid_timeframe_in_config_raises(self):
        cls = _make_class()
        v = PluginValidator()
        cfg = {"symbol": "EURUSD", "timeframe": "HOURLY", "magic_offset": 500}
        with pytest.raises(PluginConfigError):
            v._check_config_schema(cls, "test", cfg)


# ─── Conflict tests ───────────────────────────────────────────────────────────

class TestConflicts:

    def test_duplicate_name_raises(self):
        v = PluginValidator()
        v._accepted_names.add("my_strategy")
        meta = _make_class(name="my_strategy").metadata
        with pytest.raises(PluginConflictError, match="already registered"):
            v._check_conflicts(meta, "my_strategy.py")

    def test_duplicate_magic_offset_raises(self):
        v = PluginValidator()
        v._accepted_offsets[300] = "scalping"
        meta = _make_class(name="new_strategy", magic_offset=300).metadata
        with pytest.raises(PluginConflictError, match="already used by"):
            v._check_conflicts(meta, "new_strategy.py")

    def test_zero_offset_no_conflict_check(self):
        v = PluginValidator()
        v._accepted_offsets[0] = "other"
        meta = _make_class(name="zero_offset", magic_offset=0).metadata
        # Should not raise — offset 0 is excluded from conflict checking
        # (a warning is emitted by _check_metadata_values instead)
        v._check_conflicts(meta, "zero_offset.py")

    def test_reset_clears_state(self):
        v = PluginValidator()
        v._accepted_names.add("something")
        v._accepted_offsets[999] = "something"
        v.reset()
        assert len(v._accepted_names) == 0
        assert len(v._accepted_offsets) == 0


# ─── ConfigField.validate() tests ────────────────────────────────────────────

class TestConfigFieldValidate:

    def test_bool_from_string(self):
        f = ConfigField(bool, required=True, description="flag")
        assert f.validate("flag", "true", "test") is True
        assert f.validate("flag", "false", "test") is False
        assert f.validate("flag", "yes", "test") is True

    def test_float_from_int(self):
        f = ConfigField(float, required=True, description="val")
        assert f.validate("val", 10, "test") == 10.0

    def test_int_from_string_that_is_int(self):
        f = ConfigField(int, required=True, description="val")
        assert f.validate("val", "14", "test") == 14

    def test_non_coercible_raises(self):
        f = ConfigField(int, required=True, description="val")
        with pytest.raises(PluginConfigError):
            f.validate("val", "abc", "test")
