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

    Rows with missing OHLC values are dropped (a NaN bar would silently shrink
    the opening range or book a NaN exit), duplicate timestamps keep the last
    occurrence (overlapping fetches), and an empty result raises rather than
    letting downstream scripts crash on ``df.index[0]``.
    """
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    if ts_col not in df.columns and "timestamp" in cols:
        ts_col = cols["timestamp"]
    df = df.rename(columns={cols.get(c, c): c for c in REQUIRED + ["volume"] if c in cols})

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing required columns: {missing}")

    # Timestamps may be naive, uniformly tz-aware, or carry per-row UTC offsets
    # that differ across a DST transition (e.g. "-05:00" and "-04:00" in one
    # file). Mixed offsets can only be parsed with utc=True.
    try:
        ts = pd.to_datetime(df[ts_col])
        if ts.dtype == object:  # mixed offsets fall back to object dtype
            ts = pd.to_datetime(df[ts_col], utc=True)
    except (ValueError, TypeError):
        ts = pd.to_datetime(df[ts_col], utc=True)
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(ZoneInfo(source_tz))
    df.index = ts.dt.tz_convert(ZoneInfo(tz))

    df = df.dropna(subset=REQUIRED)
    if df.empty:
        raise ValueError(f"{path} contains no usable bars (empty file or all-NaN rows)")

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    keep = REQUIRED + (["volume"] if "volume" in df.columns else [])
    return df[keep].astype({c: "float64" for c in REQUIRED})


def session_slice(day_df: pd.DataFrame, open_str: str, close_str: str) -> pd.DataFrame:
    """Restrict a single day's bars to the [open, close) regular-session window.

    The close is exclusive: a bar labeled 16:00 covers 16:00-16:01, which is
    after the RTH close — trading it would use post-close prices.
    """
    return day_df.between_time(open_str, close_str, inclusive="left")


def chronological_split(df: pd.DataFrame, train_frac: float = 0.7):
    """Split bars into (in-sample, out-of-sample) on a session boundary.

    Splits by unique trading day so no session straddles the cut — essential for
    honest out-of-sample validation.
    """
    days = sorted({ts.date() for ts in df.index})
    if len(days) < 2:
        raise ValueError("need at least 2 trading days to split")
    cut = days[int(len(days) * train_frac)]
    is_df = df[df.index.map(lambda ts: ts.date() < cut)]
    oos_df = df[df.index.map(lambda ts: ts.date() >= cut)]
    return is_df, oos_df
