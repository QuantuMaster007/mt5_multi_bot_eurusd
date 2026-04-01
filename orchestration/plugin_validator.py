"""
Plugin Validator

Performs all validation checks on a strategy class BEFORE it is
accepted into the registry. Separated from plugin_loader.py so
validation logic can be tested independently and extended without
touching discovery code.

Validation pipeline (in order):
  1. Metadata structural check  — required fields, types, naming rules
  2. Metadata value check       — known timeframes, symbols, risk profiles
  3. Config file presence check — warns if YAML is missing (not fatal)
  4. Config schema check        — validates YAML fields against CONFIG_SCHEMA
  5. Conflict check             — ensures magic_offset and name are unique
     across all previously accepted plugins

Any step can raise a specific subclass of PluginLoadError so the
loader can emit a targeted error message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from core.exceptions import (
    PluginMetadataError,
    PluginConfigError,
    PluginConflictError,
)
from core.logger import get_logger
from core.settings import settings

log = get_logger("plugin_validator")

# ─── Constants ────────────────────────────────────────────────────────────────

VALID_TIMEFRAMES: Set[str] = {
    "M1", "M2", "M3", "M4", "M5", "M6",
    "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12",
    "D1", "W1", "MN1",
}

VALID_RISK_PROFILES: Set[str] = {"low", "medium", "high"}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")   # snake_case, 2-40 chars
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")         # semver-ish: 1.0.0

_REQUIRED_METADATA_FIELDS = {
    "name", "version", "description", "symbols", "timeframes", "regime_tags"
}

# Globally reserved magic offsets (prevent accidental collision)
_RESERVED_MAGIC_OFFSETS: Dict[int, str] = {}   # offset → strategy_name
_REGISTERED_NAMES:       Set[str]       = set()


# ─── Config schema DSL ────────────────────────────────────────────────────────

@dataclass
class ConfigField:
    """
    Declares one field in a strategy's CONFIG_SCHEMA.

    Strategies may define a ``CONFIG_SCHEMA`` class variable as a dict
    mapping field names to ConfigField instances. The validator then
    checks the loaded YAML against this schema.

    Example usage in a strategy class::

        CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
            "fast_ema":   ConfigField(int,   required=True,  default=9,    description="Fast EMA period"),
            "slow_ema":   ConfigField(int,   required=True,  default=21,   description="Slow EMA period"),
            "max_spread": ConfigField(float, required=False, default=2.0,
                                      min_val=0.1, max_val=10.0,
                                      description="Max allowed spread in pips"),
        }

    If CONFIG_SCHEMA is not defined on the class, config validation
    is skipped (a warning is logged encouraging its use).
    """
    type:        type                  # int | float | str | bool
    required:    bool  = True
    default:     Any   = None
    min_val:     Optional[float] = None
    max_val:     Optional[float] = None
    choices:     Optional[List[Any]] = None   # allowed values
    description: str   = ""

    def validate(self, key: str, raw_value: Any, strategy_name: str) -> Any:
        """
        Coerce and validate a raw config value.
        Returns the coerced value.
        Raises PluginConfigError on type/range/choice failure.
        """
        value = raw_value

        # Type coercion
        try:
            if self.type is bool:
                # YAML booleans are already Python bools; handle string "true"/"false"
                if isinstance(value, str):
                    value = value.lower() in ("true", "yes", "1")
                else:
                    value = bool(value)
            else:
                value = self.type(value)
        except (ValueError, TypeError) as exc:
            raise PluginConfigError(
                f"[{strategy_name}] config field '{key}': "
                f"expected {self.type.__name__}, got {type(raw_value).__name__} "
                f"({raw_value!r}). {self.description}",
                strategy_name=strategy_name,
                field=key,
            ) from exc

        # Range checks
        if self.min_val is not None and value < self.min_val:
            raise PluginConfigError(
                f"[{strategy_name}] config field '{key}' = {value} "
                f"is below minimum {self.min_val}. {self.description}",
                strategy_name=strategy_name,
                field=key,
            )
        if self.max_val is not None and value > self.max_val:
            raise PluginConfigError(
                f"[{strategy_name}] config field '{key}' = {value} "
                f"exceeds maximum {self.max_val}. {self.description}",
                strategy_name=strategy_name,
                field=key,
            )

        # Allowed choices
        if self.choices is not None and value not in self.choices:
            raise PluginConfigError(
                f"[{strategy_name}] config field '{key}' = {value!r} "
                f"is not one of {self.choices}. {self.description}",
                strategy_name=strategy_name,
                field=key,
            )

        return value


# ─── Validator ────────────────────────────────────────────────────────────────

class PluginValidator:
    """
    Validates a strategy class through the full validation pipeline.

    Usage::

        validator = PluginValidator()
        issues = validator.validate(MyStrategy, py_file)
        # issues is empty list on success; entries are ValidationIssue objects
    """

    def __init__(self) -> None:
        self._accepted_offsets: Dict[int, str] = {}
        self._accepted_names:   Set[str]       = set()

    def reset(self) -> None:
        """Call at the start of a fresh discovery run."""
        self._accepted_offsets.clear()
        self._accepted_names.clear()

    def validate(
        self,
        cls: Type,
        py_file_name: str,
    ) -> List[str]:
        """
        Run all validation checks. Raises a specific PluginLoadError subclass
        on the first hard failure. Returns a list of warning strings (non-fatal).
        """
        warnings: List[str] = []

        # Step 1 — Metadata structure
        self._check_metadata_structure(cls, py_file_name)

        meta = cls.metadata

        # Step 2 — Metadata values
        warnings += self._check_metadata_values(meta, py_file_name)

        # Step 3 — Config file presence
        cfg = settings.strategy_config(meta.name)
        if not cfg:
            warnings.append(
                f"No config file found at config/strategies/{meta.name}.yaml. "
                f"Copy config/strategies/template.yaml and fill it in."
            )

        # Step 4 — Config schema validation
        if cfg:
            warnings += self._check_config_schema(cls, meta.name, cfg)

        # Step 5 — Conflict check
        self._check_conflicts(meta, py_file_name)

        # Register as accepted
        self._accepted_offsets[meta.magic_offset] = meta.name
        self._accepted_names.add(meta.name)

        return warnings

    # ─── Step 1: Metadata structure ──────────────────────────────────────

    def _check_metadata_structure(self, cls: Type, filename: str) -> None:
        from strategies.base_strategy import BaseStrategy, StrategyMetadata

        if not hasattr(cls, "metadata"):
            raise PluginMetadataError(
                f"{cls.__name__} in {filename} has no 'metadata' class variable.\n"
                f"  Add to your class:\n"
                f"      metadata: ClassVar[StrategyMetadata] = StrategyMetadata(\n"
                f"          name=\"my_strategy\",\n"
                f"          version=\"1.0.0\",\n"
                f"          description=\"What this strategy does\",\n"
                f"          symbols=[\"EURUSD\"],\n"
                f"          timeframes=[\"M15\"],\n"
                f"          regime_tags=[\"ranging\"],\n"
                f"          magic_offset=400,\n"
                f"      )"
            )

        meta = cls.metadata
        if not isinstance(meta, StrategyMetadata):
            raise PluginMetadataError(
                f"{cls.__name__}.metadata must be a StrategyMetadata instance, "
                f"got {type(meta).__name__}."
            )

        for field in _REQUIRED_METADATA_FIELDS:
            val = getattr(meta, field, None)
            if not val:
                raise PluginMetadataError(
                    f"{cls.__name__}.metadata.{field} is empty or missing.\n"
                    f"  All required fields: {sorted(_REQUIRED_METADATA_FIELDS)}"
                )

    # ─── Step 2: Metadata values ─────────────────────────────────────────

    def _check_metadata_values(self, meta: Any, filename: str) -> List[str]:
        warnings: List[str] = []
        name = meta.name

        # Name format
        if not _NAME_RE.match(name):
            raise PluginMetadataError(
                f"Strategy name '{name}' is invalid.\n"
                f"  Rules: lowercase letters/digits/underscores, "
                f"start with a letter, 2-40 characters.\n"
                f"  Good: 'momentum_breakout', 'ema_cross', 'rsi_reversal'\n"
                f"  Bad:  'MyStrategy', '1breakout', 'x'"
            )

        # Version format
        if not _VERSION_RE.match(str(meta.version)):
            raise PluginMetadataError(
                f"[{name}] version '{meta.version}' must be semver format: "
                f"MAJOR.MINOR.PATCH  e.g. '1.0.0'"
            )

        # Timeframes
        invalid_tf = [tf for tf in meta.timeframes if tf not in VALID_TIMEFRAMES]
        if invalid_tf:
            raise PluginMetadataError(
                f"[{name}] invalid timeframe(s): {invalid_tf}\n"
                f"  Valid timeframes: {sorted(VALID_TIMEFRAMES)}"
            )

        # Risk profile
        if meta.risk_profile not in VALID_RISK_PROFILES:
            raise PluginMetadataError(
                f"[{name}] risk_profile='{meta.risk_profile}' is not valid.\n"
                f"  Must be one of: {sorted(VALID_RISK_PROFILES)}"
            )

        # Symbols — warn if contains non-standard symbol
        for sym in meta.symbols:
            if not sym.isalpha() or len(sym) < 3:
                warnings.append(
                    f"[{name}] symbol '{sym}' looks unusual. "
                    f"Standard FX symbols are 6-character uppercase e.g. 'EURUSD'."
                )

        # magic_offset — warn if 0 (will collide with any offset-0 strategy)
        if meta.magic_offset == 0:
            warnings.append(
                f"[{name}] magic_offset=0. This may cause trade attribution "
                f"conflicts with other strategies. Set a unique non-zero value "
                f"in config/strategies/{name}.yaml  (e.g. magic_offset: 400)."
            )

        # description length
        if len(meta.description) < 10:
            warnings.append(
                f"[{name}] description is very short. "
                f"Consider writing a sentence describing what the strategy does."
            )

        return warnings

    # ─── Step 4: Config schema validation ────────────────────────────────

    def _check_config_schema(
        self, cls: Type, strategy_name: str, cfg: Dict[str, Any]
    ) -> List[str]:
        """
        If the strategy declares CONFIG_SCHEMA, validate every field.
        Always validates the universal fields (symbol, timeframe, magic_offset).
        """
        warnings: List[str] = []
        schema: Optional[Dict[str, ConfigField]] = getattr(cls, "CONFIG_SCHEMA", None)

        # Universal fields every strategy config should have
        universal_schema: Dict[str, ConfigField] = {
            "strategy":     ConfigField(str,  required=False, description="Must match metadata.name"),
            "enabled":      ConfigField(bool, required=False, default=True,
                                        description="Set false to disable loading"),
            "symbol":       ConfigField(str,  required=True,
                                        choices=["EURUSD"],
                                        description="Trading symbol"),
            "timeframe":    ConfigField(str,  required=True,
                                        choices=sorted(VALID_TIMEFRAMES),
                                        description="MT5 timeframe string"),
            "magic_offset": ConfigField(int,  required=True,
                                        min_val=1, max_val=9999,
                                        description="Unique magic number offset (1–9999)"),
            "max_positions":ConfigField(int,  required=False, default=1,
                                        min_val=1, max_val=10,
                                        description="Max simultaneous open positions"),
        }

        # Merge with strategy-specific schema
        combined = {**universal_schema}
        if schema:
            combined.update(schema)
        else:
            warnings.append(
                f"[{strategy_name}] No CONFIG_SCHEMA defined on the class. "
                f"Defining CONFIG_SCHEMA enables automatic config validation "
                f"and documentation. See template_strategy.py for an example."
            )

        # Validate each schema field against the loaded YAML
        for field_name, field_def in combined.items():
            raw = cfg.get(field_name)

            if raw is None:
                if field_def.required and field_def.default is None:
                    raise PluginConfigError(
                        f"[{strategy_name}] config/strategies/{strategy_name}.yaml "
                        f"is missing required field '{field_name}'.\n"
                        f"  Description: {field_def.description}\n"
                        f"  Expected type: {field_def.type.__name__}",
                        strategy_name=strategy_name,
                        field=field_name,
                    )
                # Use default — no error
                continue

            # Validate present value
            field_def.validate(field_name, raw, strategy_name)

        # Check for magic_offset match in config vs. class metadata
        cfg_offset = cfg.get("magic_offset")
        cls_offset  = getattr(cls.metadata, "magic_offset", 0)
        if cfg_offset is not None and int(cfg_offset) != int(cls_offset):
            warnings.append(
                f"[{strategy_name}] magic_offset mismatch: "
                f"config={cfg_offset}, metadata.magic_offset={cls_offset}. "
                f"The config value takes precedence. "
                f"Update metadata.magic_offset to match for clarity."
            )

        return warnings

    # ─── Step 5: Conflict check ───────────────────────────────────────────

    def _check_conflicts(self, meta: Any, filename: str) -> None:
        # Duplicate name
        if meta.name in self._accepted_names:
            raise PluginConflictError(
                f"Strategy name '{meta.name}' is already registered by a "
                f"previously loaded plugin. Each strategy must have a unique name.\n"
                f"  Conflicting file: {filename}"
            )

        # Duplicate magic_offset
        offset = meta.magic_offset
        if offset != 0 and offset in self._accepted_offsets:
            existing = self._accepted_offsets[offset]
            raise PluginConflictError(
                f"[{meta.name}] magic_offset={offset} is already used by "
                f"strategy '{existing}'.\n"
                f"  Each strategy must have a unique magic_offset to prevent "
                f"trade attribution conflicts.\n"
                f"  Fix: change magic_offset in {meta.name}'s config YAML and "
                f"update metadata.magic_offset to match."
            )


# Module-level singleton (reset at the start of each discovery run)
plugin_validator = PluginValidator()
