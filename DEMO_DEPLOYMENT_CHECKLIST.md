# Demo-Only Deployment Checklist
## MT5 Multi-Bot EURUSD Framework

Complete **every** item before running in demo mode.
Do NOT skip to live trading until demo has run for ≥ 4 weeks with satisfactory results.

---

## 1. Environment Setup

- [ ] Python 3.10+ installed on Windows (or Wine environment)
- [ ] `pip install -r requirements.txt` completed with no errors
- [ ] `MetaTrader5` package version ≥ 5.0.45 confirmed: `python -c "import MetaTrader5; print(MetaTrader5.__version__)"`
- [ ] `.env` file created from `.env.example` with real demo account credentials
- [ ] `RUN_MODE=demo` set in `.env` (or `paper` for pure paper mode)
- [ ] MT5 terminal is running and logged in to demo account
- [ ] EURUSD visible in MT5 Market Watch

---

## 2. Broker Compatibility Checks

- [ ] Confirmed broker supports EURUSD on the demo account
- [ ] `symbol_info.filling_mode` bitmask logged on startup (visible in `data/logs/system.log`)
  - Look for: `Profile | EURUSD ... fill_modes=[...] preferred=...`
  - If `fill_modes=[]` is logged → broker returned `filling_mode=0` → verify fallback probe works
- [ ] `stops_level` confirmed reasonable (< 30 points for EURUSD)
  - Look for: `stops_level=N` in the startup profile log line
  - If stops_level > 50 points: increase `atr_min_pips` in strategy configs accordingly
- [ ] `freeze_level` noted (typically 0 for most brokers)
- [ ] Minimum lot size confirmed: check `volume_min` in log (typically 0.01)
- [ ] Maximum lot size confirmed: check `volume_max` in log (typically 100.0)

---

## 3. Configuration Review

- [ ] `config/risk_config.yaml` reviewed:
  - `default_risk_per_trade: 0.005` (0.5%) — do not exceed 1% for initial testing
  - `max_daily_loss_fraction: 0.02` — confirm you accept a 2% daily loss limit
  - `max_drawdown_fraction: 0.08` — confirm 8% drawdown halt threshold
  - `max_total_positions: 4` — does not exceed your broker's open order limit
- [ ] `config/policy_config.yaml` reviewed:
  - `consecutive_loss_disable_threshold: 4` — strategy pauses after 4 losses in a row
  - `spread_block` limits per strategy are appropriate for current broker spreads
- [ ] `config/execution_config.yaml` reviewed:
  - `deviation_points: 20` — acceptable slippage for your broker's execution speed
  - `hard_reject_cooldown_seconds: 60` — not too short (avoid hammering broker)
- [ ] `config/strategies/*.yaml` reviewed:
  - All strategies: `enabled: true`
  - `magic_offset` values are unique across strategies (100, 200, 300)
  - `timeframe` strings match MT5 format: M5, M15, H1, etc.

---

## 4. Pre-launch Tests

Run all unit tests — every test must pass before demo use:

```bash
python -m pytest tests/ -v
```

- [ ] `test_utils.py` — all pass
- [ ] `test_retcode_mapper.py` — all pass
- [ ] `test_order_validator.py` — all pass
- [ ] `test_execution_engine.py` — all pass
- [ ] `test_plugin_loader.py` — all pass
- [ ] `test_policy_engine.py` — all pass
- [ ] `test_risk_manager.py` — all pass

---

## 5. Paper Mode First

Before demo, run in **paper mode** for at least 2 trading days:

```bash
python run_paper.py
```

During paper run, confirm:

- [ ] Log line `Plugin ACCEPTED: name=mean_reversion` appears for all 3 strategies
- [ ] Log line `Bot launched: mean_reversion` (and range_trading, scalping)
- [ ] `data/heartbeats/*.json` files are being created and updated
- [ ] Running `python health_check.py` in a second terminal shows all bots as `ok`
- [ ] At least one strategy generates a signal (check `data/events/events.jsonl`)
- [ ] Paper fills appear in `data/trades/trades.jsonl` with event=`trade_open`
- [ ] Paper closes appear with event=`trade_close` (SL/TP simulation active)
- [ ] `python weekly_review.py` produces a report at `data/reports/weekly_summary.md`
  - Verify report shows trade counts > 0 for at least one strategy
  - Verify P&L column has non-zero values

---

## 6. Demo Mode Launch

```bash
python run_demo.py
```

First-minute checks (watch `data/logs/system.log`):

- [ ] `MT5 connected | server=... login=...` — connection successful
- [ ] `Account | login=... balance=... currency=...` — account info readable
- [ ] `Symbol profile loaded | EURUSD` — profile with non-empty fill_modes
- [ ] No `CRITICAL` log lines at startup
- [ ] No `order_check HARD FAIL` within first 5 minutes (would indicate config error)
- [ ] `POLICY [strategy] → ENABLED` or `BLOCKED by regime` (policy engine active)
- [ ] At least one session starts generating `signal` events within the London/NY window

---

## 7. First 24-Hour Demo Checks

After 24 hours of demo running:

- [ ] `python health_check.py` — all bots showing `ok`, not `stale`
- [ ] `data/logs/system.log` — no repeated CRITICAL or ERROR lines
- [ ] `data/events/events.jsonl` — contains mix of `signal`, `policy_decision`, `order_sent`, `order_filled`
- [ ] `data/trades/trades.jsonl` — contains `trade_open` records with real MT5 ticket numbers
- [ ] MT5 terminal → Trade tab: confirms open positions match framework magic numbers
  - mean_reversion: magic = 200100
  - range_trading:  magic = 200200
  - scalping:       magic = 200300
- [ ] No `fill_error` rejections in events log
  - If present: re-check `symbol_info.filling_mode` bits in system.log startup
- [ ] No `precheck_hard` rejections
  - If present: check `order_check HARD FAIL` lines for retcode and fix config
- [ ] `stale_tick` events: should be 0 during active trading hours
  - If > 0 during London session: broker feed may be unreliable

---

## 8. Weekly Review Workflow

Every Friday after market close:

```bash
python weekly_review.py
```

Review `data/reports/weekly_summary.md` and check:

- [ ] Every enabled strategy has trade count > 0 (if zero: check session filters and policy blocks)
- [ ] Fill rate > 90% (if lower: review broker profile and spread conditions)
- [ ] No strategy has > 8 consecutive losses in the period
- [ ] Suggestions section reviewed — address any flagged items before next week
- [ ] Export CSV opened in Excel to cross-check P&L figures against MT5 account statement

---

## 9. Strict Demo-Only Rules

Until 4 weeks of demo data is collected:

- [ ] **Never change `RUN_MODE` to `live`**
- [ ] **Never increase `default_risk_per_trade` above 0.01 (1%)**
- [ ] **Never disable the daily loss limit**
- [ ] Do not modify strategy logic mid-week — wait for weekly review
- [ ] Document every config change in a `CHANGELOG.md`

---

## 10. Known Limitations to Acknowledge

Before considering demo results as meaningful:

- [ ] Demo execution speeds may differ from live (demo fills are often faster / always filled)
- [ ] Demo spreads may be tighter than live during off-hours
- [ ] Lot sizing assumes a USD-denominated account — verify `compute_lot_size` output is sensible
- [ ] Backtester uses bar data only — no tick-level validation of entries
- [ ] Policy engine is rule-based — it does not predict whether a strategy will be profitable
- [ ] This framework does **not** guarantee profitability under any conditions

---

> ⚠️ **All trading involves risk of loss. Demo results do not guarantee live performance.**
> Run this framework on a demo account only until you fully understand its behaviour
> and have reviewed at least 4 weeks of systematic weekly reports.
