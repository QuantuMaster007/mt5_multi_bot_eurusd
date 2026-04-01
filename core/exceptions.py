"""
Custom exception hierarchy for the MT5 framework.
Structured exceptions allow catch blocks to respond correctly
to different failure categories.
"""


class FrameworkError(Exception):
    """Base class for all framework errors."""


# ─── MT5 / Broker Errors ─────────────────────────────────────────────────────
class MT5ConnectionError(FrameworkError):
    """Cannot connect to or initialise MT5 terminal."""


class MT5AuthError(FrameworkError):
    """MT5 login failed."""


class MT5DataError(FrameworkError):
    """Failed to retrieve market data from MT5."""


class BrokerRejectionError(FrameworkError):
    """Order was rejected by the broker."""
    def __init__(self, message: str, retcode: int = 0):
        super().__init__(message)
        self.retcode = retcode


class HardRejectionError(BrokerRejectionError):
    """Rejection that should NOT be retried — triggers cooldown."""


class TransientRejectionError(BrokerRejectionError):
    """Rejection that may succeed on retry."""


class InvalidFillModeError(BrokerRejectionError):
    """Chosen fill mode is not supported for this symbol/broker."""


# ─── Validation Errors ───────────────────────────────────────────────────────
class OrderValidationError(FrameworkError):
    """Order request failed pre-validation checks."""


class SymbolNotFoundError(FrameworkError):
    """Symbol is not available on this broker."""


class VolumeError(OrderValidationError):
    """Volume is below min, above max, or not aligned to step."""


class StopLevelError(OrderValidationError):
    """SL or TP violates the broker stop level requirement."""


# ─── Risk / Policy Errors ────────────────────────────────────────────────────
class RiskBlockError(FrameworkError):
    """Trade blocked by the risk manager."""


class PolicyBlockError(FrameworkError):
    """Trade blocked by the policy engine."""


class DrawdownLimitError(RiskBlockError):
    """Account has hit the maximum allowed drawdown."""


class DailyLossLimitError(RiskBlockError):
    """Daily loss limit has been reached."""


# ─── Plugin Errors ───────────────────────────────────────────────────────────
class PluginLoadError(FrameworkError):
    """A strategy plugin could not be loaded (import failure, etc.)."""


class PluginMetadataError(PluginLoadError):
    """Strategy plugin has missing or invalid metadata fields."""


class PluginConfigError(PluginLoadError):
    """
    Strategy plugin config YAML has missing required fields,
    wrong types, or out-of-range values.

    Attributes:
        strategy_name: name of the offending strategy
        field:         the config key that failed
    """
    def __init__(self, message: str, strategy_name: str = "", field: str = ""):
        super().__init__(message)
        self.strategy_name = strategy_name
        self.field         = field


class PluginConflictError(PluginLoadError):
    """
    Two or more plugins conflict — e.g. duplicate magic_offset or name.
    """


# ─── Configuration Errors ────────────────────────────────────────────────────
class ConfigError(FrameworkError):
    """A required configuration key is missing or invalid."""


# ─── State / Persistence Errors ──────────────────────────────────────────────
class StateError(FrameworkError):
    """Error reading or writing state store."""
