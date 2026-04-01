# MT5 Multi-Bot EURUSD Framework

A modular, plugin-based, multi-strategy trading framework for MetaTrader 5, initially targeting EURUSD.

## ⚠️ Disclaimer

This framework does **not** guarantee profitability. It is an engineering tool for systematic strategy development, testing, and evaluation. All trading involves risk of loss. Start with demo/paper mode only. Past performance is not indicative of future results.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                      │
│  (plugin discovery, bot lifecycle, health monitor)   │
└──────────┬──────────────────────────────────────────┘
           │ manages
   ┌───────┴───────┬───────────────┐
   ▼               ▼               ▼
┌──────┐       ┌──────┐       ┌──────┐
│ Bot1 │       │ Bot2 │       │ Bot3 │
│ MR   │       │ Range│       │ Scalp│
└──┬───┘       └──┬───┘       └──┬───┘
   └──────────────┴───────────────┘
           │ trade intents
           ▼
┌─────────────────────────────────────────────────────┐
│               POLICY ENGINE                          │
│  (regime, spread, drawdown, conflict gating)         │
└──────────────────────┬──────────────────────────────┘
                       │ approved intents
                       ▼
┌─────────────────────────────────────────────────────┐
│           RISK / PORTFOLIO MANAGER                   │
│  (sizing, exposure limits, daily loss guards)        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│            EXECUTION ENGINE                          │
│  (broker profile, fill mode, order_check, send)      │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
              MetaTrader 5 Terminal
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and configure environment
cp .env.example .env
# Edit .env with your MT5 credentials

# 3. Run in paper mode (no real orders)
python run_paper.py

# 4. Run in demo mode (real orders to MT5 demo account)
python run_demo.py

# 5. Run weekly review
python weekly_review.py
```

## Adding a New Strategy

1. Create `strategies/my_new_strategy.py` inheriting `BaseStrategy`
2. Create `config/strategies/my_new_strategy.yaml`
3. Restart the orchestrator — it auto-discovers and loads the plugin

See `docs/adding_strategies.md` for full details.

## Project Structure

```
mt5_multi_bot_eurusd/
  main.py                        # entry point
  run_demo.py                    # demo mode launcher
  run_paper.py                   # paper mode launcher
  orchestrator.py                # central controller
  weekly_review.py               # analytics runner
  health_check.py                # status tool

  config/                        # all YAML configs
  core/                          # shared infrastructure
  orchestration/                 # bot lifecycle management
  strategies/                    # strategy plugins
  paper/                         # paper trade simulation
  reports/                       # report builders
  backtest/                      # offline backtesting
  tests/                         # unit tests
  data/                          # runtime data (logs, trades, metrics)
```

## Modes

| Mode   | MT5 Orders | Fill Simulation | Logs |
|--------|-----------|----------------|------|
| Paper  | No        | Yes (synthetic)| Yes  |
| Demo   | Yes (demo)| No             | Yes  |
| Live   | Yes (live)| No             | Yes  |

## Known Limitations

- MT5 Python API is Windows-only (or Wine on Linux)
- Tick data latency depends on broker feed quality
- Backtester uses bar data (no tick-level simulation)
- Policy engine is rule-based, not predictive
- No guarantee of strategy profitability
