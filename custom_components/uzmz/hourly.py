"""USMS's usage, hour by hour, at the hours it happened.

Home Assistant's own statistics only add usage at the moment it's noticed, so
when USMS posts late (sometimes a day or more), the usage lands in the wrong
hour or day. UZMZ instead writes the hours itself, and rewrites them as USMS
catches up:

- today's hours come from the Hourly report, which only ever shows today;
- a past day's hours are what was written while it was today, made up to
  USMS's Daily total for that day: units posted after the day ended go into
  the hours that never had any, spread evenly (their split can't be known);
- days with only a daily figure (water, and history from before setup) are
  spread evenly over their 24 hours.

Posted figures are never lowered: a Daily total below what's written means
USMS hasn't finished posting that day, not that the hours were wrong.

No Home Assistant imports here, so it can be tested on its own;
`usage_statistics.py` writes the result.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

HOUR = dt.timedelta(hours=1)
# Differences smaller than this are rounding, not missing usage.
TOLERANCE = 0.001


def day_start(day: dt.date, tz: dt.tzinfo) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(), tz)


def hourly_rows(day: dt.date, hours: dict[int, float], tz: dt.tzinfo) -> dict[dt.datetime, float]:
    """The Hourly report's rows as hour starts: row n is the hour ending at n:00."""
    start = day_start(day, tz)
    return {start + (n - 1) * HOUR: units for n, units in hours.items() if 1 <= n <= 24}


def fill_day(
    day: dt.date, rows: dict[dt.datetime, float], total: float, tz: dt.tzinfo
) -> dict[dt.datetime, float]:
    """A finished day's hours, made up to USMS's daily total."""
    start = day_start(day, tz)
    filled = {hour: units for hour, units in rows.items() if start <= hour < start + 24 * HOUR}
    missing = total - sum(filled.values())
    if missing <= TOLERANCE:
        return filled
    empty = [start + n * HOUR for n in range(24) if start + n * HOUR not in filled]
    if empty:
        for hour in empty:
            filled[hour] = missing / len(empty)
    else:
        # Every hour has something: USMS added to hours already posted.
        posted = sum(filled.values())
        filled = {hour: units * total / posted for hour, units in filled.items()}
    return filled


def day_rows(
    usage: dict[str, float],
    hours: Optional[dict[int, float]],
    existing: dict[dt.datetime, float],
    today: dt.date,
    tz: dt.tzinfo,
) -> dict[dt.datetime, float]:
    """Every hour from the first day in usage to now.

    usage is the reports' total per ISO date; hours is today's Hourly report
    (None for a meter without one, whose today is left until it's a past day);
    existing is what was written before.
    """
    rows: dict[dt.datetime, float] = {}
    for iso, total in sorted(usage.items()):
        day = dt.date.fromisoformat(iso)
        if day < today:
            rows.update(fill_day(day, existing, total, tz))
        elif day == today and hours is not None:
            start = day_start(today, tz)
            rows.update({hour: units for hour, units in existing.items() if hour >= start})
            rows.update(hourly_rows(today, hours, tz))
    return rows


def costs(
    rows: dict[dt.datetime, float],
    existing: dict[dt.datetime, float],
    existing_cost: dict[dt.datetime, float],
    price: Optional[float],
) -> dict[dt.datetime, float]:
    """B$ for each hour: what it cost when first written, else the price now.

    An hour already written keeps its price per unit, so a later rewrite
    (USMS catching up) doesn't reprice old usage at today's layer.
    """
    result = {}
    for hour, units in rows.items():
        before, cost = existing.get(hour), existing_cost.get(hour)
        if before and cost is not None:
            result[hour] = round(units * cost / before, 6)
        elif price is not None:
            result[hour] = round(units * price, 6)
    return result


def running_sums(rows: dict[dt.datetime, float], base: float) -> list[tuple[dt.datetime, float]]:
    """(hour start, sum to the end of that hour), from base, in order."""
    total, result = base, []
    for hour in sorted(rows):
        total += rows[hour]
        result.append((hour, round(total, 6)))
    return result
