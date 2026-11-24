"""
SMCLab - drawdown-reduction experiments on top of SMCDaily.

SMCLab adds optional features, each switched on by a class attribute. The SMC_Vxx classes
at the bottom each turn on one idea (round 1) or a combination (round 2), so they can be
compared side by side with:

    freqtrade backtesting --strategy-list SMC_V00_Base SMC_V01_Risk ... --enable-protections

(--enable-protections is needed for the variants that use freqtrade protections.)

Ideas, mostly from common trend-following / SMC risk-management practice:
    sizing      risk_per_trade   size each trade so hitting the stop costs X% of equity
                stake_frac       fixed fraction of equity per trade (smaller positions)
    selection   max_risk_cap     skip trades whose stop is further than X%
                adx_min          pair must be trending (ADX)
                atr_pct_max      skip very volatile conditions (ATR % of price)
                displacement     the move that created the OB must be >= X ATR
                premium_discount longs only in the lower half of the recent range, shorts upper
                btc_slope        BTC regime also needs the 200-day EMA sloping the right way
    exposure    max_per_side     cap simultaneous longs / shorts
                protections_cfg  freqtrade StoplossGuard / MaxDrawdown circuit breakers
    exits       breakeven_r      move stop to entry after +X R
                trail_r/atr      chandelier trailing stop after +X R
                time_stop_days   close losing trades after N days
                rr (param)       take-profit multiple
"""

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, stoploss_from_absolute


sys.path.insert(0, str(Path(__file__).parent))
from smc_daily import SMCDaily  # noqa: E402


class SMCLab(SMCDaily):
    risk_per_trade: float | None = None
    stake_frac: float | None = None
    max_risk_cap: float | None = None
    adx_min: float | None = None
    atr_pct_max: float | None = None
    displacement: float | None = None
    premium_discount: bool = False
    btc_slope: bool = False
    btc_slope_days: int = 20
    max_per_side: int | None = None
    protections_cfg: list = []
    breakeven_r: float | None = None
    trail_r: float | None = None
    trail_atr: float = 3.0
    time_stop_days: int | None = None

    @property
    def protections(self):
        return self.protections_cfg

    # ------------------------------------------------------------------ indicators
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["hh20"] = dataframe["high"].rolling(20).max()
        dataframe["ll20"] = dataframe["low"].rolling(20).min()

        # displacement of the break that created the retested order block, in ATRs
        close, atr = dataframe["close"].to_numpy(), dataframe["atr"].to_numpy()
        for side, src_col, edge_col, sign in (
            ("long", "ob_src", "ob_top", 1),
            ("short", "ob_src_short", "bear_ob_bot", -1),
        ):
            src = dataframe[src_col].to_numpy()
            disp = np.full(len(dataframe), np.nan)
            ok = ~np.isnan(src)
            idx = src[ok].astype(int)
            disp[ok] = sign * (close[idx] - dataframe[edge_col].to_numpy()[ok]) / atr[idx]
            dataframe[f"disp_{side}"] = disp

        if self.btc_slope:
            btc = self.dp.get_pair_dataframe(self.btc_pair, self.timeframe)[["date", "close"]]
            ema = ta.EMA(btc, timeperiod=self.btc_ema_period)
            bars = max(1, round(self.btc_slope_days * self.btc_ema_period / self.btc_ema_days))
            rising = ema > ema.shift(bars)
            regime = btc[["date"]].copy()
            regime["long_ok_btc"] = ((btc["close"] > ema) & rising).astype(int)
            regime["short_ok_btc"] = ((btc["close"] < ema) & ~rising).astype(int)
            dataframe = dataframe.merge(regime, on="date", how="left")
        return dataframe

    # ------------------------------------------------------------------ entry filters
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)
        for col in ("enter_long", "enter_short"):
            if col not in dataframe:
                dataframe[col] = 0
        ok_l = dataframe["close"].notna()
        ok_s = ok_l.copy()

        if self.max_risk_cap is not None:
            ok_l &= (dataframe["close"] - dataframe["entry_sl"]) / dataframe["close"] <= (
                self.max_risk_cap
            )
            ok_s &= (dataframe["entry_sl_short"] - dataframe["close"]) / dataframe["close"] <= (
                self.max_risk_cap
            )
        if self.adx_min is not None:
            ok_l &= dataframe["adx"] > self.adx_min
            ok_s &= dataframe["adx"] > self.adx_min
        if self.atr_pct_max is not None:
            calm = dataframe["atr"] / dataframe["close"] < self.atr_pct_max
            ok_l &= calm
            ok_s &= calm
        if self.displacement is not None:
            ok_l &= dataframe["disp_long"] >= self.displacement
            ok_s &= dataframe["disp_short"] >= self.displacement
        if self.premium_discount:
            ok_l &= dataframe["close"] <= (dataframe["swing_low"] + dataframe["hh20"]) / 2
            ok_s &= dataframe["close"] >= (dataframe["swing_high"] + dataframe["ll20"]) / 2
        if self.btc_slope:
            ok_l &= dataframe["long_ok_btc"] == 1
            ok_s &= dataframe["short_ok_btc"] == 1

        dataframe.loc[~ok_l.fillna(False), "enter_long"] = 0
        dataframe.loc[~ok_s.fillna(False), "enter_short"] = 0
        return dataframe

    # ------------------------------------------------------------------ sizing / exposure
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        equity = self.wallets.get_total_stake_amount()
        stake = proposed_stake
        if self.stake_frac is not None:
            stake = min(stake, equity * self.stake_frac)
        if self.risk_per_trade is not None:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if not df.empty:
                col = "entry_sl_short" if side == "short" else "entry_sl"
                sl = df[col].iat[-1]
                if not np.isnan(sl):
                    dist = abs(current_rate - sl) / current_rate + self.sl_buffer.value
                    stake = min(stake, equity * self.risk_per_trade / max(dist, 1e-4))
        return min(stake, max_stake)

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        if self.max_per_side is not None:
            is_short = side == "short"
            same = [t for t in Trade.get_trades_proxy(is_open=True) if t.is_short == is_short]
            if len(same) >= self.max_per_side:
                return False
        return True

    # ------------------------------------------------------------------ exits
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> float | None:
        stop = self._entry_stop(pair, trade)
        if stop is None:
            return None
        o = trade.open_rate
        risk = (stop - o) if trade.is_short else (o - stop)
        if risk > 0 and (self.breakeven_r or self.trail_r):
            fav = (o - trade.min_rate) if trade.is_short else (trade.max_rate - o)
            if self.breakeven_r and fav >= self.breakeven_r * risk:
                stop = min(stop, o) if trade.is_short else max(stop, o)
            if self.trail_r and fav >= self.trail_r * risk:
                df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                atr = df["atr"].iat[-1] if not df.empty else np.nan
                if not np.isnan(atr):
                    if trade.is_short:
                        stop = min(stop, trade.min_rate + self.trail_atr * atr)
                    else:
                        stop = max(stop, trade.max_rate - self.trail_atr * atr)
        return stoploss_from_absolute(stop, current_rate, is_short=trade.is_short,
                                      leverage=trade.leverage)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> str | None:
        res = super().custom_exit(pair, trade, current_time, current_rate, current_profit,
                                  **kwargs)
        if res:
            return res
        if (self.time_stop_days is not None and current_profit < 0
                and (current_time - trade.open_date_utc).days >= self.time_stop_days):
            return "time_stop"
        return None


STOPLOSS_GUARD = {"method": "StoplossGuard", "lookback_period_candles": 30, "trade_limit": 3,
                  "stop_duration_candles": 10, "only_per_pair": False}
MAX_DD_GUARD = {"method": "MaxDrawdown", "lookback_period_candles": 60, "trade_limit": 4,
                "stop_duration_candles": 20, "max_allowed_drawdown": 0.12}


# ---------------------------------------------------------------------- round 1: one idea each
class SMC_V00_Base(SMCLab):
    pass


class SMC_V01_Risk(SMCLab):
    risk_per_trade = 0.015


class SMC_V02_Stake15(SMCLab):
    stake_frac = 0.15


class SMC_V03_MaxRisk6(SMCLab):
    max_risk_cap = 0.06


class SMC_V04_SLGuard(SMCLab):
    protections_cfg = [STOPLOSS_GUARD]


class SMC_V05_DDGuard(SMCLab):
    protections_cfg = [MAX_DD_GUARD]


class SMC_V06_BTCSlope(SMCLab):
    btc_slope = True


class SMC_V07_ADX(SMCLab):
    adx_min = 20


class SMC_V08_ATRCap(SMCLab):
    atr_pct_max = 0.07


class SMC_V09_Displace(SMCLab):
    displacement = 1.5


class SMC_V10_PremDisc(SMCLab):
    premium_discount = True


class SMC_V11_BreakEven(SMCLab):
    breakeven_r = 1.0


class SMC_V12_Trail(SMCLab):
    trail_r = 1.5


class SMC_V13_TimeStop(SMCLab):
    time_stop_days = 20


class SMC_V14_MaxSide2(SMCLab):
    max_per_side = 2


class SMC_V15_RR2(SMCLab):
    rr = DecimalParameter(1.0, 5.0, default=2.0, decimals=1, space="sell")


# ---------------------------------------------------------------------- round 2: combinations
# Round 1 winners on return / drawdown across all 4 setups: StoplossGuard (V04) and the
# per-side cap (V14); ADX (V07) helped on the unseen pairs only. Exit tweaks (break-even,
# trailing) and the premium/discount and displacement filters made things worse.
def _slg(limit=3, stop=10, lookback=30):
    return {"method": "StoplossGuard", "lookback_period_candles": lookback,
            "trade_limit": limit, "stop_duration_candles": stop, "only_per_pair": False}


class SMC_R2_01_SLG_Side2(SMCLab):
    protections_cfg = [_slg()]
    max_per_side = 2


class SMC_R2_02_SLG_ADX20(SMCLab):
    protections_cfg = [_slg()]
    adx_min = 20


class SMC_R2_03_SLG_Side2_ADX20(SMCLab):
    protections_cfg = [_slg()]
    max_per_side = 2
    adx_min = 20


class SMC_R2_04_SLG_Side2_DDG(SMCLab):
    protections_cfg = [_slg(), MAX_DD_GUARD]
    max_per_side = 2


class SMC_R2_05_SLG2_10(SMCLab):
    protections_cfg = [_slg(limit=2, stop=10)]


class SMC_R2_06_SLG3_20(SMCLab):
    protections_cfg = [_slg(limit=3, stop=20)]


class SMC_R2_07_SLG2_20_Side2(SMCLab):
    protections_cfg = [_slg(limit=2, stop=20)]
    max_per_side = 2


class SMC_R2_08_SLG_Side3(SMCLab):
    protections_cfg = [_slg()]
    max_per_side = 3


class SMC_R2_09_SLG_Side2_ADX15(SMCLab):
    protections_cfg = [_slg()]
    max_per_side = 2
    adx_min = 15


class SMC_R2_10_SLG_Side2_Risk8(SMCLab):
    protections_cfg = [_slg()]
    max_per_side = 2
    max_risk_cap = 0.08


# ---------------------------------------------------------------------- round 3: guard tuning
# Combining guard + per-side cap did not stack (R2_01 < V04 and V14). The StoplossGuard family
# beat the base in every setting tried, so round 3 maps its sensitivity to pick a stable
# setting rather than a lucky one, and tries a per-side guard for futures.
class SMC_R3_SLG2_15(SMCLab):
    protections_cfg = [_slg(limit=2, stop=15)]


class SMC_R3_SLG2_20(SMCLab):
    protections_cfg = [_slg(limit=2, stop=20)]


class SMC_R3_SLG3_15(SMCLab):
    protections_cfg = [_slg(limit=3, stop=15)]


class SMC_R3_SLG2_10_LB20(SMCLab):
    protections_cfg = [_slg(limit=2, stop=10, lookback=20)]


class SMC_R3_SLG2_10_LB45(SMCLab):
    protections_cfg = [_slg(limit=2, stop=10, lookback=45)]


class SMC_R3_SLG2_10_PerSide(SMCLab):
    protections_cfg = [dict(_slg(limit=2, stop=10), only_per_side=True)]


class SMC_R3_SLG2_10_Stake15(SMCLab):
    protections_cfg = [_slg(limit=2, stop=10)]
    stake_frac = 0.15


class SMC_R3_SLG2_10_Side3(SMCLab):
    protections_cfg = [_slg(limit=2, stop=10)]
    max_per_side = 3


class SMC_R3_SLG3_10_LB45(SMCLab):
    protections_cfg = [_slg(limit=3, stop=10, lookback=45)]


# ---------------------------------------------------------------------- round 4: around the winner
# Round 3 winner: StoplossGuard 2 stop-outs / 20-day lookback / 10-day pause (R3_SLG2_10_LB20).
class SMC_R4_SLG2_10_LB20_PerSide(SMCLab):
    protections_cfg = [dict(_slg(limit=2, stop=10, lookback=20), only_per_side=True)]


class SMC_R4_SLG2_7_LB20(SMCLab):
    protections_cfg = [_slg(limit=2, stop=7, lookback=20)]


class SMC_R4_SLG2_10_LB15(SMCLab):
    protections_cfg = [_slg(limit=2, stop=10, lookback=15)]


class SMC_R4_SLG2_10_LB20_Side3(SMCLab):
    protections_cfg = [_slg(limit=2, stop=10, lookback=20)]
    max_per_side = 3
