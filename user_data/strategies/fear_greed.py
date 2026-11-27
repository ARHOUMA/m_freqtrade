"""
Crypto Fear & Greed Index (alternative.me) as a market-sentiment column for strategies.

The index is one number per day, 0 (extreme fear) to 100 (extreme greed), published at
00:00 UTC and timestamped with that day's date. History goes back to 2018-02-01.

History is cached in user_data/data/sentiment/fear_greed.csv. Backtests read only that file
(run `python user_data/strategies/fear_greed.py` once to create or update it); dry-run / live
refresh it from the API when it is more than 6 hours old, and keep using the cached file if
the API is down.

Lookahead: the value dated D is published at the *start* of day D, so the 1d candle dated D
(open D 00:00, close D+1 00:00, signal acted on at D+1 open) may use it. fng_for_candles()
maps each candle to the latest value whose date is <= the candle's open time, which is safe on
any timeframe.

This file holds no strategy; freqtrade skips it when it looks for strategy classes.
"""

import json
import logging
import time
import urllib.request
from pathlib import Path

import pandas as pd
from pandas import DataFrame


logger = logging.getLogger(__name__)

API_URL = "https://api.alternative.me/fng/?limit=0&format=json"
CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "sentiment" / "fear_greed.csv"
REFRESH_SECONDS = 6 * 3600


def download(path: Path = CSV_PATH) -> DataFrame:
    """Fetch the full history from the API and save it to `path`."""
    req = urllib.request.Request(API_URL, headers={"User-Agent": "freqtrade-strategy"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.load(resp)["data"]
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([int(r["timestamp"]) for r in rows], unit="s", utc=True),
            "fng": [int(r["value"]) for r in rows],
        }
    )
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def load(refresh: bool) -> DataFrame:
    """History as a DataFrame [date, fng]. With refresh=True (dry-run / live), re-download
    when the cached file is missing or older than REFRESH_SECONDS."""
    stale = not CSV_PATH.exists() or time.time() - CSV_PATH.stat().st_mtime > REFRESH_SECONDS
    if refresh and stale or not CSV_PATH.exists():
        try:
            return download()
        except Exception as e:  # network / API problems must not stop the bot
            if not CSV_PATH.exists():
                raise RuntimeError(
                    f"No Fear & Greed history at {CSV_PATH} and the download failed: {e}"
                ) from e
            logger.warning("Fear & Greed refresh failed, using cached file: %s", e)
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df


def fng_for_candles(dates: pd.Series, fng: DataFrame) -> pd.Series:
    """The latest index value dated at or before each candle's open, aligned to `dates`.
    Candles more than 3 days past the last known value get NaN (stale data)."""
    unit = "datetime64[ns, UTC]"  # candle dates may be ms resolution; merge_asof needs a match
    out = pd.merge_asof(
        dates.astype(unit).to_frame("date").sort_values("date"),
        fng.rename(columns={"date": "fng_date"}).astype({"fng_date": unit}),
        left_on="date",
        right_on="fng_date",
        direction="backward",
        tolerance=pd.Timedelta(days=3),
    )
    return pd.Series(out["fng"].to_numpy(), index=dates.sort_values().index).reindex(dates.index)


if __name__ == "__main__":
    d = download()
    print(f"saved {len(d)} days ({d.date.min():%Y-%m-%d} .. {d.date.max():%Y-%m-%d}) to {CSV_PATH}")
