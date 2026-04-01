# Engineering Audit Report
## MT5 Multi-Bot EURUSD Framework
**Auditor:** Post-generation self-review  
**Status:** UNSAFE FOR DEMO USE until all CRITICAL items resolved

---

## Severity Scale
- 🔴 **CRITICAL** — Will cause incorrect/dangerous behaviour in demo: wrong orders, undetected disconnects, silent risk bypass
- 🟠 **HIGH** — Causes meaningful logic errors, bad metrics, or weak safety margins
- 🟡 **MEDIUM** — Reduces observability or correctness of analysis; doesn't cause immediate bad trades
- 🟢 **LOW** — Code quality, clarity, or minor edge-case issues

---

## File-by-File Defect List

---

### `strategies/base_strategy.py`

| # | Severity | Description |
|---|----------|-------------|
| B1 | 🔴 CRITICAL | **Policy engine never called.** `_do_run_cycle()` calls risk_manager and portfolio_manager but never calls `policy_engine.evaluate()`. All regime gating, spread-per-strategy limits, consecutive-loss pausing, and execution failure rate blocking are completely bypassed. The policy engine exists but is entirely disconnected from execution. |
| B2 | 🔴 CRITICAL | **Tick freshness never checked.** `market_data.is_tick_fresh()` exists but is never invoked before trading. If the MT5 data feed is stale (weekend, disconnection, market closed), strategies will attempt to trade on hours-old prices without any warning. |
| B3 | 🔴 CRITICAL | **No reconnect on `MT5DataError`.** When `market_data.get_rates()` raises `MT5DataError`, the exception is swallowed by the outer try/except, `_last_error` is set, and the next cycle retries without any reconnect attempt. A disconnected MT5 feed produces silent repeated failures. |
| B4 | 🟠 HIGH | **`record_exec_attempt` double-counted.** Line 275 calls `record_exec_attempt(success=False)` unconditionally before sending, then line 279 calls it again with the real result. Every single trade attempt is recorded as two events — one always-failure + one real result. Execution failure rate metrics are systematically wrong. |
| B5 | 🟠 HIGH | **Multiple tick fetches in one cycle create price inconsistency.** `market_data.get_tick()` is called at line 146, then `market_data.get_spread_pips()` internally fetches another tick at line 151 (potentially different price). Then `execution_engine._build_request()` fetches a third tick at send time. The spread used for risk/policy checks can differ from the spread at execution. For fast markets this matters. |
| B6 | 🟡 MEDIUM | **Heartbeat does not publish spread or regime context.** Weekly review cannot reconstruct what market conditions were when a bot was alive vs. idle. |

---

### `core/execution_engine.py`

| # | Severity | Description |
|---|----------|-------------|
| E1 | 🔴 CRITICAL | **`order_check()` failure never blocks the order.** When `order_check()` returns a non-zero retcode (INVALID_STOPS, NO_MONEY, TRADE_DISABLED, etc.), the code logs a warning and **proceeds to `order_send()` anyway**. The comment says "some brokers return non-zero on check for valid orders" — this is true for retcode 0 vs. 10009, but a genuine HARD retcode from `order_check` must abort. The current behaviour wastes a real order attempt on a known-to-fail request. |
| E2 | 🔴 CRITICAL | **`close_position()` bypasses `order_validator`**. The close path builds a request and calls `_send_with_retry()` directly, skipping volume validation, stop-level checks, and fill-mode validation. A close with wrong volume or mismatched fill mode will fail at the broker with no pre-detection. |
| E3 | 🟠 HIGH | **Fill mode exhaustion logic is cyclic.** `_try_alternate_fill_mode()` returns "any mode that isn't current." If the broker supports [IOC, FOK] and IOC fails with FILL_ERROR, it returns FOK. If FOK also fails, it returns IOC again — causing an infinite alternation cycle until retry count is exhausted. Tried modes are not tracked. |
| E4 | 🟠 HIGH | **`order_check` result `retcode=0` (not `RETCODE_OK=10009`) is treated as failure.** The check `check_result.retcode not in (0, RETCODE_OK)` correctly handles both. But the downstream warning is still emitted for retcode 0, logging a misleading "order_check failed" message for a successful check. |
| E5 | 🟡 MEDIUM | **Cooldown is per-symbol, not per-strategy.** If MeanReversion gets a hard rejection on EURUSD, scalping and range_trading are also blocked for 60 seconds. Cooldowns should be keyed by `(symbol, magic)` or `(symbol, strategy_name)`. |
| E6 | 🟡 MEDIUM | **No tick-age guard before `order_send`.** If `symbol_info_tick()` returns a tick older than a few seconds (feed lag), the order is placed at a stale price. The `is_tick_fresh()` check exists in `market_data.py` but is never called in the execution path. |

---

### `core/order_validator.py`

| # | Severity | Description |
|---|----------|-------------|
| V1 | 🟠 HIGH | **`freeze_level` is stored in `SymbolProfile` but never validated.** The `stops_level` check validates distance from current price, but `freeze_level` (zone around current price where modifications are blocked) is fetched and stored but completely ignored. For modifying existing positions this is important. The code needs to at least document the distinction and validate it for close/modify operations. |
| V2 | 🟠 HIGH | **Stop validation uses `current_price` but the correct reference is the fill price (ask for buys, bid for sells).** SL/TP distance is measured from the fill price, not mid-price. Validating against mid-price can pass stops that the broker will reject. |
| V3 | 🟡 MEDIUM | **Volume snap-down to step silently produces 0.** If `round_to_step(0.009, 0.01) = 0.0`, the validator then compares `0.0 < 0.01` and raises `VolumeError`. This is correct, but the error message says "volume 0.009 is below minimum 0.01" which is misleading — it should say the computed lot size is too small, suggesting account balance or SL size needs to change. |

---

### `core/broker_profile.py`

| # | Severity | Description |
|---|----------|-------------|
| P1 | 🔴 CRITICAL | **Profile cache never refreshed after MT5 reconnect.** The `_cache` dict is populated once and never invalidated. After a reconnect (which may involve a different session, server update, or symbol re-subscription), `symbol_info.filling_mode`, `stops_level`, and `spread` could all have changed. The stale profile will produce wrong fill modes and stop validation. `broker_profile.refresh()` exists but is never called from the reconnect flow. |
| P2 | 🟠 HIGH | **`FILL_RETURN` set as `preferred = FILL_RETURN` default before checking supported list.** If `supported_fill_modes` is empty (all bits zero — some brokers report this), the fallback profile uses IOC, but the in-branch default is RETURN. For brokers that genuinely don't report any fill bits (buggy `filling_mode=0`), RETURN is sent and rejected. |
| P3 | 🟡 MEDIUM | **`pip_value` derived from `digits` only — wrong for JPY pairs.** `pip_value = point * 10 if digits in (5, 3) else point`. For USDJPY (3-digit = 0.001 point, pip = 0.01), `digits=3` so pip = `0.001 * 10 = 0.01`. Correct for JPY. But EURUSD has 5 digits, EURUSD.pro may have 3 digits at some brokers. This logic is brittle for non-standard quoting. Noted as low-risk for EURUSD-only deployment but will break on symbol expansion. |

---

### `core/market_data.py`

| # | Severity | Description |
|---|----------|-------------|
| M1 | 🔴 CRITICAL | **`get_tick()` does not validate tick age.** Returns any tick regardless of how old it is. There is no age gate. A weekend tick from Friday will be returned on Monday morning and used as a live price. |
| M2 | 🟠 HIGH | **`get_spread_pips()` re-fetches the tick internally** instead of accepting a pre-fetched tick object. This creates multiple distinct MT5 API calls in one strategy cycle, each potentially returning different prices, making the spread calculation and the price used in signal generation inconsistent. |

---

### `core/mt5_connector.py`

| # | Severity | Description |
|---|----------|-------------|
| C1 | 🔴 CRITICAL | **Reconnect does not refresh broker profile or re-subscribe symbols.** After `reconnect()` succeeds, the system has a fresh MT5 session but stale: broker profile fill modes, symbol_info attributes, and Market Watch subscriptions. The framework continues with old cached data. |
| C2 | 🟠 HIGH | **Reconnect failure raises `MT5ConnectionError` which propagates uncaught in strategy loops.** The bot runner catches generic `Exception`, so this is caught, but it causes the bot to restart rather than gracefully waiting for MT5 to come back. A smarter approach waits with backoff and does NOT kill the bot thread. |
| C3 | 🟡 MEDIUM | **`ensure_connected()` is never called by bots before data requests.** Data calls (`copy_rates_from_pos`, `symbol_info_tick`) go straight to MT5 without checking `self._connected`. If the terminal disconnected between cycles, the first data call fails silently and returns `None`. |

---

### `orchestrator.py`

| # | Severity | Description |
|---|----------|-------------|
| O1 | 🔴 CRITICAL | **No reconnect loop in the supervision loop.** The orchestrator monitors heartbeats and restarts dead threads, but never calls `connector.reconnect()`. A terminal disconnect kills data flow for all bots but the orchestrator just keeps running without restoring the connection. |
| O2 | 🟠 HIGH | **No MT5 connection health check.** The orchestrator checks file-based heartbeats but never checks `connector.connected` or calls `mt5.account_info()` to verify the terminal is actually responding. |

---

### `orchestration/policy_engine.py`

| # | Severity | Description |
|---|----------|-------------|
| PE1 | 🔴 CRITICAL | **Consecutive-loss pause timer resets on every call.** `_check_consecutive_losses()` is called every cycle. When `consecutive_losses >= threshold`, it sets `_pause_until[strategy] = now + pause_seconds` **every single time**. This means `_check_active_pause()` clears on the next cycle's evaluation, and then immediately `_check_consecutive_losses` re-pauses. The timer never expires as long as losses persist. The bot oscillates between "paused" and "would-be-paused" every cycle. |
| PE2 | 🟡 MEDIUM | **Policy engine state is lost on process restart.** Consecutive loss counts live in `metrics_store` (in-memory). If the process is restarted, counts reset to 0, policy state is wiped, and a strategy that should be paused is immediately allowed to trade again. |

---

### `orchestration/bot_runner.py`

| # | Severity | Description |
|---|----------|-------------|
| BR1 | 🟠 HIGH | **`_restart_count` never resets on success.** A strategy that has 2 errors over a long period (below the threshold of 3) will accumulate them until it eventually hits the limit and dies permanently, even if 1000 cycles ran cleanly between errors. |

---

### `paper/paper_execution.py`

| # | Severity | Description |
|---|----------|-------------|
| PP1 | 🔴 CRITICAL | **`risk_manager.record_trade_close()` never called when paper positions close.** When SL/TP is hit in `_close_position()`, the trade is logged but `risk_manager` is not notified. Daily loss tracking, weekly loss tracking, consecutive loss counting, and drawdown highwater mark are all wrong in paper mode. The risk guards that the whole system depends on are silent no-ops for paper trades. |
| PP2 | 🟠 HIGH | **`metrics_store.record_trade()` never called from paper mode.** Weekly report metrics (win rate, expectancy, profit factor, drawdown) will all show zeros for paper mode sessions. The framework's primary evaluation tool is blind to paper results. |
| PP3 | 🟡 MEDIUM | **`update_positions()` is never called automatically.** Paper SL/TP checking only happens when `paper_engine.update_positions()` is explicitly called. In the current framework, nothing calls it. Paper positions will stay open forever regardless of price movement. |

---

### `reports/weekly_report_builder.py`

| # | Severity | Description |
|---|----------|-------------|
| R1 | 🟠 HIGH | **Trade close event filter only matches `"paper_trade_close"`.** Demo/live trades use the event `"trade_open"` / `"trade_close_detected"` (from `order_manager`). The `_strategy_summaries()` method filters `if "close" not in event and "pnl" not in t: continue` — this is fragile string matching that will silently miss all live trade close events, producing an empty strategy summary for demo runs. |
| R2 | 🟠 HIGH | **No open-time/close-time for live trades in trade log.** `base_strategy._execute_intent()` logs `trade_open` but no `trade_close` for live positions. Close detection only happens via `order_manager.reconcile()` which is never called. So the weekly report has zero live trade close records and zero P&L for demo mode. |
| R3 | 🟡 MEDIUM | **`_strategy_summaries` modifies `s["consec_losses"]` as running state but this is per-report-build, not persistent.** Consecutive loss counting in the report re-plays trades in arrival order (JSONL order), which is close but not guaranteed to be exact trade sequence if events interleave. |

---

### `core/constants.py`

| # | Severity | Description |
|---|----------|-------------|
| K1 | 🟠 HIGH | **`RETCODE_LONG_ONLY = 10030` and `RETCODE_INVALID_FILL = 10030` are the same value.** In `retcode_mapper.py`, `10030` maps to `FILL_ERROR`. But MT5's real `10030` is `TRADE_RETCODE_LONG_ONLY` — a hard broker restriction, not a fill mode error. The framework will misclassify a long-only broker rejection as a fill mode error, attempt to retry with alternate fill modes (incorrect), and apply a fill-error cooldown instead of a hard-reject cooldown. |

---

### `core/utils.py`

| # | Severity | Description |
|---|----------|-------------|
| U1 | 🟡 MEDIUM | **`round_to_step` uses float arithmetic that is fragile for unusual step values.** `math.floor(value / step)` can produce off-by-one errors for step sizes like 0.001 with values like 0.1005 due to IEEE 754 representation. Using `Decimal` ensures exact decimal arithmetic for lot sizes, which are always expressible in fixed decimal notation. |

---

## Summary

| Severity | Count |
|----------|-------|
| 🔴 CRITICAL | 11 |
| 🟠 HIGH | 12 |
| 🟡 MEDIUM | 9 |
| **Total** | **32** |

**Before demo deployment:** All CRITICAL items must be resolved.  
**Before trusting weekly reports:** All HIGH items must be resolved.
