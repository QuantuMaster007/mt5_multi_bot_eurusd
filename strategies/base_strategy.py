"""
BaseStrategy — abstract base for all strategy plugins.

Every strategy MUST:
  1. Subclass BaseStrategy
  2. Define a ``metadata`` ClassVar of type StrategyMetadata
  3. Implement ``generate_signal()``

Every strategy SHOULD:
  4. Define a ``CONFIG_SCHEMA`` ClassVar for validated config access
  5. Override ``prepare_indicators()`` to compute indicators
  6. Override ``manage_open_positions()`` if active exit management is needed

Quickstart — copy this pattern::

    from typing import ClassVar, Dict, Any, List, Optional
    from strategies.base_strategy import (
        BaseStrategy, StrategyMetadata, TradeIntent
    )
    from orchestration.plugin_validator import ConfigField

    class MyStrategy(BaseStrategy):

        metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
            name        = "my_strategy",
            version     = "1.0.0",
            description = "What this strategy does",
            symbols     = ["EURUSD"],
            timeframes  = ["M15"],
            regime_tags = ["ranging"],
            magic_offset= 400,
        )

        CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
            "fast_ema": ConfigField(int,   required=True,  default=9,   description="Fast EMA period"),
            "slow_ema": ConfigField(int,   required=True,  default=21,  description="Slow EMA period"),
        }

        def prepare_indicators(self, df) -> Dict[str, Any]:
            return {}   # compute and return indicator values

        def generate_signal(self, df, indicators, regimes, spread_pips, tick):
            return None   # return TradeIntent or None
"""
from __future__ import annotations

import abc
import traceback
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from core.broker_profile import broker_profile, SymbolProfile
from core.constants import (
    SIDE_BUY, SIDE_SELL,
    STATE_ENABLED, STATE_BLOCKED, STATE_PAUSED,
    EVT_ENTRY_BLOCKED,
)
from core.exceptions import MT5DataError, RiskBlockError
from core.execution_engine import execution_engine, ExecutionResult
from core.heartbeat import Heartbeat
from core.json_logger import get_event_logger, get_trade_logger
from core.logger import get_logger
from core.market_data import market_data
from core.metrics_store import metrics_store
from core.mt5_connector import connector
from core.order_validator import OrderIntent
from core.portfolio_manager import portfolio_manager
from core.regime_detector import regime_detector
from core.risk_manager import risk_manager
from core.session_filter import session_filter
from core.settings import settings
from core.state_store import StateStore
from core.utils import ts_now
from orchestration.policy_engine import policy_engine


# ─── Plugin metadata ─────────────────────────────────────────────────────────

@dataclass
class StrategyMetadata:
    """
    Declares strategy capabilities.

    All fields are validated by plugin_validator.PluginValidator before
    the plugin is accepted. Required fields: name, version, description,
    symbols, timeframes, regime_tags.

    Attributes:
        name:         Unique snake_case identifier (e.g. "momentum_breakout").
                      Must match the config/strategies/<name>.yaml filename.
        version:      Semver string (e.g. "1.0.0").
        description:  One-sentence description of the strategy's logic.
        symbols:      List of symbols this strategy supports (e.g. ["EURUSD"]).
        timeframes:   List of MT5 timeframe strings (e.g. ["M15", "H1"]).
        regime_tags:  Market regimes this strategy is COMPATIBLE with.
                      Used by the policy engine's regime gating rules.
                      Values: "ranging" | "trending" | "breakout" | etc.
        risk_profile: "low" | "medium" | "high"
        author:       Optional author name for documentation.
        magic_offset: Unique integer 1–9999 added to magic_base to form
                      the MT5 magic number. Must be unique across all plugins.
                      Also set in config YAML — the config value takes precedence.
    """
    name:         str
    version:      str
    description:  str
    symbols:      List[str]
    timeframes:   List[str]
    regime_tags:  List[str]
    risk_profile: str = "medium"
    author:       str = "unknown"
    magic_offset: int = 0


# ─── Trade intent ─────────────────────────────────────────────────────────────

@dataclass
class TradeIntent:
    """
    A strategy's desire to open a position.

    Returned by generate_signal(). The base class sends this through
    risk, portfolio, and execution checks before actually placing the order.

    Attributes:
        strategy:    Strategy name (set automatically).
        symbol:      Trading symbol (e.g. "EURUSD").
        side:        "buy" or "sell".
        entry_price: Expected fill price. Used for logging only; actual fill
                     price is fetched fresh at send time.
        sl:          Stop-loss price (0 = no SL, not recommended).
        tp:          Take-profit price (0 = no TP).
        volume:      Lot size computed by risk_manager.compute_lot_size().
        reason_code: Short snake_case label for this signal (used in logs
                     and weekly reports, e.g. "bb_bounce_long").
        notes:       Optional free-text debug context (indicator values, etc.)
    """
    strategy:    str
    symbol:      str
    side:        str
    entry_price: float
    sl:          float
    tp:          float
    volume:      float
    reason_code: str
    notes:       str = ""
    timestamp:   str = field(default_factory=ts_now)


# ─── BaseStrategy ─────────────────────────────────────────────────────────────

class BaseStrategy(abc.ABC):
    """
    Abstract base for all strategy plugins.

    The orchestrator calls run_cycle() once per loop interval.
    Do NOT sleep inside run_cycle() — the orchestrator controls timing.

    Template for a minimal strategy::

        class MyStrategy(BaseStrategy):
            metadata: ClassVar[StrategyMetadata] = StrategyMetadata(...)
            CONFIG_SCHEMA: ClassVar[Dict] = {...}   # optional but recommended

            def generate_signal(self, df, indicators, regimes, spread_pips, tick):
                # return TradeIntent(...) or None
                return None
    """

    # Required: every subclass must define this
    metadata: ClassVar[StrategyMetadata]

    # Optional: define this for automatic config validation at load time
    # See orchestration/plugin_validator.py — ConfigField for field definitions
    CONFIG_SCHEMA: ClassVar[Optional[Dict]] = None

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def __init__(self) -> None:
        name          = self.metadata.name
        self._log     = get_logger(f"strategy.{name}")
        self._cfg: Dict[str, Any] = settings.strategy_config(name)
        self._state   = StateStore(name)
        self._hb      = Heartbeat(name)
        self._el      = get_event_logger()
        self._tl      = get_trade_logger()
        self._symbol: str     = self._cfg.get("symbol", "EURUSD")
        self._timeframe: str  = self._cfg.get("timeframe", "M15")
        self._status: str     = STATE_ENABLED
        self._loop_count: int = 0
        self._last_error: Optional[str] = None
        self._magic: int = (
            settings.execution.get("magic_base", 200000)
            + self._cfg.get("magic_offset", self.metadata.magic_offset)
        )

        # Register symbol for reconnect re-subscription
        connector.add_watched_symbol(self._symbol)

        self._log.info(
            "Initialised | name=%s version=%s symbol=%s tf=%s magic=%d",
            name, self.metadata.version,
            self._symbol, self._timeframe, self._magic,
        )

    # ─── Config access helpers ────────────────────────────────────────────

    def cfg_int(self, key: str, default: int = 0) -> int:
        """Get a config value as int with a fallback default."""
        return int(self._cfg.get(key, default))

    def cfg_float(self, key: str, default: float = 0.0) -> float:
        """Get a config value as float with a fallback default."""
        return float(self._cfg.get(key, default))

    def cfg_str(self, key: str, default: str = "") -> str:
        """Get a config value as str with a fallback default."""
        return str(self._cfg.get(key, default))

    def cfg_bool(self, key: str, default: bool = False) -> bool:
        """Get a config value as bool with a fallback default."""
        val = self._cfg.get(key, default)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "yes", "1")

    # ─── Main cycle entry point ───────────────────────────────────────────

    def run_cycle(self) -> None:
        """
        Called every loop iteration by BotRunner.
        Catches all exceptions — never propagates.
        """
        self._loop_count += 1
        try:
            self._do_run_cycle()
        except Exception as exc:
            self._last_error = str(exc)
            self._log.error(
                "Unhandled exception in %s run_cycle: %s\n%s",
                self.metadata.name, exc, traceback.format_exc(),
            )
        finally:
            self._hb.beat(
                status=self._status,
                last_error=self._last_error,
                open_positions=self.get_open_position_count(),
            )

    def _do_run_cycle(self) -> None:
        # 1. Session filter
        allowed_sessions = self._cfg.get("allowed_sessions", None)
        if not session_filter.is_tradeable_now(
            allowed_sessions=allowed_sessions,
            strategy_name=self.metadata.name,
        ):
            return

        # 2. Single tick fetch — reused throughout the cycle
        tick = market_data.get_tick(self._symbol)
        if tick is None:
            self._log.warning("No valid tick — skipping cycle")
            return

        # 3. Fetch bars with reconnect on error
        try:
            df = market_data.get_rates(self._symbol, self._timeframe, count=200)
        except MT5DataError as exc:
            self._log.error("MT5DataError: %s — attempting reconnect", exc)
            self._last_error = str(exc)
            connector.reconnect()
            return

        profile     = broker_profile.get_symbol_profile(self._symbol)
        spread_pips = market_data.get_spread_pips(
            self._symbol, profile.pip_value, tick=tick
        )

        # 4. Prepare indicators
        indicators = self.prepare_indicators(df)

        # 5. Detect regime
        regimes = regime_detector.detect_multiple(df, spread_pips=spread_pips)
        self._log.debug("Regimes=%s spread=%.2fpips", regimes, spread_pips)

        # 6. Policy gate
        decision = policy_engine.evaluate(
            strategy_name=self.metadata.name,
            regimes=regimes,
            spread_pips=spread_pips,
        )
        if decision.state != STATE_ENABLED:
            self._status = decision.state
            self._log.info(
                "Policy %s: %s [%s]",
                decision.state.upper(), decision.reason, decision.reason_code,
            )
            metrics_store.record_policy_block(self.metadata.name)
            return
        self._status = STATE_ENABLED

        # 7. Manage open positions (subclass hook)
        self.manage_open_positions(df, indicators, profile, tick)

        # 8. Generate signal (subclass hook)
        signal = self.generate_signal(df, indicators, regimes, spread_pips, tick)
        if signal is None:
            self._log.debug("No signal")
            return

        self._el.write({
            "event":    "signal",
            "strategy": self.metadata.name,
            "symbol":   self._symbol,
            "side":     signal.side,
            "reason":   signal.reason_code,
            "regimes":  regimes,
            "spread":   spread_pips,
            "notes":    signal.notes,
            "ts":       ts_now(),
        })

        # 9. Account state
        acct = connector.account_info()
        if acct is None:
            self._log.warning("No account info — skipping entry")
            return

        # 10. Risk check
        try:
            risk_manager.check_can_trade(
                symbol=self._symbol,
                spread_pips=spread_pips,
                strategy_name=self.metadata.name,
                account_balance=acct.balance,
                account_equity=acct.equity,
            )
        except Exception as exc:
            self._log.info("Risk block: %s", exc)
            metrics_store.record_risk_block(self.metadata.name)
            return

        # 11. Portfolio check
        try:
            portfolio_manager.check_can_open(
                symbol=self._symbol,
                side=signal.side,
                volume=signal.volume,
                strategy_name=self.metadata.name,
                strategy_magic=self._magic,
            )
        except Exception as exc:
            self._log.info("Portfolio block: %s", exc)
            metrics_store.record_risk_block(self.metadata.name)
            return

        # 12. Execute
        self._execute_intent(signal, profile, acct.balance)

    # ─── Abstract methods — implement in your strategy ────────────────────

    @abc.abstractmethod
    def generate_signal(
        self,
        df,
        indicators: Dict[str, Any],
        regimes: List[str],
        spread_pips: float,
        tick: dict,
    ) -> Optional[TradeIntent]:
        """
        Core signal logic. Called every cycle if all gates pass.

        Args:
            df:          OHLCV DataFrame, oldest→newest, UTC timestamps.
                         Columns: time, open, high, low, close, tick_volume
            indicators:  Dict returned by prepare_indicators()
            regimes:     List of active regime strings (e.g. ["ranging"])
            spread_pips: Current spread in pips (from pre-fetched tick)
            tick:        Dict with keys: time, bid, ask, last, volume

        Returns:
            TradeIntent if a signal fires, None otherwise.
            Do NOT call execution_engine here — return intent only.

        Example::

            close = float(df["close"].iloc[-1])
            if close < indicators["bb_lower"].iloc[-1]:
                return TradeIntent(
                    strategy=self.metadata.name,
                    symbol=self._symbol,
                    side="buy",
                    entry_price=tick["ask"],
                    sl=tick["ask"] - 0.0015,
                    tp=tick["ask"] + 0.0030,
                    volume=self._size_lots(0.0015),
                    reason_code="bb_lower_touch",
                )
            return None
        """
        ...

    def prepare_indicators(self, df) -> Dict[str, Any]:
        """
        Compute strategy-specific indicators.
        Override to pre-compute values used by generate_signal().

        Returns a dict of any values (pandas Series, floats, etc.).
        Return {} if you compute everything inline in generate_signal().

        Example::

            import ta
            close = df["close"]
            return {
                "rsi": ta.momentum.RSIIndicator(close, 14).rsi(),
                "atr": ta.volatility.AverageTrueRange(
                    df["high"], df["low"], close, 14
                ).average_true_range(),
            }
        """
        return {}

    def manage_open_positions(
        self,
        df,
        indicators: Dict[str, Any],
        profile: SymbolProfile,
        tick: dict,
    ) -> None:
        """
        Check and manage any open positions for this strategy.
        Called every cycle before signal generation.

        Override to implement:
          - Trailing stops
          - Time-based exits
          - Partial take-profits
          - Breakeven moves

        Example — move SL to breakeven when halfway to TP::

            for pos in self.get_open_positions():
                if pos.tp and pos.sl:
                    midpoint = (pos.price_open + pos.tp) / 2
                    current  = tick["bid"] if pos.type == 0 else tick["ask"]
                    if pos.type == 0 and current >= midpoint and pos.sl < pos.price_open:
                        # Move SL to breakeven
                        ...
        """
        pass

    # ─── Execution helper ─────────────────────────────────────────────────

    def _execute_intent(
        self,
        intent: TradeIntent,
        profile: SymbolProfile,
        balance: float,
    ) -> None:
        order = OrderIntent(
            symbol=intent.symbol,
            side=intent.side,
            volume=intent.volume,
            entry_price=intent.entry_price,
            sl=intent.sl,
            tp=intent.tp,
            comment=intent.reason_code[:31],
            magic=self._magic,
        )

        self._log.info(
            "→ %s %s  vol=%.2f  sl=%.5f  tp=%.5f  [%s]",
            intent.side.upper(), intent.symbol,
            intent.volume, intent.sl, intent.tp, intent.reason_code,
        )

        result: ExecutionResult = execution_engine.send_market_order(order)

        # Record exec attempt ONCE after result is known
        metrics_store.record_exec_attempt(
            self.metadata.name, success=result.success
        )

        if result.success:
            risk_manager.record_trade_open()
            self._tl.write({
                "event":    "trade_open",
                "strategy": self.metadata.name,
                "symbol":   intent.symbol,
                "side":     intent.side,
                "volume":   result.volume_filled,
                "price":    result.fill_price,
                "sl":       intent.sl,
                "tp":       intent.tp,
                "ticket":   result.order_id,
                "magic":    self._magic,
                "reason":   intent.reason_code,
                "notes":    intent.notes,
                "ts":       ts_now(),
            })
            self._log.info(
                "✓ Opened ticket=%d  price=%.5f  vol=%.2f",
                result.order_id, result.fill_price, result.volume_filled,
            )
        else:
            self._log.warning(
                "✗ Failed category=%s  %s",
                result.category, result.error_description,
            )
            self._el.write({
                "event":    EVT_ENTRY_BLOCKED,
                "strategy": self.metadata.name,
                "symbol":   intent.symbol,
                "side":     intent.side,
                "reason":   result.error_description,
                "category": result.category,
                "ts":       ts_now(),
            })

    # ─── Lot sizing helper — call from generate_signal() ─────────────────

    def _size_lots(
        self,
        sl_distance_price: float,
        risk_fraction: Optional[float] = None,
    ) -> float:
        """
        Compute lot size so that a full SL hit costs risk_fraction
        of account balance. Returns 0.0 if account info is unavailable.

        Args:
            sl_distance_price: Absolute price distance of SL from entry.
                               e.g. if entry=1.0800 and sl=1.0770,
                               sl_distance_price = 0.0030
            risk_fraction:     Override for risk per trade (0.005 = 0.5%).
                               If None, uses risk_config default.

        Example::

            entry = tick["ask"]
            sl    = entry - 20 * profile.pip_value    # 20-pip SL
            lots  = self._size_lots(abs(entry - sl))
        """
        acct = connector.account_info()
        if acct is None or acct.balance <= 0:
            return 0.0

        profile  = broker_profile.get_symbol_profile(self._symbol)
        pip_val  = profile.pip_value
        sl_pips  = sl_distance_price / pip_val if pip_val > 0 else 0.0

        if sl_pips <= 0:
            return 0.0

        return risk_manager.compute_lot_size(
            acct.balance, sl_pips, profile, risk_fraction
        )

    # ─── Position queries ─────────────────────────────────────────────────

    def get_open_positions(self):
        """Return live MT5 positions belonging to this strategy."""
        all_pos = connector.positions_get(symbol=self._symbol)
        return [p for p in all_pos if p.magic == self._magic]

    def get_open_position_count(self) -> int:
        return len(self.get_open_positions())

    def has_open_position(self) -> bool:
        return self.get_open_position_count() > 0

    # ─── Properties ──────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return self._status == STATE_ENABLED

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def magic(self) -> int:
        return self._magic

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def timeframe(self) -> str:
        return self._timeframe
