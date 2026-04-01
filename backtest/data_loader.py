"""
Backtest Data Loader

Loads historical OHLCV data from MT5 for offline strategy testing.
Returns a pandas DataFrame compatible with the strategy indicator pipeline.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from core.logger import get_logger
from core.mt5_connector import connector

log = get_logger("backtest.data_loader")


class BacktestDataLoader:

    def load_from_mt5(
        self,
        symbol: str,
        timeframe_str: str,
        bars: int = 5000,
    ) -> pd.DataFrame:
        """
        Pull historical bars from MT5.

        Returns DataFrame with columns:
          time, open, high, low, close, tick_volume, spread, real_volume
        """
        tf_int = connector.timeframe_constant(timeframe_str)
        raw = connector.copy_rates_from_pos(symbol, tf_int, 0, bars)
        if raw is None or len(raw) == 0:
            raise ValueError(f"No data for {symbol} {timeframe_str}")

        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.sort_values("time").reset_index(drop=True)
        log.info(
            "Loaded %d bars for %s %s | %s → %s",
            len(df), symbol, timeframe_str,
            df["time"].iloc[0], df["time"].iloc[-1],
        )
        return df

    def load_from_csv(self, path: str | Path) -> pd.DataFrame:
        """
        Load from a CSV file with columns:
          time, open, high, low, close, volume  (minimum)
        """
        df = pd.read_csv(path, parse_dates=["time"])
        df = df.sort_values("time").reset_index(drop=True)
        log.info("Loaded %d bars from CSV: %s", len(df), path)
        return df


# Module-level singleton
backtest_data_loader = BacktestDataLoader()
