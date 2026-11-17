"""
SMCDaily - a Smart Money Concepts strategy for the daily timeframe.

Concepts implemented (all computed without lookahead):

* Swing points   - a swing high/low at bar t-k is only *confirmed* at bar t, once k candles
                   on its right side have closed.
* Market structure
    - BOS   (Break of Structure)   : close breaks the last swing in the direction of the trend.
    - CHoCH (Change of Character)  : close breaks the last swing against the trend -> trend flips.
* Order block (OB)    - the last bearish candle before the impulse that produced a bullish break.
* Fair value gap (FVG)- 3-candle imbalance: low[t] > high[t-2].
* Liquidity sweep     - wick below the last swing low that closes back above it (sell-side grab).

Long entries (spot, long only) require bullish structure, plus one of
(only ob_retest is enabled by default; see use_fvg / use_sweep):
    ob_retest  : price trades back into an unmitigated bullish order block
    fvg_retest : price trades back into an unfilled bullish FVG
    sweep      : sell-side liquidity sweep of the last swing low
Filters: pair closes above its 50-day EMA, and BTC closes above its 200-day EMA
(market regime - no new longs in a BTC downtrend).

Risk management:
    * Stop-loss sits just below the zone that justified the entry (OB / FVG bottom or sweep wick).
    * Take-profit at `rr` x the initial risk.
    * Exit signal on a bearish CHoCH (structure flips down).
"""

from datetime import datetime

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IStrategy,
    merge_informative_pair,
    stoploss_from_absolute,
    timeframe_to_minutes,
)


class SMCDaily(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = False

    # Exits are driven by structure / RR targets, so ROI is effectively disabled.
    minimal_roi = {"0": 100}
    # Hard cap. The real stop is the structural one set in custom_stoploss.
    stoploss = -0.25
    use_custom_stoploss = True
    trailing_stop = False

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    process_only_new_candles = True

    # Structure settings (fixed: they shape the indicators themselves)
    swing_length = 3  # candles on each side to confirm a swing point
    ob_lookback = 10  # how far back from a break to search for the order block candle
    ema_period = 50
    # Market regime filter: no new longs while BTC closes below its 200-DAY EMA.
    # Structure settings above are in candles of the chart timeframe; this one is in days, and
    # is converted to candles in __init__ so the filter means the same thing on any timeframe
    # (e.g. --timeframe 4h -> 1200-candle EMA).
    btc_pair = "BTC/USDT"
    btc_ema_days = 200

    startup_candle_count: int = 210

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        tf_minutes = timeframe_to_minutes(config.get("timeframe", self.timeframe))
        self.btc_ema_period = max(2, round(self.btc_ema_days * 1440 / tf_minutes))
        self.startup_candle_count = max(self.ema_period, self.btc_ema_period) + 10

    # Hyperoptable parameters
    use_ob = BooleanParameter(default=True, space="buy")
    # FVG and sweep entries lost money in backtests (2021-26), so only OB retests are on by default
    use_fvg = BooleanParameter(default=False, space="buy")
    use_sweep = BooleanParameter(default=False, space="buy")
    use_ema_filter = BooleanParameter(default=True, space="buy")
    use_btc_filter = BooleanParameter(default=True, space="buy")
    max_risk = DecimalParameter(0.04, 0.20, default=0.12, decimals=2, space="buy")

    sl_buffer = DecimalParameter(0.0, 0.03, default=0.01, decimals=3, space="sell")
    rr = DecimalParameter(1.0, 5.0, default=2.5, decimals=1, space="sell")

    plot_config = {
        "main_plot": {
            "swing_high": {"color": "red"},
            "swing_low": {"color": "green"},
            "ob_top": {"color": "blue"},
            "ob_bot": {"color": "blue"},
            "ema": {"color": "orange"},
        },
        "subplots": {"Structure": {"trend": {"color": "purple"}}},
    }

    # ------------------------------------------------------------------ indicators
    def _smart_money(self, df: DataFrame) -> DataFrame:
        k = self.swing_length
        o = df["open"].to_numpy()
        h = df["high"].to_numpy()
        lo = df["low"].to_numpy()
        c = df["close"].to_numpy()
        n = len(df)

        # Swing pivot centred at i-k, confirmed at i (window [i-2k, i]).
        win = 2 * k + 1
        is_ph = (df["high"].shift(k) == df["high"].rolling(win).max()).to_numpy()
        is_pl = (df["low"].shift(k) == df["low"].rolling(win).min()).to_numpy()

        out = {
            name: np.full(n, np.nan)
            for name in ("swing_high", "swing_low", "ob_top", "ob_bot", "fvg_top", "fvg_bot")
        }
        for name in ("trend", "bos_up", "choch_up", "choch_down", "ob_touch", "fvg_touch",
                     "sweep"):
            out[name] = np.zeros(n)
        out["entry_sl"] = np.full(n, np.nan)

        trend = 0
        sh = sl = np.nan
        sh_broken = sl_broken = sl_swept = False
        ob = None  # [top, bottom, created_idx, used]
        fvg = None

        for i in range(n):
            # 1. new confirmed swing points
            if is_ph[i]:
                sh, sh_broken = h[i - k], False
            if is_pl[i]:
                sl, sl_broken, sl_swept = lo[i - k], False, False

            # 2. structure breaks (on close)
            if not np.isnan(sh) and not sh_broken and c[i] > sh:
                sh_broken = True
                if trend == 1:
                    out["bos_up"][i] = 1
                else:
                    out["choch_up"][i] = 1
                trend = 1
                # order block = last bearish candle before the break
                for j in range(i - 1, max(i - 1 - self.ob_lookback, -1), -1):
                    if c[j] < o[j]:
                        ob = [h[j], lo[j], i, False]
                        break
            if not np.isnan(sl) and not sl_broken and c[i] < sl:
                sl_broken = True
                if trend == 1:
                    out["choch_down"][i] = 1
                trend = -1
                ob = fvg = None  # bullish zones no longer valid

            # 3. zone invalidation (closed through the bottom)
            if ob is not None and c[i] < ob[1]:
                ob = None
            if fvg is not None and c[i] < fvg[1]:
                fvg = None

            # 4. entry triggers (only in bullish structure)
            if trend == 1:
                if ob is not None and not ob[3] and i > ob[2] and lo[i] <= ob[0] and c[i] > ob[1]:
                    out["ob_touch"][i] = 1
                    ob[3] = True
                    out["entry_sl"][i] = ob[1]
                if (fvg is not None and not fvg[3] and i > fvg[2] and lo[i] <= fvg[0]
                        and c[i] >= fvg[1]):
                    out["fvg_touch"][i] = 1
                    fvg[3] = True
                    if np.isnan(out["entry_sl"][i]):
                        out["entry_sl"][i] = fvg[1]
                if not np.isnan(sl) and not sl_swept and lo[i] < sl < c[i]:
                    out["sweep"][i] = 1
                    sl_swept = True
                    out["entry_sl"][i] = lo[i]  # wick low is the invalidation

            # 5. new bullish FVG (newest one replaces the old)
            if i >= 2 and lo[i] > h[i - 2] and trend == 1:
                fvg = [lo[i], h[i - 2], i, False]

            out["trend"][i] = trend
            out["swing_high"][i] = sh
            out["swing_low"][i] = sl
            if ob is not None:
                out["ob_top"][i], out["ob_bot"][i] = ob[0], ob[1]
            if fvg is not None:
                out["fvg_top"][i], out["fvg_bot"][i] = fvg[0], fvg[1]

        return pd.concat([df, pd.DataFrame(out, index=df.index)], axis=1)

    def informative_pairs(self):
        return [(self.btc_pair, self.timeframe)]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self._smart_money(dataframe)
        dataframe["ema"] = ta.EMA(dataframe, timeperiod=self.ema_period)

        btc = self.dp.get_pair_dataframe(self.btc_pair, self.timeframe)
        btc = btc[["date", "close"]].copy()
        btc["bull"] = (btc["close"] > ta.EMA(btc, timeperiod=self.btc_ema_period)).astype(int)
        dataframe = merge_informative_pair(
            dataframe, btc[["date", "bull"]], self.timeframe, self.timeframe, ffill=True,
            append_timeframe=False, suffix="btc",
        )
        return dataframe

    # ------------------------------------------------------------------ signals
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        base = (
            (dataframe["trend"] == 1)
            & (dataframe["volume"] > 0)
            & dataframe["entry_sl"].notna()
            # risk to the structural stop must be acceptable
            & ((dataframe["close"] - dataframe["entry_sl"]) / dataframe["close"]
               <= self.max_risk.value)
        )
        if self.use_ema_filter.value:
            base &= dataframe["close"] > dataframe["ema"]
        if self.use_btc_filter.value:
            base &= dataframe["bull_btc"] == 1

        # later assignments win, so order = lowest -> highest priority tag
        for flag, col, tag in (
            (self.use_fvg.value, "fvg_touch", "fvg_retest"),
            (self.use_ob.value, "ob_touch", "ob_retest"),
            (self.use_sweep.value, "sweep", "sweep"),
        ):
            if flag:
                cond = base & (dataframe[col] == 1)
                dataframe.loc[cond, ["enter_long", "enter_tag"]] = (1, tag)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["choch_down"] == 1, ["exit_long", "exit_tag"]] = (1, "choch_down")
        return dataframe

    # ------------------------------------------------------------------ risk
    def _entry_stop(self, pair: str, trade: Trade) -> float | None:
        """Structural stop price, read from the signal candle that opened the trade."""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        signal = df.loc[df["date"] < trade.open_date_utc]
        if signal.empty or np.isnan(signal["entry_sl"].iat[-1]):
            return None
        return signal["entry_sl"].iat[-1] * (1 - self.sl_buffer.value)

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> float | None:
        stop = self._entry_stop(pair, trade)
        if stop is None:
            return None
        return stoploss_from_absolute(stop, current_rate, is_short=trade.is_short,
                                      leverage=trade.leverage)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> str | None:
        stop = self._entry_stop(pair, trade)
        if stop is None or stop >= trade.open_rate:
            return None
        target = trade.open_rate + self.rr.value * (trade.open_rate - stop)
        if current_rate >= target:
            return "take_profit_rr"
        return None
