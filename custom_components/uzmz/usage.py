"""Keep readings current between logins using USMS's usage reports.

USMS's Home cards show remaining units and balance as of login, and keep
showing that snapshot for the rest of the session. Its usage reports are
live. So at each new session we store an anchor: the Home snapshot plus
the usage the reports showed at that moment. Later,

    used since login = usage the reports show now - usage they showed then
    remaining units  = anchor units - used
    balance          = anchor balance - used * rate   (an estimate)

Comparing what the reports show (rather than counting hours from the login
time) also handles data USMS posts late, like water's daily figures.

The rate is learned from the change between two login snapshots; until two
are available, the snapshot's own balance-per-unit ratio is used.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

# Days before the anchor date covered by the comparison, to catch late data.
LOOKBACK_DAYS = 3
# Oldest day the Daily report accepts ("Max 1 month").
MAX_REPORT_DAYS = 30
# Ignore rate samples over tiny unit changes.
MIN_UNITS_FOR_RATE = 1.0


def parse_number(value: Any) -> Optional[float]:
    """'2432.223 kWh' / '$220.97' -> float; None if there is no number."""
    cleaned = "".join(c for c in str(value or "") if c in "0123456789.")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def window_start(anchor: dict[str, Any], today: dt.date) -> tuple[dt.date, bool]:
    """First day to fetch usage for, and whether it had to be shortened."""
    wanted = dt.date.fromisoformat(anchor["date"]) - dt.timedelta(days=LOOKBACK_DAYS)
    oldest = today - dt.timedelta(days=MAX_REPORT_DAYS)
    return (wanted, False) if wanted >= oldest else (oldest, True)


def make_anchor(
    units: float, balance: float, today: dt.date, usage: dict[str, float], approximate: bool
) -> dict[str, Any]:
    """Anchor a new session's snapshot to the usage reported right now."""
    return {
        "units": units,
        "balance": balance,
        "date": today.isoformat(),
        "usage": usage,
        "approximate": approximate,
    }


def learn_rate(
    previous: Optional[dict[str, Any]], units: float, balance: float, old_rate: Optional[float]
) -> Optional[float]:
    """Balance per unit from two login snapshots, if they show plain usage."""
    if previous:
        used = previous["units"] - units
        spent = previous["balance"] - balance
        # Skip top-ups (balance went up) and tiny changes.
        if used >= MIN_UNITS_FOR_RATE and spent > 0:
            return round(spent / used, 5)
    return old_rate


def current_readings(
    anchor: dict[str, Any], usage_now: dict[str, float], start: dt.date, rate: Optional[float]
) -> dict[str, Any]:
    """Remaining units and estimated balance from the anchor and live usage."""
    since = start.isoformat()
    then = sum(v for d, v in anchor["usage"].items() if d >= since)
    now = sum(v for d, v in usage_now.items() if d >= since)
    used = max(0.0, round(now - then, 3))

    rate_is_average = rate is None
    if rate is None:
        rate = anchor["balance"] / anchor["units"] if anchor["units"] else 0.0

    return {
        "remaining_unit": round(max(0.0, anchor["units"] - used), 3),
        "remaining_balance": round(max(0.0, anchor["balance"] - used * rate), 2),
        "used_since_sign_in": used,
        "rate": round(rate, 5),
        "rate_is_average": rate_is_average,
    }
