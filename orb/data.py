"""Load and normalize OHLCV bar data into a tz-aware session-indexed frame."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

REQUIRED = ["open", "high", "low", "close"]


def load_bars(
    path: str,
    tz: str = "America/New_York",
    ts_col: str = "timestamp",
    source_tz: str = "UTC",
) -> pd.DataFrame:
    """Load a CSV of OHLCV bars and return a frame indexed by tz-aware datetime.

    The CSV must have a timestamp column plus open/high/low/close (volume
    optional). ``source_tz`` is the timezone the raw timestamps are expressed in
    (use "UTC" for IBKR exports); the index is converted to ``tz`` so all session
    logic can be done in exchange-local (New York) time.
    """
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    if ts_col not in df.columns and "timestamp" in cols:
        ts_col = cols["timestamp"]
    df = df.rename(columns={cols.get(c, c): c for c in REQUIRED + ["volume"] if c in cols})

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing required columns: {missing}")

    ts = pd.to_datetime(df[ts_col], utc=False)
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(ZoneInfo(source_tz))
    df.index = ts.dt.tz_convert(ZoneInfo(tz))
    df = df.sort_index()

    keep = REQUIRED + (["volume"] if "volume" in df.columns else [])
    return df[keep].astype({c: "float64" for c in REQUIRED})


def session_slice(day_df: pd.DataFrame, open_str: str, close_str: str) -> pd.DataFrame:
    """Restrict a single day's bars to the [open, close] regular-session window."""
    return day_df.between_time(open_str, close_str)
