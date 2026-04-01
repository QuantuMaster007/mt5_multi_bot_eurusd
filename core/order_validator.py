"""
Order Validator

FIX V1: freeze_level is documented. It applies to modifications of
         existing positions, not to new order placement. For new orders,
         only stops_level applies. close_position() in execution_engine
         must check freeze_level separately (see that module).
FIX V2: Stop validation now uses the actual fill price (ask for buys,
         bid for sells) as the reference, not the mid-price passed in.
         This matches how MT5 measures SL/TP distance server-side.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from core.broker_profile import SymbolProfile
from core.constants import SIDE_BUY, SIDE_SELL, FILL_MODE_NAMES
from core.exceptions import (
    OrderValidationError,
    VolumeError,
    StopLevelError,
    InvalidFillModeError,
)
from core.logger import get_logger
from core.utils import round_to_step

log = get_logger("order_validator")


@dataclass
class OrderIntent:
    """
    Symbol-agnostic order request.

    sl=0 and tp=0 mean no stop/TP requested.
    fill_mode=None → execution engine uses profile.preferred_fill_mode.
    """
    symbol:      str
    side:        str          # "buy" | "sell"
    volume:      float
    entry_price: float        # 0 → pure market order (price set at send time)
    sl:          float = 0.0
    tp:          float = 0.0
    comment:     str   = ""
    magic:       int   = 0
    fill_mode:   Optional[int] = None


class OrderValidator:
    """
    Validates an OrderIntent against broker/symbol constraints.
    Returns normalised OrderIntent on success; raises on failure.
    """

    def validate(
        self,
        intent: OrderIntent,
        profile: SymbolProfile,
        fill_price: float,
    ) -> OrderIntent:
        """
        Validate and normalise *intent*.

        Args:
            intent:      The raw order intent.
            profile:     Broker/symbol profile.
            fill_price:  Expected fill price (ask for buys, bid for sells).
                         Used as reference for SL/TP distance checks.
                         FIX V2: must be the correct side price, not mid.

        Returns:
            Normalised OrderIntent (volume aligned to step).

        Raises:
            VolumeError, StopLevelError, InvalidFillModeError,
            OrderValidationError on any failure.
        """
        intent = self._validate_volume(intent, profile)
        self._validate_stops(intent, profile, fill_price)
        self._validate_fill_mode(intent, profile)
        return intent

    # ─── Volume ──────────────────────────────────────────────────────────

    def _validate_volume(self, intent: OrderIntent, profile: SymbolProfile) -> OrderIntent:
        raw     = intent.volume
        snapped = round_to_step(raw, profile.volume_step)

        if snapped <= 0 or snapped < profile.volume_min:
            raise VolumeError(
                f"Computed volume {raw:.5f} rounds to {snapped:.5f} which is below "
                f"broker minimum {profile.volume_min} for {intent.symbol}. "
                f"Account balance or SL distance may be too small for this risk setting."
            )
        if snapped > profile.volume_max:
            raise VolumeError(
                f"Volume {raw:.5f} exceeds broker maximum {profile.volume_max} "
                f"for {intent.symbol}. Check lot-size cap in risk config."
            )

        if abs(snapped - raw) > 1e-9:
            log.debug(
                "Volume snapped %.5f → %.5f (step=%.5f) for %s",
                raw, snapped, profile.volume_step, intent.symbol,
            )

        return replace(intent, volume=snapped)

    # ─── Stops ───────────────────────────────────────────────────────────

    def _validate_stops(
        self,
        intent: OrderIntent,
        profile: SymbolProfile,
        fill_price: float,
    ) -> None:
        """
        FIX V2: validate SL/TP distance against fill_price (ask/bid),
        not mid-price. MT5 measures stop distance from the side-specific
        execution price.

        freeze_level NOTE: freeze_level is the zone around the CURRENT
        price within which stops on EXISTING positions cannot be modified.
        It does NOT apply to stop placement on NEW orders.
        For existing position management, check freeze_level separately.
        """
        if intent.sl == 0 and intent.tp == 0:
            return

        min_distance = profile.stops_level * profile.point

        if min_distance == 0:
            return  # broker has no minimum (rare)

        if intent.sl != 0:
            sl_dist = abs(fill_price - intent.sl)
            if sl_dist < min_distance:
                raise StopLevelError(
                    f"{intent.symbol} SL distance {sl_dist:.5f} < "
                    f"required {min_distance:.5f} "
                    f"(stops_level={profile.stops_level} points from fill price {fill_price:.5f}). "
                    f"Widen your SL by at least "
                    f"{(min_distance - sl_dist) / profile.pip_value:.1f} pips."
                )

        if intent.tp != 0:
            tp_dist = abs(fill_price - intent.tp)
            if tp_dist < min_distance:
                raise StopLevelError(
                    f"{intent.symbol} TP distance {tp_dist:.5f} < "
                    f"required {min_distance:.5f} "
                    f"(stops_level={profile.stops_level} points from fill price {fill_price:.5f}). "
                    f"Widen your TP by at least "
                    f"{(min_distance - tp_dist) / profile.pip_value:.1f} pips."
                )

        # Direction sanity: SL must be on the losing side
        if intent.side == SIDE_BUY:
            if intent.sl != 0 and intent.sl >= fill_price:
                raise StopLevelError(
                    f"BUY SL {intent.sl:.5f} must be BELOW fill price {fill_price:.5f}"
                )
            if intent.tp != 0 and intent.tp <= fill_price:
                raise StopLevelError(
                    f"BUY TP {intent.tp:.5f} must be ABOVE fill price {fill_price:.5f}"
                )
        else:
            if intent.sl != 0 and intent.sl <= fill_price:
                raise StopLevelError(
                    f"SELL SL {intent.sl:.5f} must be ABOVE fill price {fill_price:.5f}"
                )
            if intent.tp != 0 and intent.tp >= fill_price:
                raise StopLevelError(
                    f"SELL TP {intent.tp:.5f} must be BELOW fill price {fill_price:.5f}"
                )

    # ─── Fill mode ───────────────────────────────────────────────────────

    def _validate_fill_mode(
        self,
        intent: OrderIntent,
        profile: SymbolProfile,
    ) -> None:
        requested = intent.fill_mode
        if requested is None:
            return  # execution engine will use profile.preferred_fill_mode

        if profile.supported_fill_modes and requested not in profile.supported_fill_modes:
            raise InvalidFillModeError(
                f"Fill mode {FILL_MODE_NAMES.get(requested, requested)} "
                f"not supported for {intent.symbol}. "
                f"Supported: {[FILL_MODE_NAMES.get(m, m) for m in profile.supported_fill_modes]}",
                retcode=10006,
            )


# Module-level singleton
order_validator = OrderValidator()
