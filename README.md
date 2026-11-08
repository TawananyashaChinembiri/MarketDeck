# Streamlit Portfolio Dashboard

An interactive web app where you enter tickers and share counts and get candlestick charts, portfolio performance, an allocation chart, key fundamentals and a near-live price feed that can be recorded to a local database. All market data comes from [yfinance](https://github.com/ranaroussi/yfinance) and is cached with `st.cache_data`.

## Features

| Tab | What it shows |
|---|---|
| **Candlestick** | OHLC candles with volume bars and selectable moving averages (10/20/50/100/200), from 1-minute to monthly bars |
| **Performance** | Growth of 100 for the portfolio vs each holding, drawdown curve, total return, CAGR, annualised volatility, Sharpe ratio (rf = 0), max drawdown |
| **Allocation** | Donut chart and table of current value and weight per position |
| **Fundamentals** | Market cap, P/E, forward P/E, P/B, EPS, beta, margins, ROE, debt/equity, dividend yield, 52-week range |
| **Live feed** | Latest price and change vs session open per ticker, auto-refreshing on a timer, with optional recording and a chart and CSV download of recorded ticks |

The portfolio is modelled as buy-and-hold: the share counts you enter are held constant across the selected period. Volatility is annualised with the number of bars per year for the chosen interval.

## Project structure

```
portfolio-dashboard/
├── app.py                      # Streamlit application
├── recorder.py                 # Latest-quote fetch, SQLite storage, background recorder CLI
├── portfolio_dashboard.ipynb   # Notebook walkthrough of the same logic
├── requirements.txt
├── README.md
└── data/ticks.db               # Created automatically when you record
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501. Edit the table in the sidebar (add rows with the `+` at the bottom) to change tickers and shares, then pick an interval and period.

To explore the logic step by step:

```bash
jupyter notebook portfolio_dashboard.ipynb
```

## Intraday intervals

Yahoo Finance limits how far back each interval goes, so the period choices change with the interval.

| Interval | Periods offered | Yahoo's limit |
|---|---|---|
| 1 min | 1d, 5d, 7d | about 7 days |
| 5 min, 15 min | 1d, 5d, 1mo | about 60 days |
| 1 hour | 5d to 2y | about 730 days |
| Daily, Weekly, Monthly | 1mo to max | none |

Weekends are hidden on intraday and daily candlesticks. Overnight gaps are not, because trading hours differ by exchange.

## Live mode and recording

In the sidebar:

- **Auto-refresh live feed** re-polls the Live feed tab every 30-300 seconds using `st.fragment(run_every=...)`. Only that tab refreshes, not the whole page.
- **Record ticks to SQLite** saves each poll to `data/ticks.db`. The table keeps one row per ticker per 1-minute bar, and a row is updated with the latest price until the bar closes, so repeated polls never create duplicates.

The tab charts recorded prices (first tick = 100) and offers a CSV download.

### Recording without a browser

Streamlit only polls while a browser tab is open. To record continuously, run the recorder as its own process:

```bash
python recorder.py --tickers AAPL,MSFT,NVDA,JPM --interval 60
```

| Option | Meaning |
|---|---|
| `--tickers` | Comma-separated symbols |
| `--interval` | Seconds between polls (minimum 30, default 60) |
| `--db` | SQLite path (default `data/ticks.db`, shared with the app) |
| `--once` | Poll once and exit, useful for cron |

The recorder fetches all tickers in a single batched request per poll and backs off exponentially (up to 15 minutes) after failures. Stop it with Ctrl+C. Keep it alive with `nohup`, `tmux`, a systemd service or a cron job.

## Caching strategy

Yahoo Finance throttles aggressive clients, so the app is built to make as few requests as possible.

| Function | Cache key | TTL | Reason |
|---|---|---|---|
| `history_slow` (daily, weekly, monthly) | ticker, period, interval | 15 min | Prices change intraday, but not fast enough to justify refetching on every widget interaction |
| `history_fast` (1m, 5m, 15m, 1h) | ticker, period, interval | 60 s | Intraday bars go stale quickly |
| `load_latest` | tuple of tickers | 20 s | One batched download for the whole portfolio; the minimum refresh of 30 s always gets fresh data |
| `load_fundamentals` | ticker | 6 hours | Fundamentals move slowly, and `Ticker.info` is the most expensive call |

Design choices that follow from this:

- **Per-ticker history caching.** Adding one ticker fetches only that ticker, not the whole set again.
- **Batched live quotes.** The live feed makes one request for all tickers, however many you hold.
- **Streamlit reruns the script on every interaction.** Without caching, changing a moving-average checkbox would trigger a network call per ticker.
- **Derived values are not cached.** Portfolio values, weights and metrics are cheap pandas operations on cached data.
- **Failures degrade gracefully.** If a ticker returns no data or `info` fails, the app warns and carries on with the remaining holdings.
- **Manual refresh.** The *Clear cache & refresh* button calls `st.cache_data.clear()`.

If you still hit rate limits, increase the TTL constants at the top of `app.py`, use fewer tickers, or lengthen the refresh interval.

## Notes and limitations

- This is near-live, not a streaming feed. Yahoo data can be delayed by about 15 minutes depending on the exchange, and polling is the only update mechanism. For true tick-by-tick data use a licensed provider.
- Live quotes use the last 1-minute bar of the most recent session. When the market is closed they show the previous session's last bar, and the timestamp in the tab tells you which.
- While Auto-refresh is on, the Live feed tab keeps polling even if you are viewing another tab.
- Recording writes to local disk. On hosts with an ephemeral filesystem (for example Streamlit Community Cloud) the database is lost on restart, so run the recorder on a machine you control.
- Prices are adjusted for splits and dividends (`auto_adjust=True`), so returns include dividends.
- yfinance is an unofficial wrapper around Yahoo Finance. Fields can be missing or change without notice, and the data is not guaranteed to be accurate. It is intended for education and personal analysis, not trading decisions.
- Mixing currencies is not handled. Values are summed as if every ticker were quoted in the same currency.
- Sharpe is the annualised mean return divided by annualised volatility, with a zero risk-free rate. CAGR is shown only when the selected period covers at least a year.
- Yahoo's terms of use apply to the data. Review them before any commercial use.

## Skills demonstrated

Streamlit app structure, state and fragments, yfinance data access, Plotly (candlestick, subplots, donut), caching with TTLs to respect rate limits, SQLite persistence with upserts, and basic portfolio analytics.
