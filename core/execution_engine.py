"""
Execution Engine

The ONLY place in the framework that calls order_send().

FIX E1: order_check() HARD failures abort before order_send().
FIX E2: close_position() validates volume and checks freeze_level.
FIX E3: Fill mode exhaustion tracks already-tried modes.
FIX E4: retcode 0 from order_check treated as success.
FIX E5: Cooldowns keyed by (symbol, magic), not symbol alone.
FIX E6: Single authoritative tick fetch — freshness checked from the
         same tick object used for pricing.  Previously two separate
         connector calls were made (one via market_data.is_tick_fresh,
         one directly), causing test-mock isolation failures and a
         theoretical race window in live trading.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from core.broker_profile import broker_profile, SymbolProfile
from core.constants import (
    SIDE_BUY, SIDE_SELL,
    ORDER_TYPE_BUY, ORDER_TYPE_SELL,
    RETCODE_OK, RETCODE_PLACED,
    EVT_ORDER_SENT, EVT_ORDER_FILLED, EVT_ORDER_REJECTED,
    FILL_ERROR_COMMENT_PATTERNS,
)
from core.exceptions import OrderValidationError
from core.json_logger import get_event_logger
from core.logger import get_logger
from core.market_data import MAX_TICK_AGE_SECONDS   # constant only — no market_data calls here
from core.mt5_connector import connector
from core.order_validator import OrderIntent, order_validator
from core import retcode_mapper
from core.retcode_mapper import RetcodeCategory
from core.settings import settings
from core.utils import ts_now

log = get_logger("execution_engine")


@dataclass
class ExecutionResult:
    success:           bool
    retcode:           int   = 0
    order_id:          int   = 0
    volume_filled:     float = 0.0
    fill_price:        float = 0.0
    comment:           str   = ""
    error_description: str   = ""
    category:          str   = ""
    request_snapshot:  Dict[str, Any] = field(default_factory=dict)


class ExecutionEngine:
    """
    Broker-aware, fill-mode-safe order executor.
    The ONLY place in the framework that calls order_send().
    """

    def __init__(self) -> None:
        self._cfg = settings.execution
        # FIX E5: Cooldown keyed by (symbol, magic) not symbol alone
        self._cooldowns: Dict[tuple, float] = {}
        self._event_log = None

    @property
    def _el(self):
        if self._event_log is None:
            self._event_log = get_event_logger()
        return self._event_log

    # ─── Public API ──────────────────────────────────────────────────────

    def send_market_order(self, intent: OrderIntent) -> ExecutionResult:
        """
        Validate, pre-check, and send a market order.
        Never raises — all outcomes returned as ExecutionResult.
        """
        cooldown_key = (intent.symbol, intent.magic)
        if self._is_in_cooldown(cooldown_key):
            remaining = self._cooldown_remaining(cooldown_key)
            msg = (
                f"{intent.symbol} magic={intent.magic} in cooldown "
                f"for {remaining:.0f}s after hard rejection"
            )
            log.warning(msg)
            return ExecutionResult(success=False, comment=msg, category="cooldown")

        profile = broker_profile.get_symbol_profile(intent.symbol)

        # FIX E6: Fetch ONE tick; validate freshness; use for pricing.
        # This replaces the previous two-step pattern where
        # market_data.is_tick_fresh() fetched a tick independently and
        # connector.symbol_info_tick() fetched a second one.  Using a
        # single fetch eliminates the mock-isolation failure in tests and
        # the theoretical race window in live trading.
        tick = self._get_fresh_tick(intent.symbol)
        if isinstance(tick, ExecutionResult):
            return tick  # stale or missing — error result already built
        fill_price = tick.ask if intent.side == SIDE_BUY else tick.bid

        # Validate order intent against broker profile
        try:
            intent = order_validator.validate(intent, profile, fill_price)
        except OrderValidationError as exc:
            log.error("Pre-validation failed: %s", exc)
            self._emit_rejected(intent, 0, str(exc), "validation_error")
            return ExecutionResult(
                success=False, comment=str(exc), category="validation_error"
            )

        request = self._build_request(intent, profile, fill_price)

        # FIX E1: order_check failures abort on HARD retcodes
        if self._cfg.get("pre_check_enabled", True):
            abort = self._run_order_check(intent, request)
            if abort is not None:
                return abort

        return self._send_with_retry(intent, request, profile)

    def close_position(
        self,
        symbol: str,
        position_ticket: int,
        volume: float,
        magic: int = 0,
        comment: str = "close",
    ) -> ExecutionResult:
        """
        FIX E2: Validates volume and checks freeze_level before closing.
        """
        profile = broker_profile.get_symbol_profile(symbol)
        tick    = connector.symbol_info_tick(symbol)
        if tick is None:
            return ExecutionResult(
                success=False, comment="No tick for close", category="data_error"
            )

        positions = connector.positions_get(symbol=symbol)
        pos = next((p for p in positions if p.ticket == position_ticket), None)
        if pos is None:
            return ExecutionResult(
                success=False,
                comment=f"Position {position_ticket} not found",
                category="not_found",
            )

        # FIX E2: Check freeze_level for existing position modification
        if profile.freeze_level > 0:
            current_price = tick.bid if pos.type == 0 else tick.ask
            pos_price     = pos.price_open
            price_dist    = abs(current_price - pos_price) / profile.point
            if price_dist < profile.freeze_level:
                msg = (
                    f"Position {position_ticket} is within freeze_level "
                    f"({price_dist:.0f} < {profile.freeze_level} points) — "
                    f"broker will reject modification/close"
                )
                log.warning(msg)
                return ExecutionResult(
                    success=False, comment=msg, category="freeze_level"
                )

        if pos.type == 0:   # POSITION_TYPE_BUY → sell to close
            close_side = SIDE_SELL
            fill_price = tick.bid
        else:
            close_side = SIDE_BUY
            fill_price = tick.ask

        close_intent = OrderIntent(
            symbol=symbol,
            side=close_side,
            volume=volume,
            entry_price=fill_price,
            comment=comment,
            magic=magic,
        )

        # FIX E2: validate volume on close as well
        try:
            close_intent = order_validator.validate(close_intent, profile, fill_price)
        except OrderValidationError as exc:
            log.error("Close validation failed ticket=%d: %s", position_ticket, exc)
            return ExecutionResult(
                success=False, comment=str(exc), category="validation_error"
            )

        request = self._build_request(close_intent, profile, fill_price, position=position_ticket)
        return self._send_with_retry(close_intent, request, profile)

    # ─── Tick helper ─────────────────────────────────────────────────────

    def _get_fresh_tick(self, symbol: str):
        """
        Fetch one tick via the connector and validate its age.

        Returns the raw tick object if fresh, or an ExecutionResult
        error if the tick is missing or stale.

        This is the single authoritative tick source for the entire
        send_market_order() path.  No second fetch is performed.
        """
        raw = connector.symbol_info_tick(symbol)
        if raw is None:
            msg = f"No tick data for {symbol}"
            log.warning(msg)
            return ExecutionResult(success=False, comment=msg, category="data_error")

        age = time.time() - raw.time
        if age > MAX_TICK_AGE_SECONDS:
            msg = (
                f"Stale tick for {symbol} — "
                f"age={age:.1f}s > max={MAX_TICK_AGE_SECONDS}s"
            )
            log.warning(msg)
            return ExecutionResult(success=False, comment=msg, category="stale_tick")

        return raw

    # ─── order_check ─────────────────────────────────────────────────────

    def _run_order_check(
        self, intent: OrderIntent, request: Dict[str, Any]
    ) -> Optional[ExecutionResult]:
        """
        FIX E1: Run order_check and return a failure ExecutionResult if
        the check returns a HARD retcode. Return None if OK to proceed.
        """
        check = connector.order_check(request)
        if check is None:
            log.debug("order_check returned None (stub or MT5 issue) — proceeding")
            return None

        # Retcode 0 and RETCODE_OK (10009) both mean "check passed"
        if check.retcode in (0, RETCODE_OK, RETCODE_PLACED):
            log.debug("order_check passed | retcode=%d", check.retcode)
            return None

        cat, desc = retcode_mapper.classify(check.retcode)

        if cat == RetcodeCategory.TRANSIENT:
            log.info(
                "order_check transient retcode=%d (%s) — proceeding to send",
                check.retcode, desc,
            )
            return None

        # HARD / FILL_ERROR / UNKNOWN — abort
        log.error(
            "order_check HARD FAIL | retcode=%d desc=%s | aborting send",
            check.retcode, desc,
        )
        self._emit_rejected(intent, check.retcode, f"order_check: {desc}", "precheck_hard")
        self._apply_cooldown((intent.symbol, intent.magic))
        return ExecutionResult(
            success=False,
            retcode=check.retcode,
            error_description=f"order_check failed: {desc}",
            category="precheck_hard",
        )

    # ─── Request building ─────────────────────────────────────────────────

    def _build_request(
        self,
        intent: OrderIntent,
        profile: SymbolProfile,
        fill_price: float,
        position: int = 0,
    ) -> Dict[str, Any]:
        fill_mode  = intent.fill_mode if intent.fill_mode is not None else profile.preferred_fill_mode
        order_type = ORDER_TYPE_BUY if intent.side == SIDE_BUY else ORDER_TYPE_SELL

        req: Dict[str, Any] = {
            "action":       1,               # TRADE_ACTION_DEAL
            "symbol":       intent.symbol,
            "volume":       intent.volume,
            "type":         order_type,
            "price":        fill_price,
            "deviation":    int(self._cfg.get("deviation_points", 20)),
            "magic":        intent.magic,
            "comment":      intent.comment[:31],
            "type_time":    0,               # ORDER_TIME_GTC
            "type_filling": fill_mode,
        }
        if intent.sl:
            req["sl"] = intent.sl
        if intent.tp:
            req["tp"] = intent.tp
        if position:
            req["position"] = position

        return req

    # ─── Send with retry ─────────────────────────────────────────────────

    def _send_with_retry(
        self,
        intent: OrderIntent,
        request: Dict[str, Any],
        profile: SymbolProfile,
    ) -> ExecutionResult:
        max_retries  = int(self._cfg.get("transient_retry_count", 2))
        retry_delay  = float(self._cfg.get("transient_retry_delay_seconds", 1))
        cooldown_key = (intent.symbol, intent.magic)

        # FIX E3: Track tried fill modes to prevent cyclic alternation
        tried_fill_modes: Set[int] = set()

        last_result = None

        for attempt in range(max_retries + 1):
            log.info(
                "order_send %d/%d | %s %s vol=%.2f magic=%d fill=%s",
                attempt + 1, max_retries + 1,
                intent.side.upper(), intent.symbol,
                intent.volume, intent.magic,
                request.get("type_filling"),
            )
            self._el.write({
                "event":   EVT_ORDER_SENT,
                "attempt": attempt + 1,
                "symbol":  intent.symbol,
                "side":    intent.side,
                "volume":  intent.volume,
                "magic":   intent.magic,
                "fill":    request.get("type_filling"),
            })

            result = connector.order_send(request)
            last_result = result

            if result is None:
                err = connector.last_error()
                log.error("order_send returned None | error=%s", err)
                time.sleep(retry_delay)
                continue

            cat, desc = retcode_mapper.classify(result.retcode)

            if retcode_mapper.is_success(result.retcode):
                log.info(
                    "Filled | ticket=%d price=%.5f vol=%.2f",
                    result.order, result.price, result.volume,
                )
                self._el.write({
                    "event":   EVT_ORDER_FILLED,
                    "ticket":  result.order,
                    "symbol":  intent.symbol,
                    "side":    intent.side,
                    "volume":  result.volume,
                    "price":   result.price,
                    "retcode": result.retcode,
                    "magic":   intent.magic,
                    "ts":      ts_now(),
                })
                return ExecutionResult(
                    success=True,
                    retcode=result.retcode,
                    order_id=result.order,
                    volume_filled=result.volume,
                    fill_price=result.price,
                    comment=desc,
                    category=cat.value,
                    request_snapshot=request,
                )

            # FIX E3: Detect fill-mode error and try next UNTRIED mode
            if cat == RetcodeCategory.FILL_ERROR or self._looks_like_fill_error(result):
                tried_fill_modes.add(request["type_filling"])
                new_fill = self._next_untried_fill_mode(tried_fill_modes, profile)
                if new_fill is not None:
                    log.warning(
                        "Fill mode %s rejected; retrying with %s",
                        request["type_filling"], new_fill,
                    )
                    request["type_filling"] = new_fill
                    continue
                # All fill modes exhausted
                self._apply_cooldown(cooldown_key)
                self._emit_rejected(intent, result.retcode, desc, "fill_error")
                return ExecutionResult(
                    success=False, retcode=result.retcode,
                    error_description=f"All fill modes exhausted: {desc}",
                    category="fill_error",
                )

            if cat == RetcodeCategory.TRANSIENT and attempt < max_retries:
                log.warning(
                    "Transient retcode=%d (%s) — retry in %.1fs",
                    result.retcode, desc, retry_delay,
                )
                # Re-fetch price for retry attempt only
                retry_tick = connector.symbol_info_tick(intent.symbol)
                if retry_tick:
                    request["price"] = retry_tick.ask if intent.side == SIDE_BUY else retry_tick.bid
                time.sleep(retry_delay)
                continue

            # Hard rejection
            log.error(
                "Hard rejection | retcode=%d desc=%s | %s %s",
                result.retcode, desc, intent.side, intent.symbol,
            )
            self._apply_cooldown(cooldown_key)
            self._emit_rejected(intent, result.retcode, desc, cat.value)
            return ExecutionResult(
                success=False, retcode=result.retcode,
                error_description=desc, category=cat.value,
                request_snapshot=request,
            )

        rc = last_result.retcode if last_result else 0
        return ExecutionResult(
            success=False, retcode=rc,
            error_description="Max retries exhausted",
            category="retry_exhausted",
        )

    # ─── Fill mode helpers ────────────────────────────────────────────────

    @staticmethod
    def _looks_like_fill_error(result: Any) -> bool:
        """
        MT5 signals unsupported fill mode as RETCODE_REJECT (10006) with
        a comment string. Check the comment for known fill-error patterns.
        """
        comment = getattr(result, "comment", "") or ""
        comment_lower = comment.lower()
        return any(pat in comment_lower for pat in FILL_ERROR_COMMENT_PATTERNS)

    @staticmethod
    def _next_untried_fill_mode(
        tried: Set[int], profile: SymbolProfile
    ) -> Optional[int]:
        """FIX E3: Return next supported fill mode not yet tried."""
        from core.constants import FILL_IOC, FILL_FOK, FILL_RETURN
        priority = [FILL_IOC, FILL_FOK, FILL_RETURN]
        for mode in priority:
            if mode in profile.supported_fill_modes and mode not in tried:
                return mode
        return None

    # ─── Cooldown ────────────────────────────────────────────────────────

    def _apply_cooldown(self, key: tuple) -> None:
        seconds = float(self._cfg.get("hard_reject_cooldown_seconds", 60))
        self._cooldowns[key] = time.monotonic() + seconds
        log.warning(
            "Cooldown %.0fs | symbol=%s magic=%s", seconds, key[0], key[1]
        )

    def _is_in_cooldown(self, key: tuple) -> bool:
        return time.monotonic() < self._cooldowns.get(key, 0)

    def _cooldown_remaining(self, key: tuple) -> float:
        return max(0.0, self._cooldowns.get(key, 0) - time.monotonic())

    # ─── Event helpers ────────────────────────────────────────────────────

    def _emit_rejected(
        self, intent: OrderIntent, retcode: int, desc: str, category: str
    ) -> None:
        self._el.write({
            "event":       EVT_ORDER_REJECTED,
            "symbol":      intent.symbol,
            "side":        intent.side,
            "volume":      intent.volume,
            "magic":       intent.magic,
            "retcode":     retcode,
            "description": desc,
            "category":    category,
            "ts":          ts_now(),
        })


# Module-level singleton
execution_engine = ExecutionEngine()
