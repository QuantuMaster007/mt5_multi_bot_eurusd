"""
Framework-wide constants. Do not import MetaTrader5 here — this module
must be importable without an MT5 terminal present (e.g., in tests).

FIX K1: Removed RETCODE_INVALID_FILL = 10030 alias.
         10030 is TRADE_RETCODE_LONG_ONLY in real MT5.
         Fill-mode rejection is detected via order comment string pattern,
         not by retcode alone. See retcode_mapper.py.
"""

# ─── Run Modes ───────────────────────────────────────────────────────────────
MODE_PAPER = "paper"
MODE_DEMO  = "demo"
MODE_LIVE  = "live"
VALID_MODES = {MODE_PAPER, MODE_DEMO, MODE_LIVE}

# ─── Order Sides ─────────────────────────────────────────────────────────────
SIDE_BUY  = "buy"
SIDE_SELL = "sell"

# ─── Order Types (MT5 integer values) ────────────────────────────────────────
ORDER_TYPE_BUY        = 0
ORDER_TYPE_SELL       = 1
ORDER_TYPE_BUY_LIMIT  = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP   = 4
ORDER_TYPE_SELL_STOP  = 5

# ─── MT5 Fill Modes ──────────────────────────────────────────────────────────
FILL_FOK    = 0   # ORDER_FILLING_FOK
FILL_IOC    = 1   # ORDER_FILLING_IOC
FILL_RETURN = 2   # ORDER_FILLING_RETURN

FILL_MODE_NAMES = {FILL_FOK: "FOK", FILL_IOC: "IOC", FILL_RETURN: "RETURN"}

# ─── MT5 Retcodes ────────────────────────────────────────────────────────────
RETCODE_OK               = 10009
RETCODE_PLACED           = 10008
RETCODE_REQUOTE          = 10004
RETCODE_REJECT           = 10006
RETCODE_CANCEL           = 10007
RETCODE_ERROR            = 10011
RETCODE_TIMEOUT          = 10012
RETCODE_INVALID          = 10013
RETCODE_INVALID_VOLUME   = 10014
RETCODE_INVALID_PRICE    = 10015
RETCODE_INVALID_STOPS    = 10016
RETCODE_TRADE_DISABLED   = 10017
RETCODE_MARKET_CLOSED    = 10018
RETCODE_NO_MONEY         = 10019
RETCODE_PRICE_CHANGED    = 10020
RETCODE_PRICE_OFF        = 10021
RETCODE_INVALID_EXP      = 10022
RETCODE_ORDER_CHANGED    = 10023
RETCODE_TOO_MANY_REQ     = 10024
RETCODE_NO_CHANGES       = 10025
RETCODE_SERVER_DISCON    = 10026
RETCODE_BROKER_BUSY      = 10027
RETCODE_REQUOTE2         = 10028
RETCODE_ORDER_LOCKED     = 10029
RETCODE_LONG_ONLY        = 10030   # Broker restricts short sales — HARD rejection
RETCODE_TOO_MANY_ORDERS  = 10031
RETCODE_HEDGE_PROHIBITED = 10032
RETCODE_PROHIBITED_BY_FIFO = 10033

# Fill mode errors are signalled by RETCODE_REJECT (10006) + specific comment text.
# There is no dedicated retcode for "unsupported filling mode" in the official MT5 spec.
# Detection is done in retcode_mapper via the order comment field.
FILL_ERROR_COMMENT_PATTERNS = [
    "unsupported filling mode",
    "invalid filling",
    "filling mode",
]

# ─── Strategy States ─────────────────────────────────────────────────────────
STATE_ENABLED    = "enabled"
STATE_PAUSED     = "paused"
STATE_BLOCKED    = "blocked"
STATE_PAPER_ONLY = "paper_only"
STATE_DISABLED   = "disabled"

# ─── Market Regimes ──────────────────────────────────────────────────────────
REGIME_TRENDING      = "trending"
REGIME_STRONG_TREND  = "strong_trend"
REGIME_RANGING       = "ranging"
REGIME_BREAKOUT      = "breakout"
REGIME_HIGH_VOL      = "high_volatility"
REGIME_LOW_VOL       = "low_volatility"
REGIME_LOW_LIQUIDITY = "low_liquidity"
REGIME_HIGH_SPREAD   = "high_spread"
REGIME_NEWS_WINDOW   = "news_window"
REGIME_UNKNOWN       = "unknown"

# ─── Event Types ─────────────────────────────────────────────────────────────
EVT_SIGNAL          = "signal"
EVT_ENTRY_INTENT    = "entry_intent"
EVT_ENTRY_APPROVED  = "entry_approved"
EVT_ENTRY_BLOCKED   = "entry_blocked"
EVT_ORDER_SENT      = "order_sent"
EVT_ORDER_FILLED    = "order_filled"
EVT_ORDER_REJECTED  = "order_rejected"
EVT_ORDER_CANCELLED = "order_cancelled"
EVT_EXIT_INTENT     = "exit_intent"
EVT_EXIT_SENT       = "exit_sent"
EVT_EXIT_FILLED     = "exit_filled"
EVT_POLICY_DECISION = "policy_decision"
EVT_RISK_BLOCK      = "risk_block"
EVT_SPREAD_SPIKE    = "spread_spike"
EVT_RECONNECT       = "reconnect"
EVT_HEARTBEAT       = "heartbeat"
EVT_ERROR           = "error"
EVT_STRATEGY_STATE  = "strategy_state"
EVT_REGIME_CHANGE   = "regime_change"
EVT_COOLDOWN_START  = "cooldown_start"
EVT_COOLDOWN_END    = "cooldown_end"
EVT_SESSION_FILTER  = "session_filter"
EVT_TRADE_OPEN      = "trade_open"
EVT_TRADE_CLOSE     = "trade_close"
EVT_STALE_TICK      = "stale_tick"
EVT_MT5_RECONNECT   = "mt5_reconnect"
