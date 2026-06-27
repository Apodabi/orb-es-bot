"""Interactive Brokers historical-data fetcher for ES futures.

Requires a running TWS or IB Gateway with the API enabled. Paper trading is the
default port (7497 for TWS paper, 4002 for Gateway paper). Live ports are
7496 / 4001.

    pip install ib_insync
    python -m orb.ibkr  # or use scripts/fetch_data.py
"""

from __future__ import annotations

import datetime as dt

import pandas as pd


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
) -> pd.DataFrame:
    """Pull ES bars from IBKR and return a UTC-indexed OHLCV frame.

    `expiry` is the contract month, e.g. "20240920"; if None, IBKR resolves the
    front month via a continuous-style ambiguity prompt, so passing an explicit
    expiry is recommended for reproducible backtests. `use_rth=False` keeps the
    full electronic session so the 09:30 ET open is always present.
    """
    from ib_insync import IB, Future  # imported lazily so the package loads without ib_insync

    ib = IB()
    ib.connect(host, port, clientId=client_id)
    try:
        contract = Future(symbol="ES", exchange="CME", currency="USD", lastTradeDateOrContractMonth=expiry or "")
        ib.qualifyContracts(contract)

        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=2,  # UTC epoch -> tz-aware
        )
    finally:
        ib.disconnect()

    if not bars:
        raise RuntimeError("IBKR returned no bars — check contract/expiry and market data permissions.")

    df = pd.DataFrame(
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
    return df


def save_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index=False)


if __name__ == "__main__":
    out = "data/es_1min.csv"
    df = fetch_es_bars()
    save_csv(df, out)
    print(f"saved {len(df):,} bars -> {out}")
