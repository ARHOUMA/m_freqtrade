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

Short entries (futures only - see config_smc_futures.json) mirror the order-block long:
    ob_retest_short : in bearish structure, price trades back up into an unmitigated bearish
                      order block (the last bullish candle before the impulse that broke a
                      swing low). Pair below its 50-day EMA, BTC below its 200-day EMA.
FVG / sweep entries are long-only.

Risk management (both sides):
    * Stop-loss sits just beyond the zone that justified the entry (OB / FVG edge or sweep wick).
    * Take-profit at `rr` x the initial risk.
    * Exit signal on a CHoCH against the position (structure flips).
    * Circuit breaker (freqtrade StoplossGuard): after 2 stop-outs within 20 days, no new
      entries on any pair for 10 days. Chosen from ~40 drawdown experiments
      (user_data/strategies/smc_variants.py); it cut max drawdown on every pair set tested.
      Backtests only apply it with --enable-protections; dry-run / live always apply it.

Optional sentiment filter (use_fng_filter, off by default): the Crypto Fear & Greed Index
(see fear_greed.py). Longs only while fng_long_min <= index <= fng_long_max, shorts only
while fng_short_min <= index <= fng_short_max. The defaults (80 / 20 caps, 0 / 100 floors)
block longs in euphoria and shorts in capitulation; raising the long floor / lowering the
short cap instead uses sentiment as trend confirmation. Days without index data never block.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
    IStrategy,
    merge_informative_pair,
    stoploss_from_absolute,
    timeframe_to_minutes,
    timeframe_to_prev_date,
)
from freqtrade.enums import RunMode


sys.path.insert(0, str(Path(__file__).parent))
import fear_greed  # noqa: E402


logger = logging.getLogger(__name__)

class SMCDaily(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = False  # turned on in __init__ when running in futures mode

    # Exits are driven by structure / RR targets, so ROI is effectively disabled.
    minimal_roi = {"0": 100}
    # Hard cap: a 25% price move against the trade (scaled by leverage in __init__, because
    # freqtrade measures stoploss on the margin). The real stop is the structural one set in
    # custom_stoploss.
    stoploss = -0.25
    # Leverage for futures trades (spot is always 1x). Structural stops and RR targets are set
    # in price, so leverage changes position size and P&L, not where trades exit. Kept at 1x:
    # 2x / 3x backtests (smc_leverage_lab.py) more than doubled max drawdown (to 50-85%) and
    # lowered CAGR / drawdown on every futures pair set.
    futures_leverage = 1.0
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
    # Market regime filter: longs only while BTC closes above its 200-DAY EMA, shorts only
    # while it closes below.
    # Structure settings above are in candles of the chart timeframe; this one is in days, and
    # is converted to candles in __init__ so the filter means the same thing on any timeframe
    # (e.g. --timeframe 4h -> 1200-candle EMA).
    btc_pair = "BTC/USDT"
    btc_ema_days = 200

    startup_candle_count: int = 210

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        # Same file runs long-only on spot and long + short on futures.
        if config.get("trading_mode", "spot") == "futures":
            self.can_short = True
            self.btc_pair = f"{self.btc_pair}:{self.btc_pair.split('/')[1]}"  # BTC/USDT:USDT
            self.stoploss = max(-0.99, self.stoploss * self.futures_leverage)
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
    # Fear & Greed sentiment filter (0 = extreme fear, 100 = extreme greed)
    use_fng_filter = BooleanParameter(default=False, space="buy")
    fng_long_max = IntParameter(50, 100, default=80, space="buy")
    fng_short_min = IntParameter(0, 50, default=20, space="buy")
    fng_long_min = IntParameter(0, 80, default=0, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")

    sl_buffer = DecimalParameter(0.0, 0.03, default=0.01, decimals=3, space="sell")
    rr = DecimalParameter(1.0, 5.0, default=2.5, decimals=1, space="sell")

    @property
    def protections(self):
        return [
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 20,
                "trade_limit": 2,
                "stop_duration_candles": 10,
                "only_per_pair": False,
            }
        ]

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
        out["bear_ob_top"] = np.full(n, np.nan)
        out["bear_ob_bot"] = np.full(n, np.nan)
        for name in ("trend", "bos_up", "bos_down", "choch_up", "choch_down", "ob_touch",
                     "fvg_touch", "sweep", "ob_touch_short"):
            out[name] = np.zeros(n)
        out["entry_sl"] = np.full(n, np.nan)
        out["entry_sl_short"] = np.full(n, np.nan)
        # index of the candle whose break created the order block being retested (diagnostic)
        out["ob_src"] = np.full(n, np.nan)
        out["ob_src_short"] = np.full(n, np.nan)

        trend = 0
        sh = sl = np.nan
        sh_broken = sl_broken = sl_swept = False
        ob = None  # [top, bottom, created_idx, used]
        fvg = None
        bear_ob = None  # same layout, bearish order block

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
                bear_ob = None  # bearish zone no longer valid
                # order block = last bearish candle before the break
                for j in range(i - 1, max(i - 1 - self.ob_lookback, -1), -1):
                    if c[j] < o[j]:
                        ob = [h[j], lo[j], i, False]
                        break
            if not np.isnan(sl) and not sl_broken and c[i] < sl:
                sl_broken = True
                if trend == -1:
                    out["bos_down"][i] = 1
                else:
                    out["choch_down"][i] = 1
                trend = -1
                ob = fvg = None  # bullish zones no longer valid
                # bearish order block = last bullish candle before the break
                for j in range(i - 1, max(i - 1 - self.ob_lookback, -1), -1):
                    if c[j] > o[j]:
                        bear_ob = [h[j], lo[j], i, False]
                        break

            # 3. zone invalidation (closed through the far side)
            if ob is not None and c[i] < ob[1]:
                ob = None
            if fvg is not None and c[i] < fvg[1]:
                fvg = None
            if bear_ob is not None and c[i] > bear_ob[0]:
                bear_ob = None

            # 4. entry triggers (only in bullish structure)
            if trend == 1:
                if ob is not None and not ob[3] and i > ob[2] and lo[i] <= ob[0] and c[i] > ob[1]:
                    out["ob_touch"][i] = 1
                    ob[3] = True
                    out["entry_sl"][i] = ob[1]
                    out["ob_src"][i] = ob[2]
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
            elif trend == -1:
                if (bear_ob is not None and not bear_ob[3] and i > bear_ob[2]
                        and h[i] >= bear_ob[1] and c[i] < bear_ob[0]):
                    out["ob_touch_short"][i] = 1
                    bear_ob[3] = True
                    out["entry_sl_short"][i] = bear_ob[0]
                    out["ob_src_short"][i] = bear_ob[2]

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
            if bear_ob is not None:
                out["bear_ob_top"][i], out["bear_ob_bot"][i] = bear_ob[0], bear_ob[1]

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
        dataframe["fng"] = self._fear_greed(dataframe["date"])
        return dataframe

    def _fear_greed(self, dates):
        """Fear & Greed value per candle (NaN where unknown). The history is loaded once per
        candle in dry-run / live (refreshed from the API when stale) and once per backtest."""
        live = self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE)
        latest = dates.iat[-1] if len(dates) else None
        cached = getattr(self, "_fng_cache", None)
        if cached is None or (live and cached[0] != latest):
            try:
                self._fng_cache = (latest, fear_greed.load(refresh=live))
            except Exception as e:
                logger.warning("Fear & Greed data unavailable, filter disabled: %s", e)
                return np.nan
        return fear_greed.fng_for_candles(dates, self._fng_cache[1])

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
        if self.use_fng_filter.value:
            base &= ~(dataframe["fng"] > self.fng_long_max.value)  # NaN never blocks
            base &= ~(dataframe["fng"] < self.fng_long_min.value)

        # later assignments win, so order = lowest -> highest priority tag
        for flag, col, tag in (
            (self.use_fvg.value, "fvg_touch", "fvg_retest"),
            (self.use_ob.value, "ob_touch", "ob_retest"),
            (self.use_sweep.value, "sweep", "sweep"),
        ):
            if flag:
                cond = base & (dataframe[col] == 1)
                dataframe.loc[cond, ["enter_long", "enter_tag"]] = (1, tag)

        if self.can_short and self.use_ob.value:
            short = (
                (dataframe["trend"] == -1)
                & (dataframe["volume"] > 0)
                & (dataframe["ob_touch_short"] == 1)
                & ((dataframe["entry_sl_short"] - dataframe["close"]) / dataframe["close"]
                   <= self.max_risk.value)
            )
            if self.use_ema_filter.value:
                short &= dataframe["close"] < dataframe["ema"]
            if self.use_btc_filter.value:
                short &= dataframe["bull_btc"] == 0
            if self.use_fng_filter.value:
                short &= ~(dataframe["fng"] < self.fng_short_min.value)
                short &= ~(dataframe["fng"] > self.fng_short_max.value)
            dataframe.loc[short, ["enter_short", "enter_tag"]] = (1, "ob_retest_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["choch_down"] == 1, ["exit_long", "exit_tag"]] = (1, "choch_down")
        dataframe.loc[dataframe["choch_up"] == 1, ["exit_short", "exit_tag"]] = (1, "choch_up")
        return dataframe

    # ------------------------------------------------------------------ risk
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        return min(self.futures_leverage, max_leverage)

    def _signal_candle(self, pair: str, trade: Trade) -> DataFrame:
        """Candles up to the signal candle (the last one closed before the entry candle).
        Live entries happen a few seconds after the candle opens, so compare with the start
        of the entry candle, not the exact open time - otherwise the entry candle itself
        becomes the 'signal' once it has closed."""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        entry_candle = timeframe_to_prev_date(self.timeframe, trade.open_date_utc)
        return df.loc[df["date"] < entry_candle]

    def _entry_stop(self, pair: str, trade: Trade) -> float | None:
        """Structural stop price, read from the signal candle that opened the trade."""
        signal = self._signal_candle(pair, trade)
        col = "entry_sl_short" if trade.is_short else "entry_sl"
        if signal.empty or np.isnan(signal[col].iat[-1]):
            return None
        side = 1 if trade.is_short else -1  # buffer goes beyond the zone, away from price
        return signal[col].iat[-1] * (1 + side * self.sl_buffer.value)

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
        if stop is None:
            return None
        risk = (stop - trade.open_rate) if trade.is_short else (trade.open_rate - stop)
        if risk <= 0:
            return None
        # Check the target once per candle, on the last closed candle's close (= the open of
        # the current candle), exactly as backtesting does. Live, current_rate moves all day and
        # would exit intraday at the target, which backtested worse (SMC00_OB_Plus).
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df.empty:
            return None
        price = df["close"].iat[-1]
        if trade.is_short:
            hit = price <= trade.open_rate - self.rr.value * risk
        else:
            hit = price >= trade.open_rate + self.rr.value * risk
        return "take_profit_rr" if hit else None
