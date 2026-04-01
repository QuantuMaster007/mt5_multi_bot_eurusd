# MT5 Multi-Bot Framework — Architecture & Usage Reference

## Directory Tree (77 files total, ~7,550 lines of code)

```
mt5_multi_bot_eurusd/
├── .env.example                    ← copy to .env, fill credentials
├── README.md
├── requirements.txt
├── main.py                         ← entry point
├── run_paper.py                    ← shortcut: paper mode
├── run_demo.py                     ← shortcut: demo mode
├── orchestrator.py                 ← central controller
├── weekly_review.py                ← analytics runner
├── health_check.py                 ← live status tool
│
├── config/
│   ├── general_config.yaml
│   ├── mt5_config.yaml
│   ├── symbol_eurusd.yaml
│   ├── execution_config.yaml
│   ├── risk_config.yaml
│   ├── policy_config.yaml
│   ├── logging_config.yaml
│   └── strategies/
│       ├── mean_reversion.yaml
│       ├── range_trading.yaml
│       └── scalping.yaml
│
├── core/                           ← shared infrastructure (no strategy logic)
│   ├── constants.py                ← all magic numbers / enums
│   ├── exceptions.py               ← typed exception hierarchy
│   ├── utils.py                    ← pure utility functions
│   ├── settings.py                 ← config loader singleton
│   ├── logger.py                   ← structured log setup
│   ├── json_logger.py              ← JSONL event/trade logging
│   ├── mt5_connector.py            ← thread-safe MT5 wrapper
│   ├── broker_profile.py           ← dynamic fill-mode detection
│   ├── retcode_mapper.py           ← MT5 retcode classification
│   ├── order_validator.py          ← pre-flight order validation
│   ├── execution_engine.py         ← broker-aware order sender
│   ├── market_data.py              ← OHLCV + tick fetching
│   ├── order_manager.py            ← position tracking & reconciliation
│   ├── risk_manager.py             ← per-trade sizing + daily guards
│   ├── portfolio_manager.py        ← cross-strategy exposure control
│   ├── regime_detector.py          ← ADX/ATR regime classification
│   ├── session_filter.py           ← trading session windows
│   ├── metrics_store.py            ← rolling per-strategy metrics
│   ├── heartbeat.py                ← bot health publishing
│   ├── state_store.py              ← durable key-value persistence
│   ├── analytics.py                ← aggregation helpers
│   ├── cooldown_manager.py         ← cooldown timer helpers
│   ├── news_filter.py              ← news event gating
│   ├── trade_logger.py             ← trade event helpers
│   └── event_logger.py             ← system event helpers
│
├── orchestration/
│   ├── plugin_loader.py            ← AUTO-DISCOVERY of strategy plugins
│   ├── strategy_registry.py        ← central plugin catalogue
│   ├── bot_runner.py               ← per-strategy thread wrapper
│   ├── process_manager.py          ← lifecycle for all runners
│   ├── health_monitor.py           ← heartbeat staleness checker
│   ├── policy_engine.py            ← explainable gating rules
│   └── allocation_engine.py        ← competing intent resolution
│
├── strategies/
│   ├── base_strategy.py            ← abstract base ALL plugins extend
│   ├── mean_reversion.py           ← BB + RSI mean reversion
│   ├── range_trading.py            ← S/R zone rejection
│   └── scalping.py                 ← EMA crossover scalper
│
├── paper/
│   ├── paper_execution.py          ← paper fill engine
│   └── synthetic_fill_model.py     ← configurable slippage model
│
├── backtest/
│   ├── engine.py                   ← bar-by-bar backtester
│   ├── cost_model.py               ← spread/commission/slippage model
│   ├── data_loader.py              ← CSV/MT5 historical data loader
│   └── metrics.py                  ← backtest statistics
│
├── reports/
│   ├── weekly_report_builder.py    ← .md + .csv + .json reports
│   ├── csv_exporter.py             ← JSONL → CSV for spreadsheet analysis
│   └── json_exporter.py            ← metrics snapshot exports
│
├── tests/
│   ├── test_utils.py
│   ├── test_retcode_mapper.py
│   ├── test_order_validator.py
│   ├── test_execution_engine.py
│   ├── test_plugin_loader.py
│   ├── test_policy_engine.py
│   └── test_risk_manager.py
│
└── data/                           ← runtime output (created automatically)
    ├── logs/
    ├── heartbeats/
    ├── state/
    ├── trades/
    ├── metrics/
    ├── events/
    └── reports/
```

---

## Architecture Explanation

### Why threads, not processes?
The MT5 Python API (`MetaTrader5`) uses a single global terminal connection
per OS process. Spawning multiple subprocesses would require each one to
maintain its own MT5 terminal instance — impractical on a standard retail
trading setup. Threads share the process and the single MT5 connection,
which is protected by a `threading.Lock()` in `MT5Connector`.

The real bottleneck in this system is I/O (MT5 API calls), not CPU.
Python's GIL is not a meaningful constraint here.

### Data flow per bot cycle

```
BotRunner.run_loop()
  └─ strategy.run_cycle()
       ├─ session_filter.is_tradeable_now()     ← session gate
       ├─ market_data.get_rates()               ← fetch OHLCV
       ├─ regime_detector.detect_multiple()     ← ADX/ATR regime
       ├─ strategy.prepare_indicators()         ← per-strategy indicators
       ├─ strategy.manage_open_positions()      ← check/manage open trades
       ├─ strategy.generate_signal()            ← entry logic
       ├─ risk_manager.check_can_trade()        ← daily loss, drawdown, spread
       ├─ portfolio_manager.check_can_open()    ← exposure limits
       └─ execution_engine.send_market_order()  ← broker-aware send
            ├─ broker_profile.get_symbol_profile()
            ├─ order_validator.validate()
            ├─ connector.order_check()
            └─ connector.order_send() + retcode_mapper.classify()
```

### Plugin Auto-Discovery

`orchestration/plugin_loader.py` uses Python's `importlib` to scan
every `.py` file in `strategies/`, finds classes that inherit
`BaseStrategy`, validates their `metadata` attribute, and instantiates
them. No registration table to edit. No `__init__.py` modifications.

Plugin validation checks:
1. Class is a `BaseStrategy` subclass
2. `metadata` is a `StrategyMetadata` dataclass instance
3. `name`, `version`, `description`, `symbols`, `timeframes`, `regime_tags` are non-empty
4. `name` is alphanumeric + underscores only
5. Strategy config file in `config/strategies/<name>.yaml` is checked for `enabled: true`

### Policy Engine — Explainable Gating

Every policy decision is logged with a `reason_code`. No magic.
Checks run in priority order:

1. `_check_active_pause`       → previously triggered loss cooldown still active
2. `_check_regime`             → current regime is in this strategy's blocked list
3. `_check_spread`             → current spread > strategy's spread limit
4. `_check_consecutive_losses` → N consecutive losses → triggers timed pause
5. `_check_exec_failure_rate`  → >40% failures over rolling window → blocks strategy

---

## Setup Instructions

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `MetaTrader5` package is Windows-only (or Wine on Linux/macOS).
> On non-Windows systems, the framework runs in stub mode (no live data).

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env:
#   MT5_LOGIN=your_account_number
#   MT5_PASSWORD=your_password
#   MT5_SERVER=YourBroker-Demo
#   MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe  (optional)
#   RUN_MODE=paper
```

### 3. Connect to MT5

1. Install MetaTrader 5 from your broker's website
2. Log in to a **demo account** first
3. Make sure the EURUSD symbol is visible in Market Watch
4. Keep the terminal running while the framework runs

### 4. Run in paper mode (no real orders)

```bash
python run_paper.py
```

Paper mode fetches live prices from MT5 but all fills are simulated
locally. Nothing is sent to the broker. All logs and trade journals
are written identically to demo/live mode.

### 5. Run in demo mode (real orders to demo account)

```bash
python run_demo.py
```

Uses your MT5 demo account. Real orders are sent via `order_send()`.
All broker validation, fill-mode detection, and retcode handling apply.

### 6. Check bot health while running

```bash
python health_check.py
```

### 7. Generate weekly review report

```bash
python weekly_review.py          # last 7 days
python weekly_review.py --days 14
```

Reports are written to `data/reports/`:
- `weekly_summary.md`   — human-readable
- `weekly_summary.csv`  — open in Excel/Google Sheets
- `weekly_summary.json` — programmatic access

---

## Adding a New Strategy (3 steps)

### Step 1 — Create the strategy file

```python
# strategies/breakout_strategy.py
from typing import Any, ClassVar, Dict, List, Optional
from strategies.base_strategy import BaseStrategy, StrategyMetadata, TradeIntent

class BreakoutStrategy(BaseStrategy):

    metadata: ClassVar[StrategyMetadata] = StrategyMetadata(
        name="breakout",
        version="1.0.0",
        description="ATR-based breakout entry on M30",
        symbols=["EURUSD"],
        timeframes=["M30"],
        regime_tags=["trending", "breakout"],
        risk_profile="medium",
        magic_offset=400,
    )

    def prepare_indicators(self, df) -> Dict[str, Any]:
        # compute your indicators here
        return {}

    def generate_signal(self, df, indicators, regimes, spread_pips, tick) -> Optional[TradeIntent]:
        # return TradeIntent(...) or None
        return None
```

### Step 2 — Create the config file

```yaml
# config/strategies/breakout.yaml
strategy: breakout
enabled: true
symbol: EURUSD
timeframe: M30
atr_period: 14
breakout_mult: 1.5
stop_atr_mult: 1.0
take_profit_atr_mult: 2.0
max_positions: 1
magic_offset: 400
```

### Step 3 — Restart

```bash
python run_demo.py
```

The plugin loader will print:
```
Plugin ACCEPTED: name=breakout version=1.0.0 symbols=['EURUSD'] tf=['M30']
Bot launched: breakout
```

**No other files need editing.**

---

## How Weekly Review Works

`weekly_review.py` reads two JSONL files:
- `data/events/events.jsonl` — all policy/risk/execution events
- `data/trades/trades.jsonl` — all trade open/close records

It parses every line, filters to the chosen date range, and aggregates:

| Section               | Key Metrics |
|-----------------------|-------------|
| Strategy Performance  | trades, win%, pnl, expectancy, profit factor, max DD |
| Execution Diagnostics | attempts, filled, rejected, fill rate, rejection categories |
| Policy Summary        | blocks by reason code, blocks by strategy |
| Risk Summary          | blocks by reason, by strategy |
| Regime Summary        | signal count by regime |
| Suggestions           | rule-based, honest, no profit claims |

---

## Fill Mode Handling (UNSUPPORTED FILLING MODE prevention)

The most common MT5 order rejection for new users is:
```
TRADE_RETCODE_REJECT — "Unsupported filling mode"
```

This framework prevents it by:

1. `broker_profile.py` reads `symbol_info.filling_mode` (a bitmask)
2. Extracts which of FOK / IOC / RETURN the broker actually supports
3. `execution_engine.py` uses `profile.preferred_fill_mode` automatically
4. If a fill mode is still rejected, the engine tries the next supported mode
5. If all modes fail, a hard-reject cooldown is applied and the event is logged

**You never need to hardcode `type_filling` anywhere.**

---

## Known Limitations

1. **Windows only** — MT5 Python API is Windows-native. Use Wine on Linux or a Windows VM.
2. **Single process** — all strategies share one Python process. A fatal crash affects all bots.
3. **Bar data only** — the backtester uses OHLCV bars, not tick data. Fill simulation is approximate.
4. **No account currency conversion** — lot sizing assumes a USD-denominated account. For EUR, GBP, etc. accounts, `risk_manager.compute_lot_size()` needs a FX conversion factor added.
5. **No news calendar integration** — `news_filter.py` is a stub; integrate with a news API for production use.
6. **No profitability guarantee** — this is an engineering framework. Whether any strategy generates alpha depends entirely on the strategy logic and market conditions.

---

## Suggested Next Improvements

1. **Tick-level data integration** — replace bar-based backtester with tick replay
2. **FX rate conversion** — fix lot sizing for non-USD accounts
3. **News API integration** — connect `news_filter.py` to ForexFactory or Finnhub calendar
4. **Walk-forward optimisation** — periodic parameter re-fitting on rolling windows
5. **Telegram / email alerts** — emit heartbeat failures and daily summaries to a channel
6. **Web dashboard** — a Flask/FastAPI endpoint serving live heartbeat and metrics data
7. **Multi-symbol expansion** — the infrastructure is symbol-agnostic; add GBPUSD etc. by adding config files and updating strategy `symbols` metadata
8. **Position sizing improvements** — Kelly criterion variant, volatility-adjusted sizing
9. **True multiprocessing mode** — for strategies with heavy CPU compute (e.g. ML models), add an optional `multiprocessing.Process` runner alongside the thread runner
10. **Database backend** — replace JSONL files with SQLite or PostgreSQL for faster aggregation in weekly reports

---

> ⚠️ **Disclaimer:** This framework is provided for educational and research
> purposes. It does not guarantee profitability. All trading involves
> substantial risk of loss. Always test thoroughly on a demo account
> before risking real capital.
