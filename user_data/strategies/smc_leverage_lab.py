"""Leverage experiments for the futures setups (SMCDaily.futures_leverage, default 1x). Lab only."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))
from smc_combo import SMCCombo_ModelGuard  # noqa: E402
from smc_daily import SMCDaily  # noqa: E402


class SMCDaily_Lev3(SMCDaily):
    futures_leverage = 3.0


class SMCDaily_Lev2(SMCDaily):
    futures_leverage = 2.0


class SMCCombo_ModelGuard_Lev3(SMCCombo_ModelGuard):
    futures_leverage = 3.0


class SMCCombo_ModelGuard_Lev2(SMCCombo_ModelGuard):
    futures_leverage = 2.0
