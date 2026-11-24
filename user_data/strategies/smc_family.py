"""
SMC / ICT strategy family - 13 published "smart money" trading models, coded mechanically.

Each model below is a common public description of an ICT / SMC setup (TradingView guides,
LuxAlgo concept library, TradeZella, Raschke & Connors "Street Smarts", Wyckoff method).
They are written as fixed rules so they can be backtested; none was optimised on results.

    SMC01_ICT2022       ICT 2022 mentorship model: sell-side sweep -> market structure shift
                        with a displacement FVG -> entry on the FVG retest, stop under the sweep
    SMC02_TurtleSoup    Raschke / ICT turtle soup: new 20-day low (prior low >= 4 days old)
                        that closes back above the prior low = failed breakdown
    SMC03_OTE           Optimal trade entry: after a break of structure, buy the 62-79 %
                        retracement of the impulse leg, stop under the leg, target -27 % ext.
    SMC04_Breaker       Breaker block: a swing high's last up-close candle fails as resistance
                        (lower low sweeps the prior low, then price closes above the high);
                        buy the retest of that candle
    SMC05_Unicorn       Unicorn: breaker block that overlaps a FVG from the break; buy the overlap
    SMC06_IFVG          Inverse FVG: a bearish FVG that price closes through becomes support;
                        buy the retest, stop under the low before the inversion
    SMC07_CISD          Change in state of delivery: after a sweep of the swing low, a body
                        close above the open of the down-candle run into the low
    SMC08_CRT           Candle range theory: a candle sweeps the prior candle's low and closes
                        back inside its range; target the prior candle's high
    SMC09_WeeklyPO3     Power of 3 / AMD on the weekly candle: Mon-Wed trades below the weekly
                        open (Judas swing) then closes back above it; target last week's high,
                        exit at the end of the week
    SMC10_SMT           SMT divergence: the pair and BTC (ETH for BTC) disagree at a swing low
                        (one makes a lower low, the other a higher low)
    SMC11_Wyckoff       Wyckoff spring: false break of a trading range's support that closes
                        back inside; target the top of the range (no trend filter: it is a
                        reversal from accumulation)
    SMC12_JudasPO3      (1h) Daily power of 3: price drops below the New York midnight open
                        (Judas swing) then a London / New York candle closes back above it;
                        target the previous day's high, exit at the 16:00 New York close
    SMC13_SilverBullet  (5m) ICT silver bullet: first FVG inside the 10:00-11:00 New York window
                        in the direction of the daily bias, entered on its retest

All models are mirrored for shorts (futures). Common rules, the same for every model:
    * daily bias: longs only above the pair's 50-day EMA, shorts only below (except Wyckoff)
    * stop just beyond the model's invalidation level, skip if the risk is above 12 %
    * target: the model's own liquidity target when it has one, else `rr` x risk; the target
      is a limit-style exit (custom_roi), so it fills at the target price
    * `*_Plus` variants add the two upgrades that worked for SMCDaily: BTC 200-day EMA regime
      filter and the StoplossGuard circuit breaker

Benchmarks: SMC00_OB_Core (SMCDaily's order-block rules, no BTC filter / guard) and
SMC00_OB_Plus (SMCDaily as committed), both with the same limit-style target.
"""

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
    IStrategy,
    merge_informative_pair,
    stoploss_from_absolute,
)


sys.path.insert(0, str(Path(__file__).parent))
from smc_daily import SMCDaily  # noqa: E402


GUARD = [{"method": "StoplossGuard", "lookback_period_candles": 20, "trade_limit": 2,
          "stop_duration_candles": 10, "only_per_pair": False}]


def pivots(h: np.ndarray, lo: np.ndarray, k: int):
    """Swing high / low centred at i-k, confirmed at bar i (no lookahead)."""
    sh, sl = pd.Series(h), pd.Series(lo)
    win = 2 * k + 1
    is_ph = (sh.shift(k) == sh.rolling(win).max()).to_numpy()
    is_pl = (sl.shift(k) == sl.rolling(win).min()).to_numpy()
    return is_ph, is_pl


def empty(n: int):
    return np.zeros(n, dtype=bool), np.full(n, np.nan), np.full(n, np.nan)


class SMCFamily(IStrategy):
    """Shared plumbing. Subclasses implement setup() for the LONG side on (o, h, l, c);
    the short side is the same function run on mirrored prices (-o, -l, -h, -c)."""

    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    minimal_roi = {"0": 100}
    stoploss = -0.30
    use_custom_stoploss = True
    use_custom_roi = True
    use_exit_signal = True
    process_only_new_candles = True
    startup_candle_count = 210

    tag = "smc"
    swing_length = 3
    ema_period = 50
    btc_ema_period = 200  # daily candles
    use_bias = True
    use_btc = False
    guard = False
    max_risk = 0.12
    min_rr = 1.0  # structural targets closer than 1R are skipped
    sl_buffer = 0.005
    rr = 2.5
    max_hold = 0  # candles, 0 = no time stop
    uses_ref = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.futures = config.get("trading_mode", "spot") == "futures"
        self.can_short = self.futures
        quote = ":USDT" if self.futures else ""
        self.btc_pair = f"BTC/USDT{quote}"
        self.eth_pair = f"ETH/USDT{quote}"
        self._levels_cache: dict = {}

    @property
    def protections(self):
        return GUARD if self.guard else []

    def informative_pairs(self):
        pairs = [(self.btc_pair, "1d")]
        if self.uses_ref:
            pairs.append((self.eth_pair, "1d"))
        if self.timeframe != "1d":
            pairs += [(p, "1d") for p in self.dp.current_whitelist()]
        return pairs

    # ------------------------------------------------------------------ indicators
    def _daily_regime(self, df: DataFrame, pair: str) -> DataFrame:
        daily = self.dp.get_pair_dataframe(pair, "1d")[["date", "close"]].copy()
        daily["bias"] = np.where(daily["close"] > ta.EMA(daily, timeperiod=self.ema_period), 1, -1)
        btc = self.dp.get_pair_dataframe(self.btc_pair, "1d")[["date", "close"]].copy()
        btc["btc_bull"] = (btc["close"] > ta.EMA(btc, timeperiod=self.btc_ema_period)).astype(int)
        for inf, col in ((daily, "bias"), (btc, "btc_bull")):
            df = merge_informative_pair(df, inf[["date", col]], self.timeframe, "1d", ffill=True,
                                        append_timeframe=False, suffix="x")
            df = df.rename(columns={f"{col}_x": col}).drop(columns=["date_x"])
        if self.uses_ref:
            ref = self.eth_pair if pair == self.btc_pair else self.btc_pair
            r = self.dp.get_pair_dataframe(ref, "1d")[["date", "high", "low"]].copy()
            df = merge_informative_pair(df, r, self.timeframe, "1d", ffill=True,
                                        append_timeframe=False, suffix="ref")
        return df

    def _ctx(self, df: DataFrame) -> dict:
        d = df["date"]
        iso = d.dt.isocalendar()
        ny = d.dt.tz_convert("America/New_York")
        ctx = {
            "v": df["volume"].to_numpy(),
            "weekday": d.dt.weekday.to_numpy(),
            "week": (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy(),
            "ny_hour": ny.dt.hour.to_numpy(),
            "ny_day": ny.dt.tz_localize(None).dt.normalize().to_numpy().astype("int64"),
        }
        if self.uses_ref:
            ctx["ref_h"] = df["high_ref"].to_numpy(dtype=float)
            ctx["ref_l"] = df["low_ref"].to_numpy(dtype=float)
        return ctx

    def setup(self, o, h, lo, c, x):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = self._daily_regime(dataframe, metadata["pair"])
        x = self._ctx(df)
        o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        sig, sl, tp = self.setup(o, h, lo, c, x)
        df["long_sig"], df["long_sl"], df["long_tp"] = sig.astype(int), sl, tp
        xm = dict(x)
        if self.uses_ref:
            xm["ref_h"], xm["ref_l"] = -x["ref_l"], -x["ref_h"]
        sig, sl, tp = self.setup(-o, -lo, -h, -c, xm)
        df["short_sig"], df["short_sl"], df["short_tp"] = sig.astype(int), -sl, -tp
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        for side, sign in (("long", 1), ("short", -1)):
            if side == "short" and not self.can_short:
                continue
            sl, tp, c = df[f"{side}_sl"], df[f"{side}_tp"], df["close"]
            risk = sign * (c - sl) / c
            reward = sign * (tp - c) / c
            cond = (
                (df[f"{side}_sig"] == 1)
                & (df["volume"] > 0)
                & (risk > 0)
                & (risk <= self.max_risk)
                & (tp.isna() | (reward >= self.min_rr * risk))
            )
            if self.use_bias:
                cond &= df["bias"] == sign
            if self.use_btc:
                cond &= df["btc_bull"] == (1 if sign == 1 else 0)
            tag = self.tag if side == "long" else f"{self.tag}_short"
            df.loc[cond, [f"enter_{side}", "enter_tag"]] = (1, tag)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    # ------------------------------------------------------------------ risk
    def _levels(self, pair: str, trade: Trade):
        """(stop, target) prices, read once from the signal candle that opened the trade."""
        key = (pair, trade.open_date_utc, trade.is_short)
        if key in self._levels_cache:
            return self._levels_cache[key]
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        signal = df.loc[df["date"] < trade.open_date_utc]
        if signal.empty:
            return None
        side = "short" if trade.is_short else "long"
        stop = signal[f"{side}_sl"].iat[-1]
        target = signal[f"{side}_tp"].iat[-1]
        if np.isnan(stop):
            return None
        sign = -1 if trade.is_short else 1
        stop *= 1 - sign * self.sl_buffer
        if np.isnan(target):
            target = trade.open_rate + sign * self.rr * abs(trade.open_rate - stop)
        self._levels_cache[key] = (stop, target)
        return stop, target

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> float | None:
        lv = self._levels(pair, trade)
        if lv is None:
            return None
        return stoploss_from_absolute(lv[0], current_rate, is_short=trade.is_short,
                                      leverage=trade.leverage)

    def custom_roi(self, pair: str, trade: Trade, current_time: datetime, trade_duration: int,
                   entry_tag: str | None, side: str, **kwargs) -> float | None:
        lv = self._levels(pair, trade)
        if lv is None:
            return None
        roi = trade.calc_profit_ratio(lv[1])
        return roi if roi > 0 else None

    def extra_exit(self, trade: Trade, current_time: datetime) -> str | None:
        return None

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> str | None:
        if self.max_hold:
            held = (current_time - trade.open_date_utc).total_seconds() / 60
            if held >= self.max_hold * self.timeframe_minutes():
                return "time_exit"
        return self.extra_exit(trade, current_time)

    def timeframe_minutes(self) -> int:
        from freqtrade.exchange import timeframe_to_minutes

        return timeframe_to_minutes(self.timeframe)


# ============================================================================ models
class SMC01_ICT2022(SMCFamily):
    tag = "ict2022"
    rr = 3.0  # sources quote a 1:3 minimum
    window = 10

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        k = self.swing_length
        is_ph, is_pl = pivots(h, lo, k)
        sh = sl = np.nan
        sl_used = True
        phase = 0
        sweep_low = mss_level = fvg_top = fvg_bot = 0.0
        sweep_i = mss_i = 0
        for i in range(n):
            if is_ph[i]:
                sh = h[i - k]
            if is_pl[i]:
                sl, sl_used = lo[i - k], False
            if phase == 2:
                if c[i] < fvg_bot or lo[i] < sweep_low or i - mss_i > self.window:
                    phase = 0
                elif lo[i] <= fvg_top:
                    sig[i], sl_a[i] = True, sweep_low
                    phase = 0
                    continue
            if phase == 1:
                sweep_low = min(sweep_low, lo[i])
                if c[i] > mss_level:
                    phase = 0
                    for j in range(i, sweep_i, -1):  # displacement must leave a bullish FVG
                        if j >= 2 and lo[j] > h[j - 2]:
                            fvg_top, fvg_bot, mss_i, phase = lo[j], h[j - 2], i, 2
                            break
                elif i - sweep_i > self.window:
                    phase = 0
            if phase == 0 and not sl_used and lo[i] < sl and not np.isnan(sh) and sh > sl:
                sl_used = True
                phase, sweep_low, sweep_i, mss_level = 1, lo[i], i, sh
        return sig, sl_a, tp_a


class SMC02_TurtleSoup(SMCFamily):
    tag = "turtle_soup"
    rr = 2.0
    max_hold = 10  # Raschke takes profits within days
    lookback = 20
    min_age = 4

    def setup(self, o, h, lo, c, x):
        s = pd.Series(lo).shift(1)
        prior = s.rolling(self.lookback).min()
        # bars since the prior N-day low was made (most recent occurrence)
        age = s.rolling(self.lookback).apply(lambda a: 1 + np.argmin(a[::-1]), raw=True)
        cond = (lo < prior) & (c > prior) & (age >= self.min_age)
        sig = cond.to_numpy()
        sl = np.where(sig, lo, np.nan)
        return sig, sl, np.full(len(c), np.nan)


class SMC03_OTE(SMCFamily):
    tag = "ote"
    fib_top, fib_bot, extension = 0.62, 0.79, 0.27

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        k = self.swing_length
        is_ph, is_pl = pivots(h, lo, k)
        sh = pl = np.nan
        sh_broken = True
        armed = False
        leg_lo = leg_hi = 0.0
        bos_i = 0
        for i in range(n):
            if is_ph[i]:
                sh, sh_broken = h[i - k], False
            if is_pl[i]:
                pl = lo[i - k]
            if armed and i > bos_i:
                rng = leg_hi - leg_lo
                top = leg_hi - self.fib_top * rng
                bot = leg_hi - self.fib_bot * rng
                if lo[i] <= leg_lo:
                    armed = False
                elif lo[i] <= top and c[i] >= bot:
                    sig[i], sl_a[i], tp_a[i] = True, leg_lo, leg_hi + self.extension * rng
                    armed = False
                else:
                    leg_hi = max(leg_hi, h[i])
            if not np.isnan(sh) and not sh_broken and c[i] > sh:
                sh_broken = True
                if not np.isnan(pl) and pl < sh:
                    armed, leg_lo, leg_hi, bos_i = True, pl, h[i], i
        return sig, sl_a, tp_a


class SMC04_Breaker(SMCFamily):
    tag = "breaker"
    unicorn = False
    expiry = 20

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        k = self.swing_length
        is_ph, is_pl = pivots(h, lo, k)
        pl = np.nan
        cand = None  # [B high, zone top, zone bot, A low, swept, C low, C idx]
        active = None  # (top, bot, activated idx, stop)
        for i in range(n):
            if is_pl[i]:
                pl = lo[i - k]
            if is_ph[i] and not np.isnan(pl):
                b = i - k
                for j in range(b, max(b - 5, -1), -1):  # last up-close candle of the swing high
                    if c[j] > o[j]:
                        cand = [h[b], h[j], lo[j], pl, False, np.inf, -1]
                        break
            if active is not None:
                top, bot, act_i, stop = active
                if c[i] < bot or i - act_i > self.expiry:
                    active = None
                elif i > act_i and lo[i] <= top:
                    sig[i], sl_a[i] = True, stop
                    active = None
            if cand is not None:
                if not cand[4] and lo[i] < cand[3]:
                    cand[4] = True  # lower low swept the prior swing low
                if cand[4] and lo[i] < cand[5]:
                    cand[5], cand[6] = lo[i], i
                if cand[4] and c[i] > cand[0]:  # ...and price closed back above the swing high
                    top, bot = cand[1], cand[2]
                    if self.unicorn:
                        fvg = None
                        for j in range(i, cand[6] + 1, -1):
                            if lo[j] > h[j - 2]:
                                fvg = (lo[j], h[j - 2])
                                break
                        if fvg is None or min(top, fvg[0]) <= max(bot, fvg[1]):
                            cand = None
                            continue
                        top, bot = min(top, fvg[0]), max(bot, fvg[1])
                    active = (top, bot, i, cand[2])
                    cand = None
        return sig, sl_a, tp_a


class SMC05_Unicorn(SMC04_Breaker):
    tag = "unicorn"
    unicorn = True


class SMC06_IFVG(SMCFamily):
    tag = "ifvg"
    fvg_life = 30
    expiry = 10

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        gaps: list[list] = []  # bearish FVGs: [top, bot, created, low since]
        pending = None  # (top, bot, inverted idx, stop)
        for i in range(n):
            if pending is not None:
                top, bot, inv_i, stop = pending
                if c[i] < bot or i - inv_i > self.expiry:
                    pending = None
                elif i > inv_i and lo[i] <= top:
                    sig[i], sl_a[i] = True, stop
                    pending = None
            keep = []
            for g in gaps:
                g[3] = min(g[3], lo[i])
                if c[i] > g[0]:  # body closed through the bearish gap -> inverted
                    pending = (g[0], g[1], i, g[3])
                elif i - g[2] <= self.fvg_life:
                    keep.append(g)
            gaps = keep
            if i >= 2 and h[i] < lo[i - 2]:
                gaps.append([lo[i - 2], h[i], i, lo[i]])
                gaps = gaps[-3:]
        return sig, sl_a, tp_a


class SMC07_CISD(SMCFamily):
    tag = "cisd"
    expiry = 5

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        k = self.swing_length
        _, is_pl = pivots(h, lo, k)
        pl = np.nan
        pl_used = True
        run_open = np.nan  # open of the first candle of the latest down-close run
        in_run = False
        armed = False
        sweep_low = level = 0.0
        arm_i = 0
        for i in range(n):
            if c[i] < o[i]:
                if not in_run:
                    run_open, in_run = o[i], True
            else:
                in_run = False
            if is_pl[i]:
                pl, pl_used = lo[i - k], False
            if not pl_used and lo[i] < pl:  # sell-side liquidity taken
                pl_used, armed, sweep_low, arm_i, level = True, True, lo[i], i, run_open
            elif armed and lo[i] < sweep_low:
                sweep_low = lo[i]
                if in_run:
                    level = run_open
            if armed:
                if c[i] > level:
                    sig[i], sl_a[i] = True, sweep_low
                    armed = False
                elif i - arm_i > self.expiry:
                    armed = False
        return sig, sl_a, tp_a


class SMC08_CRT(SMCFamily):
    tag = "crt"
    max_hold = 5

    def setup(self, o, h, lo, c, x):
        ph, pl = np.roll(h, 1), np.roll(lo, 1)
        sig = (lo < pl) & (c > pl) & (c < ph)
        sig[0] = False
        sl = np.where(sig, lo, np.nan)
        tp = np.where(sig, ph, np.nan)
        return sig, sl, tp


class SMC09_WeeklyPO3(SMCFamily):
    tag = "weekly_po3"
    rr = 2.0

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        wk, wd = x["week"], x["weekday"]
        w_open = w_low = w_high = prev_high = np.nan
        done = False
        for i in range(n):
            if i == 0 or wk[i] != wk[i - 1]:
                prev_high, w_open, w_low, w_high, done = w_high, o[i], lo[i], h[i], False
            else:
                w_low, w_high = min(w_low, lo[i]), max(w_high, h[i])
            if not done and wd[i] <= 2 and w_low < w_open < c[i]:
                sig[i], sl_a[i] = True, w_low
                tp_a[i] = prev_high if prev_high > c[i] else np.nan
                done = True
        return sig, sl_a, tp_a

    def extra_exit(self, trade: Trade, current_time: datetime) -> str | None:
        if current_time.isocalendar()[:2] != trade.open_date_utc.isocalendar()[:2]:
            return "week_close"
        return None


class SMC10_SMT(SMCFamily):
    tag = "smt"
    uses_ref = True
    max_gap = 30

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        k = self.swing_length
        _, is_pl = pivots(h, lo, k)
        ref_low = pd.Series(x["ref_l"]).rolling(2 * k + 1).min().to_numpy()
        prev = None
        for i in range(n):
            if not is_pl[i]:
                continue
            p = i - k
            cur = (lo[p], ref_low[i], p)
            if prev is not None and p - prev[2] <= self.max_gap and not np.isnan(cur[1] + prev[1]):
                pair_ll, ref_ll = cur[0] < prev[0], cur[1] < prev[1]
                if pair_ll != ref_ll:  # one made a lower low, the other did not
                    sig[i], sl_a[i] = True, lo[p]
            prev = cur
        return sig, sl_a, tp_a


class SMC11_Wyckoff(SMCFamily):
    tag = "wyckoff_spring"
    use_bias = False
    range_len = 30
    max_width = 0.5

    def setup(self, o, h, lo, c, x):
        s_l, s_h = pd.Series(lo), pd.Series(h)
        support = s_l.shift(3).rolling(self.range_len).min()
        resist = s_h.shift(3).rolling(self.range_len).max()
        low3 = s_l.rolling(3).min()
        width = (resist - support) / np.abs(c)
        spring = (low3 < support) & (c > support) & (width <= self.max_width)
        sig = (spring & ~spring.shift(1, fill_value=False)).to_numpy()
        sl = np.where(sig, low3, np.nan)
        tp = np.where(sig, resist, np.nan)
        return sig, sl, tp


class SMC12_JudasPO3(SMCFamily):
    """Intraday: run with --timeframe 1h."""

    timeframe = "1h"
    tag = "judas_po3"
    rr = 2.0

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        day, hour = x["ny_day"], x["ny_hour"]
        d_open = d_low = d_high = prev_high = np.nan
        done = False
        for i in range(n):
            if i == 0 or day[i] != day[i - 1]:
                prev_high, d_open, d_low, d_high, done = d_high, o[i], lo[i], h[i], False
            else:
                d_low, d_high = min(d_low, lo[i]), max(d_high, h[i])
            if not done and 2 <= hour[i] <= 11 and d_low < d_open < c[i]:
                sig[i], sl_a[i] = True, d_low
                tp_a[i] = prev_high if prev_high > c[i] else np.nan
                done = True
        return sig, sl_a, tp_a

    def extra_exit(self, trade: Trade, current_time: datetime) -> str | None:
        from zoneinfo import ZoneInfo

        ny = ZoneInfo("America/New_York")
        now, opened = current_time.astimezone(ny), trade.open_date_utc.astimezone(ny)
        if now.date() != opened.date() or now.hour >= 16:
            return "session_close"
        return None


class SMC13_SilverBullet(SMC12_JudasPO3):
    """Intraday: run with --timeframe 5m."""

    timeframe = "5m"
    tag = "silver_bullet"
    rr = 2.0

    def setup(self, o, h, lo, c, x):
        n = len(c)
        sig, sl_a, tp_a = empty(n)
        day, hour = x["ny_day"], x["ny_hour"]
        win_low = np.nan
        fvg = None  # (top, bot, idx, stop)
        done = False
        for i in range(n):
            if i == 0 or day[i] != day[i - 1]:
                done, fvg, win_low = False, None, np.nan
            if hour[i] != 10 or done:
                continue
            win_low = lo[i] if np.isnan(win_low) else min(win_low, lo[i])
            if fvg is not None:
                if c[i] < fvg[1]:
                    fvg = None
                elif i > fvg[2] and lo[i] <= fvg[0]:
                    sig[i], sl_a[i] = True, fvg[3]
                    done = True
                    continue
            if fvg is None and i >= 2 and hour[i - 2] == 10 and lo[i] > h[i - 2]:
                fvg = (lo[i], h[i - 2], i, win_low)
        return sig, sl_a, tp_a


# ============================================================================ + upgrades
class SMC01_ICT2022_Plus(SMC01_ICT2022):
    use_btc, guard = True, True


class SMC02_TurtleSoup_Plus(SMC02_TurtleSoup):
    use_btc, guard = True, True


class SMC03_OTE_Plus(SMC03_OTE):
    use_btc, guard = True, True


class SMC04_Breaker_Plus(SMC04_Breaker):
    use_btc, guard = True, True


class SMC05_Unicorn_Plus(SMC05_Unicorn):
    use_btc, guard = True, True


class SMC06_IFVG_Plus(SMC06_IFVG):
    use_btc, guard = True, True


class SMC07_CISD_Plus(SMC07_CISD):
    use_btc, guard = True, True


class SMC08_CRT_Plus(SMC08_CRT):
    use_btc, guard = True, True


class SMC09_WeeklyPO3_Plus(SMC09_WeeklyPO3):
    use_btc, guard = True, True


class SMC10_SMT_Plus(SMC10_SMT):
    use_btc, guard = True, True


class SMC11_Wyckoff_Plus(SMC11_Wyckoff):
    use_btc, guard = True, True


class SMC12_JudasPO3_Plus(SMC12_JudasPO3):
    use_btc, guard = True, True


class SMC13_SilverBullet_Plus(SMC13_SilverBullet):
    use_btc, guard = True, True


# ============================================================================ benchmarks
class SMC00_OB_Plus(SMCDaily):
    """SMCDaily as committed, but the take-profit fills at the target (same as the family)."""

    use_custom_roi = True

    def custom_roi(self, pair: str, trade: Trade, current_time: datetime, trade_duration: int,
                   entry_tag: str | None, side: str, **kwargs) -> float | None:
        stop = self._entry_stop(pair, trade)
        if stop is None:
            return None
        sign = -1 if trade.is_short else 1
        target = trade.open_rate + sign * self.rr.value * abs(trade.open_rate - stop)
        roi = trade.calc_profit_ratio(target)
        return roi if roi > 0 else None

    def custom_exit(self, *args, **kwargs) -> str | None:
        return None


class SMC00_OB_Core(SMC00_OB_Plus):
    """SMCDaily's order-block rules without the BTC filter and the circuit breaker."""

    use_btc_filter = BooleanParameter(default=False, space="buy")

    @property
    def protections(self):
        return []
