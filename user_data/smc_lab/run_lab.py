"""Run SMC variants on 4 setups in parallel and print a ranked comparison.

usage: python run_lab.py <run_name> <Strategy> [<Strategy> ...]
       LAB_SETUPS=holdout python run_lab.py ...   (10 never-used pairs, spot + futures)
       LAB_SETUPS=orig_fut,uns_fut ...            (any comma-separated subset)
       LAB_TF=1h LAB_TIMERANGE=20230115- ...      (timeframe / timerange override)
       LAB_ARGS="--max-open-trades 8" ...         (extra freqtrade arguments)
"""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from freqtrade.data.btanalysis import load_backtest_stats

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "user_data" / "smc_lab"
ALL_SETUPS = {
    "orig_spot": "user_data/config_smc.json",
    "orig_fut": "user_data/config_smc_futures.json",
    "uns_spot": "user_data/config_smc_unseen.json",
    "uns_fut": "user_data/config_smc_futures_unseen.json",
    "hold_spot": "user_data/config_smc_holdout.json",
    "hold_fut": "user_data/config_smc_futures_holdout.json",
}
_sel = os.environ.get("LAB_SETUPS", "orig_spot,orig_fut,uns_spot,uns_fut")
if _sel == "holdout":
    _sel = "hold_spot,hold_fut"
SETUPS = {k: ALL_SETUPS[k] for k in _sel.split(",")}
TIMERANGE = os.environ.get("LAB_TIMERANGE", "20210101-")
TIMEFRAME = os.environ.get("LAB_TF")
EXTRA_ARGS = os.environ.get("LAB_ARGS", "").split()


def run(run_name, setup, strategies):
    d = OUT / run_name / setup
    d.mkdir(parents=True, exist_ok=True)
    cmd = [str(ROOT / ".venv/bin/freqtrade"), "backtesting", "-c", SETUPS[setup],
           "--timerange", TIMERANGE, "--enable-protections", "--export", "trades",
           "--backtest-directory", str(d), "--strategy-list", *strategies]
    if TIMEFRAME:
        cmd += ["--timeframe", TIMEFRAME]
    cmd += EXTRA_ARGS
    for _ in range(3):
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        (d / "log.txt").write_text(p.stdout + p.stderr)
        if p.returncode == 0:
            return load_backtest_stats(d)["strategy"]
    raise RuntimeError(f"{setup} failed, see {d / 'log.txt'}")


def main():
    run_name, strategies = sys.argv[1], sys.argv[2:]
    with ThreadPoolExecutor(len(SETUPS)) as ex:
        res = dict(zip(SETUPS, ex.map(lambda s: run(run_name, s, strategies), SETUPS)))

    rows = []
    for s in strategies:
        cells, ratios = [], []
        for setup in SETUPS:
            r = res[setup][s]
            prof, dd = 100 * r["profit_total"], 100 * r["max_drawdown_account"]
            cagr = 100 * r["cagr"]
            ratio = cagr / dd if dd else 0.0
            ratios.append(ratio)
            cells.append(f"{prof:+7.1f}% dd{dd:5.1f}% r{ratio:4.2f} n{r['total_trades']:3d}")
        rows.append((sum(ratios) / len(ratios), min(ratios), s, cells))
    rows.sort(reverse=True)
    w = max(20, *(len(s) for s in strategies))
    lines = [f"{'strategy':{w}} {'avg r':>6} {'min r':>6} | "
             + " | ".join(f"{k:^31}" for k in SETUPS)]
    for avg, mn, s, cells in rows:
        lines.append(f"{s:{w}} {avg:6.2f} {mn:6.2f} | " + " | ".join(cells))
    lines.append("\nr = CAGR / max drawdown (account); profit and drawdown are over the whole period")
    text = "\n".join(lines)
    print(text)
    (OUT / run_name / f"ranking_{'_'.join(SETUPS)}.txt").write_text(text + "\n")


if __name__ == "__main__":
    main()
