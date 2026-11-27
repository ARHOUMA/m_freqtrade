"""Fear & Greed filter experiments (see SMCDaily.use_fng_filter). Lab only, not deployed.

Contrarian (block extremes): <base>_FNG<long_max>_<short_min>; 100 / 0 = side unfiltered.
Momentum (trend confirmation): <base>_MOM<long_min>_<short_max>; longs only with the index
>= long_min, shorts only with it <= short_max. _NoBTC = BTC 200-day filter off, sentiment
replaces it.
(Written out by hand because freqtrade only finds classes spelled `class Name(` in the file.)
"""

import sys
from pathlib import Path

from freqtrade.strategy import BooleanParameter, IntParameter


sys.path.insert(0, str(Path(__file__).parent))
from smc_combo import SMCCombo_ModelGuard  # noqa: E402
from smc_daily import SMCDaily  # noqa: E402


class _FNGOn:
    use_fng_filter = BooleanParameter(default=True, space="buy")


class SMCDaily_FNG70_0(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=70, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")


class SMCDaily_FNG80_0(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=80, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")


class SMCDaily_FNG90_0(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=90, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")


class SMCDaily_FNG100_20(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=20, space="buy")


class SMCDaily_FNG100_30(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=30, space="buy")


class SMCDaily_FNG80_20(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=80, space="buy")
    fng_short_min = IntParameter(0, 50, default=20, space="buy")


class SMCDaily_FNG75_25(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=75, space="buy")
    fng_short_min = IntParameter(0, 50, default=25, space="buy")


class SMCCombo_ModelGuard_FNG70_0(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=70, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")


class SMCCombo_ModelGuard_FNG80_0(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=80, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")


class SMCCombo_ModelGuard_FNG90_0(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=90, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")


class SMCCombo_ModelGuard_FNG100_20(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=20, space="buy")


class SMCCombo_ModelGuard_FNG100_30(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=30, space="buy")


class SMCCombo_ModelGuard_FNG80_20(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=80, space="buy")
    fng_short_min = IntParameter(0, 50, default=20, space="buy")


class SMCCombo_ModelGuard_FNG75_25(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=75, space="buy")
    fng_short_min = IntParameter(0, 50, default=25, space="buy")


class SMCDaily_MOM25_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=25, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCDaily_MOM40_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=40, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCDaily_MOM50_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=50, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCDaily_MOM60_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=60, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCDaily_MOM40_60(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=40, space="buy")
    fng_short_max = IntParameter(30, 100, default=60, space="buy")


class SMCDaily_MOM50_50(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=50, space="buy")
    fng_short_max = IntParameter(30, 100, default=50, space="buy")


class SMCDaily_MOM0_50(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=0, space="buy")
    fng_short_max = IntParameter(30, 100, default=50, space="buy")


class SMCDaily_MOM40_60_NoBTC(SMCDaily_MOM40_60):
    use_btc_filter = BooleanParameter(default=False, space="buy")


class SMCDaily_MOM50_50_NoBTC(SMCDaily_MOM50_50):
    use_btc_filter = BooleanParameter(default=False, space="buy")


class SMCCombo_ModelGuard_MOM25_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=25, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM40_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=40, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM50_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=50, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM60_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=60, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM40_60(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=40, space="buy")
    fng_short_max = IntParameter(30, 100, default=60, space="buy")


class SMCCombo_ModelGuard_MOM50_50(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=50, space="buy")
    fng_short_max = IntParameter(30, 100, default=50, space="buy")


class SMCCombo_ModelGuard_MOM0_50(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=0, space="buy")
    fng_short_max = IntParameter(30, 100, default=50, space="buy")


class SMCCombo_ModelGuard_MOM40_60_NoBTC(SMCCombo_ModelGuard_MOM40_60):
    use_btc_filter = BooleanParameter(default=False, space="buy")


class SMCCombo_ModelGuard_MOM50_50_NoBTC(SMCCombo_ModelGuard_MOM50_50):
    use_btc_filter = BooleanParameter(default=False, space="buy")


class SMCDaily_MOM55_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=55, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCDaily_MOM65_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=65, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCDaily_MOM70_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=70, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCDaily_MOM75_100(_FNGOn, SMCDaily):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=75, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM55_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=55, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM65_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=65, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM70_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=70, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")


class SMCCombo_ModelGuard_MOM75_100(_FNGOn, SMCCombo_ModelGuard):
    fng_long_max = IntParameter(50, 100, default=100, space="buy")
    fng_short_min = IntParameter(0, 50, default=0, space="buy")
    fng_long_min = IntParameter(0, 80, default=75, space="buy")
    fng_short_max = IntParameter(30, 100, default=100, space="buy")
