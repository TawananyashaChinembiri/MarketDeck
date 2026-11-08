from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

from recorder import DB_PATH, fetch_latest, read_ticks, save_ticks

st.set_page_config(page_title="Portfolio Dashboard", page_icon="📈", layout="wide")

HISTORY_TTL = 15 * 60
INTRADAY_TTL = 60
LIVE_TTL = 20
FUNDAMENTALS_TTL = 6 * 60 * 60

INTERVALS = {
    "1 min": "1m",
    "5 min": "5m",
    "15 min": "15m",
    "1 hour": "1h",
    "Daily": "1d",
    "Weekly": "1wk",
    "Monthly": "1mo",
}
INTRADAY = {"1m", "5m", "15m", "1h"}

LONG_PERIODS = ["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]
PERIOD_OPTIONS = {
    "1m": ["1d", "5d", "7d"],
    "5m": ["1d", "5d", "1mo"],
    "15m": ["1d", "5d", "1mo"],
    "1h": ["5d", "1mo", "3mo", "6mo", "1y", "2y"],
    "1d": LONG_PERIODS,
    "1wk": LONG_PERIODS,
    "1mo": LONG_PERIODS,
}
DEFAULT_PERIOD = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "1mo", "1d": "1y", "1wk": "2y", "1mo": "5y"}
PERIODS_PER_YEAR = {"1m": 252 * 390, "5m": 252 * 78, "15m": 252 * 26, "1h": 252 * 7, "1d": 252, "1wk": 52, "1mo": 12}

FUNDAMENTAL_FIELDS = {
    "longName": "Name",
    "sector": "Sector",
    "marketCap": "Market Cap",
    "trailingPE": "P/E (TTM)",
    "forwardPE": "Forward P/E",
    "priceToBook": "P/B",
    "trailingEps": "EPS (TTM)",
    "beta": "Beta",
    "profitMargins": "Profit Margin",
    "returnOnEquity": "ROE",
    "debtToEquity": "Debt/Equity",
    "fiftyTwoWeekHigh": "52W High",
    "fiftyTwoWeekLow": "52W Low",
}


def fetch_history(ticker: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[["Open", "High", "Low", "Close", "Volume"]]


@st.cache_data(ttl=HISTORY_TTL, show_spinner=False)
def history_slow(ticker: str, period: str, interval: str) -> pd.DataFrame:
    return fetch_history(ticker, period, interval)


@st.cache_data(ttl=INTRADAY_TTL, show_spinner=False)
def history_fast(ticker: str, period: str, interval: str) -> pd.DataFrame:
    return fetch_history(ticker, period, interval)


def load_history(ticker: str, period: str, interval: str) -> pd.DataFrame:
    loader = history_fast if interval in INTRADAY else history_slow
    return loader(ticker, period, interval)


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_latest(tickers: tuple[str, ...]) -> pd.DataFrame:
    return fetch_latest(tickers)


@st.cache_data(ttl=FUNDAMENTALS_TTL, show_spinner=False)
def load_fundamentals(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return {}
    row = {label: info.get(key) for key, label in FUNDAMENTAL_FIELDS.items()}
    rate, price = info.get("dividendRate"), info.get("currentPrice") or info.get("regularMarketPrice")
    row["Dividend Yield"] = rate / price if rate and price else None
    return row


def parse_portfolio(df: pd.DataFrame) -> dict[str, float]:
    holdings = {}
    for _, r in df.dropna(subset=["Ticker"]).iterrows():
        ticker = str(r["Ticker"]).strip().upper()
        shares = float(r["Shares"]) if pd.notna(r["Shares"]) else 0.0
        if ticker and shares > 0:
            holdings[ticker] = holdings.get(ticker, 0.0) + shares
    return holdings


def build_close_frame(histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    closes = pd.DataFrame({t: h["Close"] for t, h in histories.items() if not h.empty})
    return closes.sort_index().ffill().dropna()


def performance_metrics(values: pd.Series, periods_per_year: int) -> dict[str, float]:
    returns = values.pct_change().dropna()
    drawdown = values / values.cummax() - 1
    years = max((values.index[-1] - values.index[0]).days / 365.25, 1e-9)
    total = values.iloc[-1] / values.iloc[0] - 1
    vol = returns.std() * np.sqrt(periods_per_year) if len(returns) > 1 else np.nan
    cagr = (1 + total) ** (1 / years) - 1 if years >= 1 else np.nan
    sharpe = returns.mean() * periods_per_year / vol if len(returns) > 1 and vol else np.nan
    return {
        "Total return": total,
        "CAGR": cagr,
        "Volatility": vol,
        "Sharpe (rf=0)": sharpe,
        "Max drawdown": drawdown.min(),
    }


def candlestick_figure(df: pd.DataFrame, ticker: str, ma_windows: list[int], hide_weekends: bool) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name=ticker
        ),
        row=1, col=1,
    )
    for w in ma_windows:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["Close"].rolling(w).mean(), name=f"MA{w}", line=dict(width=1.3)),
            row=1, col=1,
        )
    colors = np.where(df["Close"] >= df["Open"], "#26a69a", "#ef5350")
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=colors, name="Volume", showlegend=False), row=2, col=1)
    if hide_weekends:
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_layout(
        height=640, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", y=1.02),
    )
    return fig


def performance_figure(values: pd.Series, closes: pd.DataFrame) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.65, 0.35], vertical_spacing=0.06,
                        subplot_titles=("Growth of 100 (portfolio vs holdings)", "Drawdown"))
    fig.add_trace(go.Scatter(x=values.index, y=values / values.iloc[0] * 100, name="Portfolio",
                             line=dict(width=3, color="#1f77b4")), row=1, col=1)
    for t in closes.columns:
        fig.add_trace(go.Scatter(x=closes.index, y=closes[t] / closes[t].iloc[0] * 100, name=t,
                                 line=dict(width=1), opacity=0.6), row=1, col=1)
    drawdown = values / values.cummax() - 1
    fig.add_trace(go.Scatter(x=drawdown.index, y=drawdown, fill="tozeroy", name="Drawdown",
                             line=dict(color="#ef5350"), showlegend=False), row=2, col=1)
    fig.update_yaxes(tickformat=".0%", row=2, col=1)
    fig.update_layout(height=620, margin=dict(l=10, r=10, t=40, b=10), hovermode="x unified")
    return fig


def allocation_figure(weights: pd.Series) -> go.Figure:
    fig = go.Figure(go.Pie(labels=weights.index, values=weights.values, hole=0.45, textinfo="label+percent"))
    fig.update_layout(height=480, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def ticks_figure(ticks: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for ticker, g in ticks.groupby("ticker"):
        fig.add_trace(go.Scatter(
            x=g["bar_ts"], y=g["price"] / g["price"].iloc[0] * 100, name=ticker,
            mode="lines+markers", marker=dict(size=4),
        ))
    fig.update_layout(
        height=420, hovermode="x unified", margin=dict(l=10, r=10, t=10, b=10),
        yaxis_title="Recorded price (first tick = 100)",
    )
    return fig


def format_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    pct = ["Profit Margin", "ROE", "Dividend Yield"]
    ratio = ["P/E (TTM)", "Forward P/E", "P/B", "EPS (TTM)", "Beta", "Debt/Equity", "52W High", "52W Low"]
    out = df.copy()
    for c in pct:
        out[c] = out[c].map(lambda v: f"{v:.2%}" if pd.notna(v) else "—")
    for c in ratio:
        out[c] = out[c].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "—")
    out["Market Cap"] = out["Market Cap"].map(lambda v: f"{v / 1e9:,.1f}B" if pd.notna(v) else "—")
    return out.fillna("—")


def live_panel(tickers: tuple[str, ...], record: bool) -> None:
    quotes = load_latest(tickers)
    if quotes.empty:
        st.warning("No intraday quotes returned. The market may be closed or Yahoo may be rate limiting.")
        return
    if record:
        save_ticks(quotes)

    for start in range(0, len(quotes), 6):
        chunk = quotes.iloc[start:start + 6]
        for col, r in zip(st.columns(len(chunk)), chunk.itertuples()):
            col.metric(r.ticker, f"{r.price:,.2f}", f"{r.price / r.session_open - 1:+.2%}")

    latest = quotes["bar_ts"].max()[:16].replace("T", " ")
    st.caption(f"Latest bar {latest} UTC · refreshed {datetime.now():%H:%M:%S} · change is vs session open")

    ticks = read_ticks(tickers)
    if ticks.empty:
        st.info("Turn on 'Record ticks' in the sidebar to build a local price history.")
        return
    st.plotly_chart(ticks_figure(ticks), use_container_width=True)
    st.download_button("Download recorded ticks (CSV)", ticks.to_csv(index=False), "ticks.csv", "text/csv")
    st.caption(f"{len(ticks):,} recorded rows in {DB_PATH}")


st.title("📈 Portfolio Dashboard")
st.caption("Market data from Yahoo Finance via yfinance · cached to respect rate limits")

with st.sidebar:
    st.header("Portfolio")
    default = pd.DataFrame({"Ticker": ["AAPL", "MSFT", "NVDA", "JPM"], "Shares": [10.0, 8.0, 5.0, 12.0]})
    editor = st.data_editor(default, num_rows="dynamic", use_container_width=True, hide_index=True)
    interval_label = st.selectbox("Interval", list(INTERVALS), index=4)
    interval = INTERVALS[interval_label]
    options = PERIOD_OPTIONS[interval]
    period = st.selectbox("Period", options, index=options.index(DEFAULT_PERIOD[interval]))
    ma_windows = st.multiselect("Moving averages", [10, 20, 50, 100, 200], default=[20, 50])

    st.header("Live mode")
    live = st.toggle("Auto-refresh live feed", help="Re-polls the Live feed tab on a timer while this page is open.")
    refresh_seconds = st.slider("Refresh every (seconds)", 30, 300, 60, step=15, disabled=not live)
    record = st.toggle("Record ticks to SQLite", help="Saves each poll to a local database.")

    if st.button("Clear cache & refresh"):
        st.cache_data.clear()
        st.rerun()

holdings = parse_portfolio(editor)
if not holdings:
    st.info("Add at least one ticker with a positive number of shares in the sidebar.")
    st.stop()

histories, failed = {}, []
with st.spinner("Loading market data..."):
    for ticker in holdings:
        df = load_history(ticker, period, interval)
        if df.empty:
            failed.append(ticker)
        else:
            histories[ticker] = df

if failed:
    st.warning(f"No data returned for: {', '.join(failed)}. Check the symbols or try again later.")
if not histories:
    st.stop()

closes = build_close_frame(histories)
shares = pd.Series({t: holdings[t] for t in closes.columns})
position_values = closes * shares
portfolio_values = position_values.sum(axis=1)
latest_values = position_values.iloc[-1]
weights = latest_values / latest_values.sum()
metrics = performance_metrics(portfolio_values, PERIODS_PER_YEAR[interval])

cols = st.columns(5)
cols[0].metric("Portfolio value", f"${portfolio_values.iloc[-1]:,.0f}")
cols[1].metric("Total return", f"{metrics['Total return']:.2%}")
cols[2].metric("Volatility (ann.)", f"{metrics['Volatility']:.2%}")
cols[3].metric("Sharpe", f"{metrics['Sharpe (rf=0)']:.2f}")
cols[4].metric("Max drawdown", f"{metrics['Max drawdown']:.2%}")

tab_chart, tab_perf, tab_alloc, tab_fund, tab_live = st.tabs(
    ["Candlestick", "Performance", "Allocation", "Fundamentals", "Live feed"]
)

with tab_chart:
    selected = st.selectbox("Ticker", list(histories))
    hide_weekends = interval in INTRADAY or interval == "1d"
    st.plotly_chart(
        candlestick_figure(histories[selected], selected, ma_windows, hide_weekends),
        use_container_width=True,
    )

with tab_perf:
    st.plotly_chart(performance_figure(portfolio_values, closes), use_container_width=True)
    st.dataframe(
        pd.Series(metrics, name="Value").to_frame().style.format("{:.2%}", subset=pd.IndexSlice[["Total return", "CAGR", "Volatility", "Max drawdown"], :], na_rep="—")
        .format("{:.2f}", subset=pd.IndexSlice[["Sharpe (rf=0)"], :], na_rep="—"),
        use_container_width=True,
    )

with tab_alloc:
    left, right = st.columns([3, 2])
    left.plotly_chart(allocation_figure(weights), use_container_width=True)
    table = pd.DataFrame({
        "Shares": shares,
        "Price": closes.iloc[-1],
        "Value": latest_values,
        "Weight": weights,
    })
    right.dataframe(
        table.style.format({"Shares": "{:,.2f}", "Price": "${:,.2f}", "Value": "${:,.0f}", "Weight": "{:.1%}"}),
        use_container_width=True,
    )

with tab_fund:
    rows = {t: load_fundamentals(t) for t in histories}
    fundamentals = pd.DataFrame.from_dict(rows, orient="index").reindex(
        columns=[*FUNDAMENTAL_FIELDS.values(), "Dividend Yield"]
    )
    if fundamentals.dropna(how="all").empty:
        st.warning("Fundamentals are unavailable right now (Yahoo may be rate limiting). Try again shortly.")
    else:
        st.dataframe(format_fundamentals(fundamentals), use_container_width=True)

with tab_live:
    panel = st.fragment(run_every=refresh_seconds if live else None)(live_panel)
    panel(tuple(histories), record)
