# How to Add a New Strategy

This guide explains everything you need to know to add a new strategy
plugin to the framework. The full process takes about 10–20 minutes.

---

## The 3-File Rule

Adding a strategy requires exactly **3 files**:

| File | What you do |
|------|-------------|
| `strategies/my_strategy.py` | Copy from template, implement logic |
| `config/strategies/my_strategy.yaml` | Copy from template, set parameters |
| *(restart)* | Orchestrator auto-discovers everything |

No other files need to be modified.

---

## Step-by-Step

### Step 1 — Copy the template strategy file

```bash
cp strategies/template_strategy.py strategies/momentum_breakout.py
```

Open `strategies/momentum_breakout.py` and make these changes:

**A. Rename the class** (optional but recommended for clarity):

```python
# Before
class MyTemplateStrategy(BaseStrategy):

# After
class MomentumBreakoutStrategy(BaseStrategy):
```

**B. Fill in the metadata:**

```python
metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
    name        = "momentum_breakout",  # ← must match your YAML filename
    version     = "1.0.0",
    description = "Rolling channel breakout confirmed by ADX",
    symbols     = ["EURUSD"],
    timeframes  = ["H1"],
    regime_tags = ["trending", "breakout"],
    risk_profile= "medium",
    magic_offset= 400,                  # ← pick a unique integer 1-9999
)
```

**C. Define your CONFIG_SCHEMA** (enables automatic YAML validation):

```python
CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
    "lookback_candles": ConfigField(
        int, required=True, default=20,
        min_val=5, max_val=200,
        description="Candle lookback for channel calculation",
    ),
    "adx_threshold": ConfigField(
        float, required=True, default=25.0,
        min_val=10.0, max_val=60.0,
        description="Minimum ADX to allow entry",
    ),
    "stop_atr_mult": ConfigField(
        float, required=True, default=1.5,
        description="SL = entry ± ATR × this multiplier",
    ),
}
```

**D. Implement `prepare_indicators()`:**

```python
def prepare_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
    if not TA_AVAILABLE or len(df) < 60:
        return {}
    import ta
    atr = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"],
        window=self.cfg_int("atr_period", 14)
    ).average_true_range()
    return {"atr": atr}
```

**E. Implement `generate_signal()`:**

```python
def generate_signal(self, df, indicators, regimes, spread_pips, tick):
    if not indicators:
        return None
    if self.has_open_position():
        return None

    close   = float(df["close"].iloc[-1])
    atr_val = float(indicators["atr"].iloc[-1])
    sl_dist = atr_val * self.cfg_float("stop_atr_mult", 1.5)
    pip_val = 0.0001

    # Your signal condition here
    if close > some_level:
        entry = tick["ask"]
        lots  = self._size_lots(sl_dist)
        if lots <= 0:
            return None
        return TradeIntent(
            strategy    = self.metadata.name,
            symbol      = self._symbol,
            side        = "buy",
            entry_price = entry,
            sl          = entry - sl_dist,
            tp          = entry + sl_dist * 2.0,
            volume      = lots,
            reason_code = "my_signal_name",
            notes       = f"close={close:.5f} atr={atr_val:.5f}",
        )
    return None
```

---

### Step 2 — Copy and edit the config YAML

```bash
cp config/strategies/template.yaml config/strategies/momentum_breakout.yaml
```

Edit the YAML. The filename (without `.yaml`) must match `metadata.name` exactly:

```yaml
strategy: momentum_breakout  # ← must match metadata.name
enabled: true
symbol: EURUSD
timeframe: H1
magic_offset: 400             # ← must be unique across all strategies

# Your strategy-specific params (must match CONFIG_SCHEMA keys)
lookback_candles: 20
adx_threshold: 25.0
stop_atr_mult: 1.5
take_profit_rr: 2.0
```

---

### Step 3 — Verify before restarting

Run the strategy scanner to catch any errors without starting the whole system:

```bash
python list_strategies.py
```

You should see output like:

```
═══════════════════════════════════════════════════════════════════════
  MT5 Multi-Bot — Strategy Plugin Scanner
═══════════════════════════════════════════════════════════════════════

  STRATEGY PLUGIN DISCOVERY — 5 scanned
  ✓ Accepted: 4    ✗ Rejected: 0    ○ Disabled: 0

  Strategy               Ver     Symbol   TF    Magic  Status
  ─────────────────────────────────────────────────────────────────
  mean_reversion         1.0.0   EURUSD   M15   200100 ✓ ok
  momentum_breakout      1.0.0   EURUSD   H1    200400 ✓ ok    ← new
  range_trading          1.0.0   EURUSD   H1    200200 ✓ ok
  scalping               1.0.0   EURUSD   M5    200300 ✓ ok
  ─────────────────────────────────────────────────────────────────
```

---

### Step 4 — Restart the orchestrator

```bash
# Ctrl+C to stop, then:
python run_demo.py     # or run_paper.py
```

The orchestrator prints the discovery table at startup. Your new strategy
appears in the table and begins running.

---

## Validation Rules

The plugin loader runs these checks in order. **Every check must pass** before
the plugin is accepted. If any check fails, the plugin is rejected with a
clear error message and the rest of the system continues normally.

### Metadata checks

| Rule | Detail |
|------|--------|
| `metadata` class var exists | Must be a `StrategyMetadata` instance |
| `name` format | Lowercase, letters/digits/underscores, 2–40 chars, starts with a letter |
| `version` format | Must match `MAJOR.MINOR.PATCH` (e.g. `1.0.0`) |
| `timeframes` valid | Must be recognised MT5 timeframe strings |
| `risk_profile` valid | Must be `low`, `medium`, or `high` |
| All required fields non-empty | name, version, description, symbols, timeframes, regime_tags |

### Config checks (if CONFIG_SCHEMA is defined)

| Rule | Detail |
|------|--------|
| Required fields present | Every `ConfigField(required=True)` must exist in YAML |
| Type coercible | Values must be convertible to the declared type |
| Within range | Values must satisfy `min_val` / `max_val` constraints |
| Within choices | Values must be in `choices` list if specified |
| `symbol` value | Must be `EURUSD` (expandable later) |
| `timeframe` value | Must be a valid MT5 timeframe string |
| `magic_offset` range | Must be 1–9999 |

### Conflict checks

| Rule | Detail |
|------|--------|
| Unique name | No two loaded plugins may share the same `metadata.name` |
| Unique magic_offset | No two loaded plugins may share the same `magic_offset` |

---

## Example Error Messages

When validation fails you get a targeted, actionable error:

**Missing metadata:**
```
✗ REJECTED [my_strategy.py]
  MyStrategy in my_strategy.py has no 'metadata' class variable.
  Add to your class:
      metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
          name="my_strategy",
          ...
      )
```

**Invalid name format:**
```
✗ REJECTED [MyStrategy.py]
  Strategy name 'MyStrategy' is invalid.
  Rules: lowercase letters/digits/underscores, start with a letter, 2-40 chars.
  Good: 'momentum_breakout', 'ema_cross', 'rsi_reversal'
  Bad:  'MyStrategy', '1breakout', 'x'
```

**Missing required config field:**
```
✗ REJECTED [momentum_breakout.py]
  [momentum_breakout] config/strategies/momentum_breakout.yaml
  is missing required field 'adx_threshold'.
  Description: Minimum ADX to allow entry
  Expected type: float
```

**Config value out of range:**
```
✗ REJECTED [momentum_breakout.py]
  [momentum_breakout] config field 'adx_threshold' = 5.0
  is below minimum 10.0.
  Minimum ADX to allow entry
```

**Magic offset collision:**
```
✗ REJECTED [momentum_breakout.py]
  [momentum_breakout] magic_offset=300 is already used by strategy 'scalping'.
  Each strategy must have a unique magic_offset to prevent trade attribution conflicts.
  Fix: change magic_offset in momentum_breakout's config YAML and
       update metadata.magic_offset to match.
```

---

## CONFIG_SCHEMA Reference

```python
from orchestration.plugin_validator import ConfigField

CONFIG_SCHEMA: ClassVar[Dict[str, ConfigField]] = {
    "my_param": ConfigField(
        type=int,           # int | float | str | bool
        required=True,      # if True, must be in YAML; if False, uses default
        default=20,         # used when field is absent and required=False
        min_val=5,          # numeric minimum (optional)
        max_val=200,        # numeric maximum (optional)
        choices=None,       # list of allowed values, e.g. ["M15", "H1"] (optional)
        description="...",  # shown in error messages — write a clear sentence
    ),
}
```

Fields that don't appear in CONFIG_SCHEMA are still accessible via
`self._cfg.get("key")` — schema validation only covers declared fields.

---

## StrategyMetadata Reference

```python
metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
    name         = "my_strategy",    # Required. snake_case. Matches YAML filename.
    version      = "1.0.0",          # Required. Semver.
    description  = "One sentence.",  # Required. What the strategy does.
    symbols      = ["EURUSD"],       # Required. List of supported symbols.
    timeframes   = ["M15"],          # Required. List of MT5 timeframe strings.
    regime_tags  = ["ranging"],      # Required. Compatible market regimes.
                                     # Options: ranging, trending, strong_trend,
                                     #          breakout, high_volatility, etc.
    risk_profile = "medium",         # Optional. low | medium | high.
    author       = "your_name",      # Optional. For documentation.
    magic_offset = 400,              # Required-in-practice. Unique int 1-9999.
)
```

---

## BaseStrategy Helper Methods

These are available in every strategy:

| Method | Returns | Description |
|--------|---------|-------------|
| `self.cfg_int(key, default)` | `int` | Read config YAML field as int |
| `self.cfg_float(key, default)` | `float` | Read config YAML field as float |
| `self.cfg_str(key, default)` | `str` | Read config YAML field as str |
| `self.cfg_bool(key, default)` | `bool` | Read config YAML field as bool |
| `self._size_lots(sl_distance_price)` | `float` | Risk-% lot size computation |
| `self.has_open_position()` | `bool` | True if strategy has any open MT5 position |
| `self.get_open_positions()` | `list` | List of open MT5 position objects |
| `self.get_open_position_count()` | `int` | Count of open positions |
| `self._log.info(...)` | — | Prefixed logger for this strategy |
| `self._symbol` | `str` | Symbol from config (e.g. "EURUSD") |
| `self._timeframe` | `str` | Timeframe from config (e.g. "M15") |
| `self._magic` | `int` | Full MT5 magic number (base + offset) |

---

## What the Framework Does For You

You **do not** need to implement:

- ✓ Session filtering — base class calls `session_filter.is_tradeable_now()`
- ✓ Policy gating — base class calls `policy_engine.evaluate()` (regime/spread/loss checks)
- ✓ Risk sizing — use `self._size_lots(sl_distance_price)` 
- ✓ Daily loss limits — `risk_manager.check_can_trade()` is called automatically
- ✓ Spread checks — global threshold in `risk_config.yaml`, per-strategy in policy config
- ✓ Portfolio exposure — `portfolio_manager.check_can_open()` is called automatically
- ✓ Order validation — `order_validator` runs before every send
- ✓ Fill mode detection — `broker_profile` reads from `symbol_info` dynamically
- ✓ Retry logic — `execution_engine` handles transient rejections
- ✓ Heartbeat publishing — base class writes heartbeat every cycle
- ✓ Metrics tracking — wins/losses/drawdown tracked automatically
- ✓ Weekly reports — your strategy appears in reports automatically

You **do** need to implement:

- ✓ `generate_signal()` — your entry logic
- ✓ `prepare_indicators()` — your indicator computation (optional but recommended)
- ✓ `manage_open_positions()` — your exit management (optional; default = MT5 SL/TP)

---

## Policy Engine Integration

The policy engine uses your `metadata.regime_tags` to block strategies in
incompatible regimes. Configure per-strategy rules in `config/policy_config.yaml`:

```yaml
policy:
  regime_gating:
    momentum_breakout:
      blocked_regimes: [ranging, low_liquidity]   # add your strategy name

  spread_block:
    momentum_breakout: 2.0   # block if spread > 2 pips

  strategy_priority:
    momentum_breakout: 2     # tie-breaking priority for conflict resolution
```

---

## Testing Your Strategy

Run the unit test suite to verify framework contracts still hold:

```bash
python -m pytest tests/ -v
```

Write a minimal test for your signal logic (no MT5 needed):

```python
# tests/test_momentum_breakout.py
import sys, types
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))

import pandas as pd
from strategies.momentum_breakout import MomentumBreakoutStrategy

def test_signal_returns_none_on_empty_indicators():
    strat = MomentumBreakoutStrategy()
    result = strat.generate_signal(
        df=pd.DataFrame(), indicators={}, regimes=["ranging"],
        spread_pips=1.0, tick={"bid": 1.08, "ask": 1.0801}
    )
    assert result is None
```

---

## Checklist — Before Activating in Demo

- [ ] `python list_strategies.py` shows strategy as `✓ ok` (no warnings)
- [ ] Magic offset is unique — no conflict warnings
- [ ] `config/strategies/my_strategy.yaml` has `enabled: true`
- [ ] Timeframe matches available data on your broker
- [ ] `stop_loss_pips` / `stop_atr_mult` is reasonable for the timeframe
- [ ] `max_positions: 1` unless you intentionally want multiple simultaneous positions
- [ ] Signal logic handles `has_open_position()` guard to prevent overtrading
- [ ] `generate_signal()` returns `None` correctly when conditions are not met
- [ ] `_size_lots()` result is checked for `> 0` before creating a `TradeIntent`
- [ ] All mandatory CONFIG_SCHEMA fields exist in the YAML
- [ ] Ran `python run_paper.py` and saw at least one signal event in `data/events/events.jsonl`
