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

Electricity and water are charged with their published domestic tariffs,
which are tiered by monthly consumption, so each unit is costed in the band
it falls in (using the month-to-date total from the reports). Without a
tariff, a rate learned from the change between two login snapshots is used,
and until two are available the snapshot's own balance-per-unit ratio.
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

# DES Tariff A (residential), B$ per kWh by monthly consumption, since
# 2012-01-01: https://www.des.gov.bn/electricity-tariff/
# Each entry is (upper bound of the band in kWh, or None for the last, rate).
ELECTRIC_TARIFF: tuple[tuple[Optional[float], float], ...] = (
    (600, 0.01),
    (2000, 0.08),
    (4000, 0.10),
    (None, 0.12),
)

# Domestic water, B$ per m3 by monthly consumption, as listed by USMS
# (https://www.usms.com.bn/web/tariffs/); the 54.54 m3 / B$0.11 first band
# also appears in PWD's "Water Facts". The band is 54.54 m3 per unit in the
# building; a house is one unit.
WATER_TARIFF: tuple[tuple[Optional[float], float], ...] = (
    (54.54, 0.11),
    (None, 0.44),
)


def parse_number(value: Any) -> Optional[float]:
    """'2432.223 kWh' / '$220.97' -> float; None if there is no number."""
    cleaned = "".join(c for c in str(value or "") if c in "0123456789.")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def new_session_start(today: dt.date) -> dt.date:
    """First day to fetch at sign-in: catch late data and the month so far."""
    return min(today - dt.timedelta(days=LOOKBACK_DAYS), today.replace(day=1))


def _anchor_from(anchor: dict[str, Any]) -> dt.date:
    """First day the anchor's usage covers (older anchors: date - lookback)."""
    if "from" in anchor:
        return dt.date.fromisoformat(anchor["from"])
    return dt.date.fromisoformat(anchor["date"]) - dt.timedelta(days=LOOKBACK_DAYS)


def window_start(anchor: dict[str, Any], today: dt.date) -> tuple[dt.date, bool]:
    """First day to fetch usage for, and whether it had to be shortened.

    It covers the anchor's own range (to measure usage since sign-in). Anchors
    from before the month-to-date total was stored also need the start of
    the sign-in month, to work that total out from the reports.
    """
    wanted = _anchor_from(anchor)
    if "month_to_date" not in anchor:
        wanted = min(wanted, dt.date.fromisoformat(anchor["date"]).replace(day=1))
    oldest = today - dt.timedelta(days=MAX_REPORT_DAYS)
    return (wanted, False) if wanted >= oldest else (oldest, True)


def tariff_cost(kwh: float, tariff=ELECTRIC_TARIFF) -> float:
    """Charge for kwh units used in one month under a tiered tariff."""
    cost, lower = 0.0, 0.0
    for upper, rate in tariff:
        if upper is None or kwh <= upper:
            return cost + (kwh - lower) * rate
        cost += (upper - lower) * rate
        lower = upper
    return cost


def marginal_rate(kwh: float, tariff=ELECTRIC_TARIFF) -> float:
    """Rate of the band the next unit falls in, kwh into the month."""
    for upper, rate in tariff:
        if upper is None or kwh < upper:
            return rate
    return tariff[-1][1]


def make_anchor(
    units: float,
    balance: float,
    today: dt.date,
    usage: dict[str, float],
    approximate: bool,
    start: Optional[dt.date] = None,
) -> dict[str, Any]:
    """Anchor a new session's snapshot to the usage reported right now.

    The month-to-date total is kept so the tariff band is known later, even
    once the start of the month is past the reports' one-month range.
    """
    start = start or today - dt.timedelta(days=LOOKBACK_DAYS)
    month = today.isoformat()[:7]
    anchor = {
        "units": units,
        "balance": balance,
        "date": today.isoformat(),
        # Older days can't change any more, so later comparisons start here.
        "from": max(start, today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat(),
        "usage": usage,
        "approximate": approximate,
    }
    if start <= today.replace(day=1):
        anchor["month_to_date"] = round(sum(v for d, v in usage.items() if d[:7] == month), 3)
    return anchor


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
    anchor: dict[str, Any],
    usage_now: dict[str, float],
    start: dt.date,
    rate: Optional[float],
    tariff=None,
) -> dict[str, Any]:
    """Remaining units and estimated balance from the anchor and live usage.

    With a tariff, the balance is charged band by band from each month's
    month-to-date total; otherwise at a flat rate (learned, or the average).
    """
    since = max(start, _anchor_from(anchor)).isoformat()
    # Usage that appeared in the reports since sign-in, day by day.
    delta = {
        day: max(0.0, value - anchor["usage"].get(day, 0.0))
        for day, value in usage_now.items()
        if day >= since
    }
    used = max(0.0, round(sum(delta.values()), 3))

    result: dict[str, Any] = {
        "remaining_unit": round(max(0.0, anchor["units"] - used), 3),
        "used_since_sign_in": used,
    }

    if tariff:
        spent, latest_month_total = 0.0, 0.0
        anchor_month = anchor["date"][:7]
        for month in sorted({day[:7] for day, d in delta.items() if d}):
            month_used = sum(d for day, d in delta.items() if day[:7] == month)
            if month == anchor_month and "month_to_date" in anchor:
                before = anchor["month_to_date"]
            else:
                # Later months start after sign-in; older anchors need the
                # reports from the 1st (see window_start).
                before = sum(v for day, v in usage_now.items() if day[:7] == month) - month_used
            spent += tariff_cost(before + month_used, tariff) - tariff_cost(before, tariff)
            latest_month_total = before + month_used
        if not any(delta.values()):
            latest_month_total = anchor.get("month_to_date") or sum(
                v for day, v in usage_now.items() if day[:7] == max(usage_now, default="")[:7]
            )
        result.update(
            {
                "remaining_balance": round(max(0.0, anchor["balance"] - spent), 2),
                "rate": marginal_rate(latest_month_total, tariff),
                "rate_is_average": False,
                "balance_method": "tariff",
                "month_to_date": round(latest_month_total, 3),
            }
        )
        return result

    rate_is_average = rate is None
    if rate is None:
        rate = anchor["balance"] / anchor["units"] if anchor["units"] else 0.0
    result.update(
        {
            "remaining_balance": round(max(0.0, anchor["balance"] - used * rate), 2),
            "rate": round(rate, 5),
            "rate_is_average": rate_is_average,
            "balance_method": "average rate" if rate_is_average else "learned rate",
        }
    )
    return result


# Readings worked out from the reports, which the login snapshot can't give.
ESTIMATE_KEYS = (
    "remaining_unit",
    "remaining_balance",
    "used_since_sign_in",
    "sign_in_units",
    "sign_in_balance",
    "sign_in_date",
    "rate",
    "rate_is_average",
    "balance_method",
    "month_to_date",
    "approximate",
    "usage_updated",
)


def last_estimate(previous: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The previous poll's report-based readings, to reuse when a report fails.

    The login snapshot is older, so falling back to it would make the
    remaining units jump back up (and usage counters count the drop twice).
    A previous poll that reused one passes it on, so it lasts until the
    reports come back.
    """
    if not previous or "used_since_sign_in" not in previous:
        return {}
    return {key: previous[key] for key in ESTIMATE_KEYS if key in previous}


def recent_usage(usage_now: dict[str, float], today: dt.date) -> dict[str, float]:
    """The last few days of a usage report, kept to spot when USMS posts data."""
    cutoff = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    return {day: value for day, value in usage_now.items() if day >= cutoff}


def usage_changed(previous: Optional[dict[str, float]], current: dict[str, float]) -> bool:
    """Whether the reports show new data since the previous poll.

    A day's total changing, or a new day with usage, means USMS posted
    readings. Days that drop out of the window don't count. With no previous
    poll there is nothing to compare, so it's False.
    """
    if previous is None:
        return False
    for day, value in current.items():
        old = previous.get(day)
        if old is None:
            if value > 0:
                return True
        elif value != old:
            return True
    return False
