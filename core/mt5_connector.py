"""
MT5 Connection Wrapper

Thread-safe, auto-reconnecting interface to the MetaTrader5 Python API.

FIX C1: reconnect() now calls broker_profile.refresh() for all known
         symbols and re-subscribes them to Market Watch.
FIX C2: reconnect() no longer propagates immediately — callers get a
         clear boolean result so they can wait-and-retry gracefully.
FIX C3: ensure_connected() is robust to stub mode.

WHY A SINGLETON?
MT5 Python API uses one global terminal connection per process.
Multiple threads CAN call it concurrently but we protect all calls
with a threading.Lock() to avoid interleaved request state.
"""
from __future__ import annotations

import threading
import time
from typing import Any, List, Optional, Tuple

from core.exceptions import MT5ConnectionError, MT5AuthError
from core.logger import get_logger

log = get_logger("mt5_connector")

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 package not installed — running in stub mode")


class MT5Connector:
    """
    Thread-safe wrapper around the MetaTrader5 Python API.

    All public methods acquire ``_lock`` before touching MT5.
    """

    def __init__(self) -> None:
        self._lock             = threading.Lock()
        self._connected        = False
        self._login: int       = 0
        self._password: str    = ""
        self._server: str      = ""
        self._path: str        = ""
        self._max_retries: int = 3
        self._retry_delay: float = 5.0
        # Symbols to re-subscribe after reconnect
        self._watched_symbols: List[str] = []

    # ─── Configuration ────────────────────────────────────────────────────

    def configure(
        self,
        login: int,
        password: str,
        server: str,
        path: str = "",
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        self._login        = login
        self._password     = password
        self._server       = server
        self._path         = path
        self._max_retries  = max_retries
        self._retry_delay  = retry_delay

    # ─── Connection lifecycle ─────────────────────────────────────────────

    def connect(self) -> None:
        """
        Initialise and authenticate.
        Raises MT5ConnectionError or MT5AuthError on failure.
        """
        if not MT5_AVAILABLE:
            log.warning("MT5 not available — connector in stub mode")
            self._connected = True
            return

        with self._lock:
            self._do_connect()

    def _do_connect(self) -> None:
        """Internal — must be called with lock held."""
        kwargs: dict = {}
        if self._path:
            kwargs["path"] = self._path

        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            raise MT5ConnectionError(f"mt5.initialize() failed: {err}")

        if self._login:
            ok = mt5.login(
                login=self._login,
                password=self._password,
                server=self._server,
            )
            if not ok:
                err = mt5.last_error()
                mt5.shutdown()
                raise MT5AuthError(f"mt5.login() failed: {err}")

        self._connected = True
        log.info(
            "MT5 connected | server=%s login=%s version=%s",
            self._server, self._login, mt5.version(),
        )

    def disconnect(self) -> None:
        if not MT5_AVAILABLE:
            return
        with self._lock:
            if self._connected:
                mt5.shutdown()
                self._connected = False
                log.info("MT5 disconnected")

    def reconnect(self) -> bool:
        """
        Attempt to reconnect with linear backoff.

        FIX C1/C2: Returns True on success, False on total failure.
        Does NOT raise — caller decides what to do.
        After success, invalidates broker profile cache and
        re-subscribes watched symbols.
        """
        if not MT5_AVAILABLE:
            return True

        log.info("Attempting MT5 reconnect...")
        for attempt in range(1, self._max_retries + 1):
            try:
                with self._lock:
                    try:
                        mt5.shutdown()
                    except Exception:
                        pass
                    self._connected = False
                    self._do_connect()

                log.info("MT5 reconnect succeeded on attempt %d", attempt)

                # Re-subscribe symbols to Market Watch
                self._resubscribe_symbols()

                # Invalidate broker profile cache so fresh data is loaded
                self._invalidate_broker_profiles()

                # Emit reconnect event
                self._emit_reconnect_event()
                return True

            except Exception as exc:
                log.warning("Reconnect attempt %d/%d failed: %s", attempt, self._max_retries, exc)
                time.sleep(self._retry_delay * attempt)

        log.error("All MT5 reconnect attempts exhausted")
        return False

    def _resubscribe_symbols(self) -> None:
        """Re-add watched symbols to Market Watch after reconnect."""
        if not MT5_AVAILABLE or not self._watched_symbols:
            return
        for sym in self._watched_symbols:
            try:
                mt5.symbol_select(sym, True)
                log.debug("Re-subscribed symbol: %s", sym)
            except Exception as exc:
                log.warning("Failed to re-subscribe %s: %s", sym, exc)

    def _invalidate_broker_profiles(self) -> None:
        """Clear broker profile cache so next access re-fetches from MT5."""
        try:
            from core.broker_profile import broker_profile
            for sym in self._watched_symbols:
                broker_profile.refresh(sym)
            log.info("Broker profiles refreshed after reconnect")
        except Exception as exc:
            log.warning("Broker profile refresh failed: %s", exc)

    def _emit_reconnect_event(self) -> None:
        try:
            from core.json_logger import get_event_logger
            from core.constants import EVT_MT5_RECONNECT
            from core.utils import ts_now
            get_event_logger().write({"event": EVT_MT5_RECONNECT, "ts": ts_now()})
        except Exception:
            pass

    def add_watched_symbol(self, symbol: str) -> None:
        """Register a symbol for Market Watch re-subscription after reconnect."""
        if symbol not in self._watched_symbols:
            self._watched_symbols.append(symbol)

    @property
    def connected(self) -> bool:
        return self._connected

    def ensure_connected(self) -> None:
        """FIX C3: Guard before any data call."""
        if not MT5_AVAILABLE:
            return
        if not self._connected:
            success = self.reconnect()
            if not success:
                from core.exceptions import MT5ConnectionError
                raise MT5ConnectionError("Could not re-establish MT5 connection")

    def is_connection_alive(self) -> bool:
        """Lightweight liveness check — does NOT require the lock."""
        if not MT5_AVAILABLE:
            return True
        try:
            info = mt5.account_info()
            return info is not None
        except Exception:
            return False

    # ─── Data access helpers ──────────────────────────────────────────────

    def account_info(self) -> Optional[Any]:
        with self._lock:
            if not MT5_AVAILABLE:
                return None
            return mt5.account_info()

    def symbol_info(self, symbol: str) -> Optional[Any]:
        with self._lock:
            if not MT5_AVAILABLE:
                return None
            info = mt5.symbol_info(symbol)
            if info is None:
                mt5.symbol_select(symbol, True)
                info = mt5.symbol_info(symbol)
            return info

    def symbol_info_tick(self, symbol: str) -> Optional[Any]:
        with self._lock:
            if not MT5_AVAILABLE:
                return None
            return mt5.symbol_info_tick(symbol)

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int,
        start_pos: int,
        count: int,
    ) -> Optional[Any]:
        with self._lock:
            if not MT5_AVAILABLE:
                return None
            return mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)

    def positions_get(self, symbol: Optional[str] = None) -> Tuple[Any, ...]:
        with self._lock:
            if not MT5_AVAILABLE:
                return ()
            result = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
            return result if result is not None else ()

    def orders_get(self, symbol: Optional[str] = None) -> Tuple[Any, ...]:
        with self._lock:
            if not MT5_AVAILABLE:
                return ()
            result = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
            return result if result is not None else ()

    def order_check(self, request: dict) -> Optional[Any]:
        with self._lock:
            if not MT5_AVAILABLE:
                return None
            return mt5.order_check(request)

    def order_send(self, request: dict) -> Optional[Any]:
        with self._lock:
            if not MT5_AVAILABLE:
                return None
            return mt5.order_send(request)

    def last_error(self) -> Tuple[int, str]:
        if not MT5_AVAILABLE:
            return (-1, "MT5 not available")
        return mt5.last_error()

    def timeframe_constant(self, tf_string: str) -> int:
        if not MT5_AVAILABLE:
            return 15
        mapping = {
            "M1":  mt5.TIMEFRAME_M1,  "M5":  mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1":  mt5.TIMEFRAME_H1,  "H4":  mt5.TIMEFRAME_H4,
            "D1":  mt5.TIMEFRAME_D1,  "W1":  mt5.TIMEFRAME_W1,
            "M2":  mt5.TIMEFRAME_M2,  "M3":  mt5.TIMEFRAME_M3,
            "M4":  mt5.TIMEFRAME_M4,  "M6":  mt5.TIMEFRAME_M6,
            "M10": mt5.TIMEFRAME_M10, "M12": mt5.TIMEFRAME_M12,
            "M20": mt5.TIMEFRAME_M20, "H2":  mt5.TIMEFRAME_H2,
            "H3":  mt5.TIMEFRAME_H3,  "H6":  mt5.TIMEFRAME_H6,
            "H8":  mt5.TIMEFRAME_H8,  "H12": mt5.TIMEFRAME_H12,
            "MN1": mt5.TIMEFRAME_MN1,
        }
        return mapping.get(tf_string.upper(), mt5.TIMEFRAME_M15)


# Module-level singleton
connector = MT5Connector()
