import argparse
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

DB_PATH = Path(__file__).resolve().parent / "data" / "ticks.db"
MIN_INTERVAL = 30
MAX_BACKOFF = 900
COLUMNS = ["ticker", "bar_ts", "price", "volume", "session_open"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    ticker TEXT NOT NULL,
    bar_ts TEXT NOT NULL,
    price REAL NOT NULL,
    volume REAL,
    session_open REAL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (ticker, bar_ts)
)
"""

UPSERT = """
INSERT INTO ticks (ticker, bar_ts, price, volume, session_open, recorded_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker, bar_ts) DO UPDATE SET
    price = excluded.price,
    volume = excluded.volume,
    recorded_at = excluded.recorded_at
"""


def _connect(db_path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    return conn


def _field(raw: pd.DataFrame, name: str, ticker: str) -> pd.Series:
    block = raw[name]
    return block[ticker] if isinstance(block, pd.DataFrame) else block


def fetch_latest(tickers) -> pd.DataFrame:
    tickers = list(dict.fromkeys(str(t).strip().upper() for t in tickers))
    raw = yf.download(
        tickers, period="1d", interval="1m", auto_adjust=True,
        progress=False, group_by="column", threads=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows = []
    for ticker in tickers:
        try:
            close = _field(raw, "Close", ticker).dropna()
            if close.empty:
                continue
            opens = _field(raw, "Open", ticker).dropna()
            volume = _field(raw, "Volume", ticker).reindex(close.index).fillna(0)
        except KeyError:
            continue
        ts = pd.Timestamp(close.index[-1])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        rows.append({
            "ticker": ticker,
            "bar_ts": ts.isoformat(),
            "price": float(close.iloc[-1]),
            "volume": float(volume.iloc[-1]),
            "session_open": float(opens.iloc[0]) if not opens.empty else float(close.iloc[0]),
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def save_ticks(quotes: pd.DataFrame, db_path=DB_PATH) -> int:
    if quotes.empty:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (r.ticker, r.bar_ts, r.price, r.volume, r.session_open, now)
        for r in quotes.itertuples(index=False)
    ]
    with closing(_connect(db_path)) as conn, conn:
        conn.executemany(UPSERT, rows)
    return len(rows)


def read_ticks(tickers=None, db_path=DB_PATH) -> pd.DataFrame:
    columns = ["ticker", "bar_ts", "price", "volume", "recorded_at"]
    if not Path(db_path).exists():
        return pd.DataFrame(columns=columns)
    query = "SELECT ticker, bar_ts, price, volume, recorded_at FROM ticks"
    params = []
    if tickers:
        params = [str(t).upper() for t in tickers]
        query += f" WHERE ticker IN ({','.join('?' * len(params))})"
    query += " ORDER BY bar_ts"
    with closing(_connect(db_path)) as conn:
        df = pd.read_sql_query(query, conn, params=params)
    df["bar_ts"] = pd.to_datetime(df["bar_ts"], utc=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Yahoo Finance and record latest 1-minute prices to SQLite.")
    parser.add_argument("--tickers", required=True, help="Comma-separated symbols, e.g. AAPL,MSFT,NVDA")
    parser.add_argument("--interval", type=int, default=60, help=f"Seconds between polls (min {MIN_INTERVAL})")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite file path")
    parser.add_argument("--once", action="store_true", help="Poll a single time and exit")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    interval = max(args.interval, MIN_INTERVAL)
    failures = 0

    while True:
        try:
            quotes = fetch_latest(tickers)
            saved = save_ticks(quotes, args.db)
            failures = 0
            print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  saved {saved}/{len(tickers)} quotes", flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            failures += 1
            print(f"poll failed ({failures}): {exc}", file=sys.stderr, flush=True)
        if args.once:
            break
        try:
            time.sleep(min(interval * 2 ** failures, MAX_BACKOFF) if failures else interval)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("stopped")
