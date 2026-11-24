"""
Backtest dashboard - a small local web app to explore freqtrade backtest results.

    .venv/bin/python user_data/dashboard/app.py            # then open http://127.0.0.1:8050
    .venv/bin/python user_data/dashboard/app.py --port 9000

It lists every backtest result found under user_data/backtest_results and user_data/smc_lab
(any run made with --export trades), and shows for the selected strategy:
    * KPI cards, equity curve and drawdown, yearly and monthly returns
    * profit per pair, per exit reason and per entry tag, long vs short
    * a comparison of all strategies in the same run (return vs drawdown)
    * a price chart per pair with the trades marked on it, and the trade list

Read-only: it never changes results or data. Binds to localhost only.
"""

import argparse
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from freqtrade.data.btanalysis import load_backtest_stats
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEARCH_DIRS = [ROOT / "user_data" / "backtest_results", ROOT / "user_data" / "smc_lab"]
DATADIR = ROOT / "user_data" / "data" / "binance"
MAX_CANDLES = 4000

app = FastAPI(title="Backtest dashboard")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# ---------------------------------------------------------------------- helpers
def clean(obj):
    """Make stats JSON-safe (NaN / inf -> None)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean(v) for v in obj]
    return obj


def result_file(meta: Path) -> Path | None:
    base = meta.with_name(meta.name.removesuffix(".meta.json"))
    for ext in (".zip", ".json"):
        p = base.with_suffix(ext)
        if p.is_file():
            return p
    return None


def resolve_run(run_id: str) -> Path:
    path = (ROOT / run_id).resolve()
    if not any(path.is_relative_to(d.resolve()) for d in SEARCH_DIRS) or not path.is_file():
        raise HTTPException(404, "Unknown run")
    return path


@lru_cache(maxsize=16)
def _load(path: str, mtime: float) -> dict:
    return load_backtest_stats(Path(path))


def load_run(run_id: str) -> dict:
    path = resolve_run(run_id)
    return _load(str(path), path.stat().st_mtime)


def get_strategy(run_id: str, name: str) -> dict:
    stats = load_run(run_id)
    if name not in stats["strategy"]:
        raise HTTPException(404, "Unknown strategy")
    return stats["strategy"][name]


def kpis(s: dict) -> dict:
    dd = s.get("max_drawdown_account") or 0
    cagr = s.get("cagr") or 0
    return {
        "profit_total": s.get("profit_total"),
        "profit_total_abs": s.get("profit_total_abs"),
        "starting_balance": s.get("starting_balance"),
        "final_balance": s.get("final_balance"),
        "cagr": cagr,
        "max_drawdown": dd,
        "max_drawdown_abs": s.get("max_drawdown_abs"),
        "drawdown_start": s.get("drawdown_start"),
        "drawdown_end": s.get("drawdown_end"),
        "return_dd": cagr / dd if dd else None,
        "sharpe": s.get("sharpe"),
        "sortino": s.get("sortino"),
        "calmar": s.get("calmar"),
        "profit_factor": s.get("profit_factor"),
        "expectancy": s.get("expectancy"),
        "winrate": s.get("winrate"),
        "trades": s.get("total_trades"),
        "trades_long": s.get("trade_count_long"),
        "trades_short": s.get("trade_count_short"),
        "profit_long": s.get("profit_total_long"),
        "profit_short": s.get("profit_total_short"),
        "holding_avg": s.get("holding_avg"),
        "market_change": s.get("market_change"),
        "best_pair": (s.get("best_pair") or {}).get("key"),
        "worst_pair": (s.get("worst_pair") or {}).get("key"),
        "max_consecutive_losses": s.get("max_consecutive_losses"),
    }


def equity_series(s: dict, trades: pd.DataFrame) -> list[dict]:
    start = s["starting_balance"]
    pts = [{"t": s["backtest_start_ts"], "balance": start, "dd": 0.0}]
    if not trades.empty:
        bal = start + trades["profit_abs"].cumsum()
        peak = bal.cummax().clip(lower=start)
        for t, b, p in zip(trades["close_timestamp"], bal, peak, strict=True):
            pts.append({"t": int(t), "balance": float(b), "dd": float(b / p - 1)})
    pts.append({"t": s["backtest_end_ts"], "balance": pts[-1]["balance"], "dd": pts[-1]["dd"]})
    return pts


def period_returns(s: dict, trades: pd.DataFrame, freq: str) -> list[dict]:
    """Return % per calendar period, relative to the balance at the start of that period."""
    start = pd.Timestamp(s["backtest_start_ts"], unit="ms", tz="UTC")
    end = pd.Timestamp(s["backtest_end_ts"], unit="ms", tz="UTC")
    periods = pd.period_range(start.tz_localize(None), end.tz_localize(None), freq=freq)
    by = (
        trades.groupby(trades["close"].dt.tz_localize(None).dt.to_period(freq))["profit_abs"]
        if not trades.empty
        else None
    )
    sums = by.sum() if by is not None else pd.Series(dtype=float)
    counts = by.size() if by is not None else pd.Series(dtype=int)
    bal, out = s["starting_balance"], []
    for p in periods:
        prof = float(sums.get(p, 0.0))
        out.append({"period": str(p), "pct": prof / bal if bal else 0.0, "abs": prof,
                    "trades": int(counts.get(p, 0))})
        bal += prof
    return out


def trades_frame(s: dict) -> pd.DataFrame:
    t = pd.DataFrame(s.get("trades") or [])
    if t.empty:
        return t
    t["close"] = pd.to_datetime(t["close_date"], utc=True)
    return t.sort_values("close_timestamp").reset_index(drop=True)


def summary_rows(rows: list[dict]) -> list[dict]:
    keep = ("key", "trades", "profit_total_abs", "profit_total", "profit_mean", "winrate",
            "profit_factor", "duration_avg")
    return [{k: r.get(k) for k in keep} for r in rows if r.get("key") != "TOTAL"]


# ---------------------------------------------------------------------- API
@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/runs")
def runs():
    out = []
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for meta in d.rglob("*.meta.json"):
            res = result_file(meta)
            if res is None:
                continue
            try:
                info = pd.read_json(meta, typ="series").to_dict()
            except ValueError:
                continue
            strategies = list(info.keys())
            first = next(iter(info.values()), {}) or {}
            started = max((v or {}).get("backtest_start_time", 0) for v in info.values())
            folder = res.parent.relative_to(ROOT / "user_data")
            out.append({
                "id": str(res.relative_to(ROOT)),
                "folder": str(folder),
                "strategies": strategies,
                "timeframe": first.get("timeframe"),
                "started": started,
                "range": [first.get("backtest_start_ts"), first.get("backtest_end_ts")],
            })
    out.sort(key=lambda r: r["started"], reverse=True)
    return out


@app.get("/api/run")
def run(id: str = Query(...)):
    stats = load_run(id)
    rows = []
    for name, s in stats["strategy"].items():
        rows.append({"strategy": name, "trading_mode": s.get("trading_mode"),
                     "timeframe": s.get("timeframe"), **kpis(s)})
    return clean({"strategies": rows})


@app.get("/api/strategy")
def strategy(id: str = Query(...), name: str = Query(...)):
    s = get_strategy(id, name)
    t = trades_frame(s)
    trades = []
    for r in (t.to_dict("records") if not t.empty else []):
        trades.append({
            "pair": r["pair"], "side": "short" if r.get("is_short") else "long",
            "open": int(r["open_timestamp"]), "close": int(r["close_timestamp"]),
            "open_rate": r["open_rate"], "close_rate": r["close_rate"],
            "profit_pct": r["profit_ratio"], "profit_abs": r["profit_abs"],
            "exit_reason": r.get("exit_reason"), "enter_tag": r.get("enter_tag"),
            "duration_min": r.get("trade_duration"),
        })
    return clean({
        "meta": {
            "strategy": name, "timeframe": s.get("timeframe"),
            "trading_mode": s.get("trading_mode"), "timerange": s.get("timerange"),
            "start": s.get("backtest_start"), "end": s.get("backtest_end"),
            "pairs": s.get("pairlist"), "stake_currency": s.get("stake_currency"),
            "max_open_trades": s.get("max_open_trades"),
            "protections": s.get("enable_protections"),
        },
        "kpis": kpis(s),
        "equity": equity_series(s, t),
        "yearly": period_returns(s, t, "Y"),
        "monthly": period_returns(s, t, "M"),
        "pairs": summary_rows(s.get("results_per_pair", [])),
        "exits": summary_rows(s.get("exit_reason_summary", [])),
        "tags": summary_rows(s.get("results_per_enter_tag", [])),
        "trades": trades,
    })


@app.get("/api/candles")
def candles(id: str = Query(...), name: str = Query(...), pair: str = Query(...)):
    s = get_strategy(id, name)
    if pair not in (s.get("pairlist") or []):
        raise HTTPException(404, "Pair not in this backtest")
    ctype = CandleType.FUTURES if s.get("trading_mode") == "futures" else CandleType.SPOT
    df = load_pair_history(pair, s["timeframe"], DATADIR, candle_type=ctype)
    if df.empty:
        raise HTTPException(404, "No candle data for this pair")
    start = pd.Timestamp(s["backtest_start_ts"], unit="ms", tz="UTC")
    end = pd.Timestamp(s["backtest_end_ts"], unit="ms", tz="UTC")
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    step = s["timeframe"]
    if len(df) > MAX_CANDLES:  # resample long intraday runs so the chart stays fast
        span_min = (df["date"].iloc[-1] - df["date"].iloc[0]).total_seconds() / 60
        steps = (15, 30, 60, 120, 240, 360, 720, 1440, 4320, 10080)
        minutes = next((m for m in steps if span_min / m <= MAX_CANDLES), steps[-1])
        df = (df.set_index("date")
                .resample(f"{minutes}min")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                .dropna().reset_index())
        step = f"{minutes}m"
    return clean({
        "step": step,
        "t": (df["date"].astype("int64") // 10**6).tolist(),
        "o": df["open"].tolist(), "h": df["high"].tolist(),
        "l": df["low"].tolist(), "c": df["close"].tolist(),
    })


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--port", type=int, default=8050)
    args = ap.parse_args()
    print(f"Backtest dashboard on http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
