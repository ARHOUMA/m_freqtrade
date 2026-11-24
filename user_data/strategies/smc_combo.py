"""
SMCCombo - SMCDaily's order-block entries and the Inverse FVG model in one bot.

The two models made money in different periods (Inverse FVG shorts carried 2022, when SMCDaily
was flat) and their monthly returns were only weakly correlated, so running both in one
account should smooth the equity curve.

Entries
    ob_retest / ob_retest_short : SMCDaily, unchanged (order-block retest in the trend)
    ifvg / ifvg_short           : SMC06_IFVG_Plus from smc_family.py - a bearish FVG that price
                                  closes through becomes support, buy the retest (mirrored
                                  for shorts)
    Both use the same filters: pair above / below its 50-day EMA, BTC above / below its
    200-day EMA, risk to the stop at most 12 %. When both fire on the same candle the
    order-block entry wins.

Exits - each entry keeps the exits it was tested with
    order block : stop 1 % beyond the zone, take profit at 2.5R checked at the daily open,
                  exit on a CHoCH against the trade
    inverse FVG : stop 0.5 % beyond the low before the inversion, 2.5R limit target, no CHoCH
                  exit (SMCCombo_SameExits gives these trades the order-block exits instead)

The two models share the trade slots (max_open_trades). Circuit breaker: SMCCombo shares
SMCDaily's StoplossGuard between both models (2 stop-outs in 20 days pause all entries for 10
days). The *_ModelGuard variants give each model its own breaker with the same settings, so a
losing streak in one model does not pause the other - as if they ran as two separate bots.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import timeframe_to_minutes


sys.path.insert(0, str(Path(__file__).parent))
from smc_daily import SMCDaily  # noqa: E402
from smc_family import SMC06_IFVG  # noqa: E402


class SMCCombo(SMCDaily):
    use_custom_roi = True

    # Inverse FVG settings, as tested in smc_family.py
    ifvg_tag = "ifvg"
    fvg_life = SMC06_IFVG.fvg_life
    expiry = SMC06_IFVG.expiry
    ifvg_sl_buffer = 0.005
    ifvg_rr = 2.5
    # True: inverse FVG trades keep their own exits (limit target, no CHoCH exit).
    # False: they exit exactly like the order-block trades.
    ifvg_own_exits = True
    # True: one circuit breaker per model instead of the shared StoplossGuard
    model_guard = False
    guard_lookback = timedelta(days=20)
    guard_limit = 2
    guard_pause = timedelta(days=10)
    stop_exits = ("stop_loss", "stoploss_on_exchange", "trailing_stop_loss", "liquidation")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._stop_cache: dict = {}

    @property
    def protections(self):
        return [] if self.model_guard else super().protections

    # ------------------------------------------------------------------ indicators
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(dataframe, metadata)
        o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        sig, sl, _ = SMC06_IFVG.setup(self, o, h, lo, c, {})
        df["ifvg_long"], df["ifvg_long_sl"] = sig.astype(int), sl
        # short side = the long rules on mirrored prices
        sig, sl, _ = SMC06_IFVG.setup(self, -o, -lo, -h, -c, {})
        df["ifvg_short"], df["ifvg_short_sl"] = sig.astype(int), -sl
        return df

    # ------------------------------------------------------------------ signals
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        c = df["close"]
        for side, sign in (("long", 1), ("short", -1)):
            if side == "short" and not self.can_short:
                continue
            risk = sign * (c - df[f"ifvg_{side}_sl"]) / c
            cond = (
                (df[f"ifvg_{side}"] == 1)
                & (df["volume"] > 0)
                & (risk > 0)
                & (risk <= self.max_risk.value)
            )
            if self.use_ema_filter.value:
                cond &= (c > df["ema"]) if sign == 1 else (c <= df["ema"])
            if self.use_btc_filter.value:
                cond &= df["bull_btc"] == (1 if sign == 1 else 0)
            tag = self.ifvg_tag if sign == 1 else f"{self.ifvg_tag}_short"
            df.loc[cond, [f"enter_{side}", "enter_tag"]] = (1, tag)
        # SMCDaily's order-block entries are written last, so they win on a shared candle
        return super().populate_entry_trend(df, metadata)

    def _model(self, tag: str | None) -> str:
        return "ifvg" if (tag or "").startswith(self.ifvg_tag) else "ob"

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        """Per-model StoplossGuard, replicating freqtrade's: at every exit of this model, if it
        had `guard_limit` losing stop-outs within `guard_lookback`, the model takes no new
        entries until `guard_pause` after the latest of them (rounded up to the next candle)."""
        if not self.model_guard:
            return True
        model = self._model(entry_tag)
        candle = timedelta(minutes=timeframe_to_minutes(self.timeframe))
        pause = self.guard_pause + candle  # freqtrade rounds the lock end up to the next candle
        since = current_time - self.guard_lookback - pause - candle
        closed = [
            t for t in Trade.get_trades_proxy(is_open=False)
            if t.close_date_utc > since and self._model(t.enter_tag) == model
        ]
        stops = [
            t.close_date_utc for t in closed
            if t.exit_reason in self.stop_exits and (t.close_profit or 0) < 0
        ]
        for t in closed:  # the guard is evaluated each time a trade closes
            at = t.close_date_utc
            window = [s for s in stops if at - self.guard_lookback < s <= at]
            if len(window) >= self.guard_limit and max(window) + pause > current_time:
                return False
        return True

    # ------------------------------------------------------------------ risk
    def _is_ifvg(self, trade: Trade) -> bool:
        return self._model(trade.enter_tag) == "ifvg"

    def _own_exits(self, trade: Trade) -> bool:
        return self.ifvg_own_exits and self._is_ifvg(trade)

    def _entry_stop(self, pair: str, trade: Trade) -> float | None:
        if not self._is_ifvg(trade):
            return super()._entry_stop(pair, trade)
        key = (pair, trade.open_date_utc, trade.is_short)
        if key not in self._stop_cache:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            signal = df.loc[df["date"] < trade.open_date_utc]
            col = "ifvg_short_sl" if trade.is_short else "ifvg_long_sl"
            if signal.empty or np.isnan(signal[col].iat[-1]):
                return None
            buffer = self.ifvg_sl_buffer if self.ifvg_own_exits else self.sl_buffer.value
            side = 1 if trade.is_short else -1  # buffer goes beyond the level, away from price
            self._stop_cache[key] = signal[col].iat[-1] * (1 + side * buffer)
        return self._stop_cache[key]

    def custom_roi(self, pair: str, trade: Trade, current_time: datetime, trade_duration: int,
                   entry_tag: str | None, side: str, **kwargs) -> float | None:
        """Limit-style 2.5R target for inverse FVG trades (order-block trades use custom_exit)."""
        if not self._own_exits(trade):
            return None
        stop = self._entry_stop(pair, trade)
        if stop is None:
            return None
        sign = -1 if trade.is_short else 1
        target = trade.open_rate + sign * self.ifvg_rr * abs(trade.open_rate - stop)
        roi = trade.calc_profit_ratio(target)
        return roi if roi > 0 else None

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> str | None:
        if self._own_exits(trade):
            return None
        return super().custom_exit(pair, trade, current_time, current_rate, current_profit,
                                   **kwargs)

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time: datetime, **kwargs) -> bool:
        # CHoCH exit signals are for order-block trades only
        if self._own_exits(trade) and exit_reason in ("choch_down", "choch_up", "exit_signal"):
            return False
        return True


class SMCCombo_SameExits(SMCCombo):
    """Inverse FVG entries managed exactly like the order-block trades."""

    ifvg_own_exits = False


class SMCCombo_ModelGuard(SMCCombo):
    """Each model keeps its own exits and its own circuit breaker."""

    model_guard = True


class SMCCombo_SameExits_ModelGuard(SMCCombo_SameExits):
    """Order-block exits for both models, one circuit breaker per model."""

    model_guard = True
