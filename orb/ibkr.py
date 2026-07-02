"""Interactive Brokers historical-data fetcher for ES futures.

Requires a running TWS or IB Gateway with the API enabled. Paper trading is the
default port (7497 for TWS paper, 4002 for Gateway paper). Live ports are
7496 / 4001.

    pip install ib_insync
    python -m orb.ibkr  # or use scripts/fetch_data.py

Two fetch modes:
- `fetch_es_bars`: one contract (explicit expiry, or the front month resolved
  explicitly via contract details). Long 1-minute pulls are chunked into
  10-day requests to stay inside IBKR's historical-data limits.
- `fetch_es_stitched`: roll-correct multi-quarter history. Each quarterly
  contract contributes bars only for the window in which it was the front
  month (cut over `roll_days` before expiry), so a 6-month backtest doesn't
  silently run on illiquid back-month data. This is the mode to use for any
  dataset longer than one contract's active life (~3 months).
"""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd

CHUNK_DAYS = 10           # max days of intraday bars per historical request
DEFAULT_TIMEOUT = 120.0   # seconds; the ib_insync default of 60s times out on
                          # long 1-min pulls and surfaces as "no bars"


def third_friday(year: int, month: int) -> dt.date:
    """Expiry day of an ES quarterly contract: the third Friday of its month."""
    d = dt.date(year, month, 1)
    fridays = [dt.date(year, month, day)
               for day in range(1, 29)
               if dt.date(year, month, day).weekday() == 4]
    return fridays[2]


def quarterly_contracts(start: dt.date, end: dt.date, roll_days: int = 8):
    """Yield (contract_month, active_start, active_end) covering [start, end].

    A contract is "active" from the previous contract's roll date until its own
    roll date (`roll_days` calendar days before its third-Friday expiry) — the
    window in which it carries the volume.
    """
    quarters = []
    for year in range(start.year - 1, end.year + 1):
        for month in (3, 6, 9, 12):
            expiry = third_friday(year, month)
            quarters.append((f"{year}{month:02d}", expiry - dt.timedelta(days=roll_days)))
    out = []
    for i in range(1, len(quarters)):
        month_code, roll = quarters[i]
        prev_roll = quarters[i - 1][1]
        active_start, active_end = prev_roll, roll
        if active_end <= start or active_start >= end:
            continue
        out.append((month_code, max(active_start, start), min(active_end, end)))
    return out


def _qualified_es(ib, expiry: str | None, exchange: str = "CME"):
    """Return a fully qualified ES contract.

    With `expiry` set (e.g. "202409" or "20240920"), qualifies that specific
    contract — `includeExpired=True` so past expiries used for backtest data
    still resolve. With `expiry=None`, resolves the FRONT MONTH explicitly from
    contract details (nearest expiry >= today) instead of leaving IBKR an
    ambiguous contract.
    """
    from ib_insync import Future

    if expiry:
        contract = Future(symbol="ES", exchange=exchange, currency="USD",
                          lastTradeDateOrContractMonth=expiry, includeExpired=True)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"IBKR could not qualify ES contract for expiry {expiry!r}.")
        return qualified[0]

    details = ib.reqContractDetails(Future(symbol="ES", exchange=exchange, currency="USD"))
    if not details:
        raise RuntimeError("IBKR returned no ES contract details — check market data permissions.")
    today = dt.date.today().strftime("%Y%m%d")
    contracts = sorted((d.contract for d in details),
                       key=lambda c: c.lastTradeDateOrContractMonth)
    front = next((c for c in contracts if c.lastTradeDateOrContractMonth >= today),
                 contracts[-1])
    print(f"resolved front month: ES {front.lastTradeDateOrContractMonth}")
    return front


def _bars_to_df(bars) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": b.date,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
    )


def _parse_duration_days(duration: str) -> int | None:
    """'60 D' -> 60; returns None for non-day durations ('1 W', '6 M', ...)."""
    m = re.fullmatch(r"(\d+)\s*D", duration.strip().upper())
    return int(m.group(1)) if m else None


def _fetch_history(ib, contract, end, duration, bar_size, what_to_show, use_rth, timeout):
    """reqHistoricalData with chunking for long intraday pulls.

    IBKR rejects/paces large 1-min requests; anything over CHUNK_DAYS is split
    into consecutive requests walking back from `end`.
    """
    days = _parse_duration_days(duration)
    intraday = "min" in bar_size or "sec" in bar_size or "hour" in bar_size

    def req(end_dt, dur):
        return ib.reqHistoricalData(
            contract,
            endDateTime=end_dt,
            durationStr=dur,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=2,  # UTC epoch -> tz-aware
            timeout=timeout,
        )

    if not (intraday and days and days > CHUNK_DAYS):
        return req(end, duration)

    all_bars: list = []
    end_dt = end
    remaining = days
    while remaining > 0:
        chunk = min(CHUNK_DAYS, remaining)
        bars = req(end_dt, f"{chunk} D")
        if not bars:
            # A silently truncated history would look like a complete dataset
            # to every downstream script — fail loudly instead.
            raise RuntimeError(
                f"historical chunk ending {end_dt or 'now'} returned no bars with "
                f"{remaining} of {days} days still to fetch. Likely causes: IBKR "
                "pacing (wait a minute and retry), the contract has no data that "
                "far back, or a timeout — partial data is NOT saved."
            )
        all_bars = list(bars) + all_bars
        end_dt = bars[0].date  # earliest bar of this chunk = end of the next one
        remaining -= chunk
    return all_bars


def fetch_es_bars(
    end: str = "",
    duration: str = "30 D",
    bar_size: str = "1 min",
    expiry: str | None = None,
    host: str = "127.0.0.1",
    port: int = 7497,
    client_id: int = 17,
    what_to_show: str = "TRADES",
    use_rth: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> pd.DataFrame:
    """Pull ES bars for ONE contract from IBKR; returns a UTC OHLCV frame.

    `expiry` is the contract month, e.g. "202409" or "20240920"; None resolves
    the front month explicitly. `use_rth=False` keeps the full electronic
    session so the 09:30 ET open is always present.

    WARNING: a single contract only carries the volume for ~3 months. For
    longer histories use `fetch_es_stitched`, or the backtest will run on
    thin back-month prints around the roll.
    """
    from ib_insync import IB

    ib = IB()
    ib.connect(host, port, clientId=client_id)
    try:
        contract = _qualified_es(ib, expiry)
        bars = _fetch_history(ib, contract, end, duration, bar_size,
                              what_to_show, use_rth, timeout)
    finally:
        ib.disconnect()

    if not bars:
        raise RuntimeError("IBKR returned no bars — check contract/expiry and market data permissions.")

    df = _bars_to_df(bars)
    df = df.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")
    return df.reset_index(drop=True)


def fetch_es_stitched(
    start: str,
    end: str = "",
    bar_size: str = "1 min",
    roll_days: int = 8,
    host: str = "127.0.0.1",
    port: int = 7497,
    client_id: int = 17,
    what_to_show: str = "TRADES",
    use_rth: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    allow_gaps: bool = False,
) -> pd.DataFrame:
    """Roll-correct ES history from `start` to `end` (dates as YYYY-MM-DD).

    Fetches each quarterly contract with includeExpired=True and keeps only the
    bars from its active (front-month) window, then stitches the windows into
    one continuous frame. No back-adjustment is applied — ORB trades intraday
    and is flat overnight, so roll gaps between sessions don't touch PnL.
    """
    from ib_insync import IB

    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end) if end else dt.date.today()
    if start_d >= end_d:
        raise ValueError(f"start {start_d} must be before end {end_d}")

    windows = quarterly_contracts(start_d, end_d, roll_days)
    if not windows:
        raise ValueError(f"no ES contract windows cover {start_d} -> {end_d}")

    ib = IB()
    ib.connect(host, port, clientId=client_id)
    frames = []
    try:
        for month_code, win_start, win_end in windows:
            contract = _qualified_es(ib, month_code)
            span_days = (win_end - win_start).days + 1
            # endDateTime just past the window end (UTC midnight after win_end)
            end_dt = dt.datetime.combine(win_end + dt.timedelta(days=1),
                                         dt.time(0, 0), dt.timezone.utc)
            bars = _fetch_history(ib, contract, end_dt, f"{span_days} D",
                                  bar_size, what_to_show, use_rth, timeout)
            if not bars:
                if allow_gaps:
                    print(f"  WARNING: no bars for ES {month_code} "
                          f"({win_start} -> {win_end}) — window skipped (--allow-gaps)")
                    continue
                raise RuntimeError(
                    f"no bars for ES {month_code} ({win_start} -> {win_end}). "
                    "A stitched dataset silently missing a quarter would corrupt "
                    "the backtest. Retry, or pass allow_gaps/--allow-gaps to "
                    "accept the hole knowingly."
                )
            df = _bars_to_df(bars)
            ts = pd.to_datetime(df["timestamp"], utc=True)
            mask = (ts.dt.date >= win_start) & (ts.dt.date < win_end)
            df = df[mask]
            print(f"  ES {month_code}: {len(df):,} bars for {win_start} -> {win_end}")
            frames.append(df)
    finally:
        ib.disconnect()

    if not frames:
        raise RuntimeError("stitched fetch produced no bars at all")
    out = pd.concat(frames)
    out = out.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")
    return out.reset_index(drop=True)


def save_csv(df: pd.DataFrame, path: str) -> None:
    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


if __name__ == "__main__":
    out = "data/es_1min.csv"
    df = fetch_es_bars()
    save_csv(df, out)
    print(f"saved {len(df):,} bars -> {out}")
