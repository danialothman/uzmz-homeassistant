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

Prepaid meters are priced at top-up time, not when units are used: the
published tariff bands apply to the units bought in a calendar month
("tiered rates restart each month; top-ups earlier in the month use up the
cheaper blocks first"), and the balance is what the remaining units cost.
Units are used oldest first. So for electricity and water UZMZ keeps the
remaining units as priced layers (oldest first), checked against USMS's own
balance at every sign-in:

- a top-up (units and balance up by more than usage explains) adds layers,
  priced by the units already bought that month;
- usage takes units off the front, and costs what they cost;
- the first time, or if the layers no longer match USMS's balance, they're
  inferred from the balance-per-unit ratio as two adjacent bands.

Without a tariff, a rate learned from the change between two login
snapshots is used, and until two are available the snapshot's own ratio.
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
# Smallest rise in units and balance (beyond what usage explains) treated as
# a top-up. USMS's smallest top-ups are a few dollars, so far above this.
MIN_TOP_UP_UNITS = 1.0
MIN_TOP_UP_AMOUNT = 1.0
# How far the layers' value may drift from USMS's balance before they're
# inferred again (rounding to cents and late usage reports cause a little).
LAYERS_TOLERANCE = 0.2

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


def tariff_slices(before: float, units: float, tariff) -> list[list[float]]:
    """Price layers for units bought after `before` units this month."""
    layers, lower, end = [], 0.0, before + units
    for upper, rate in tariff:
        top = end if upper is None else min(upper, end)
        part = top - max(before, lower)
        if part > 1e-9:
            layers.append([round(part, 3), rate])
        if upper is None or upper >= end:
            break
        lower = upper
    return layers


def layers_value(layers: list[list[float]]) -> float:
    """What the remaining units cost (USMS's balance, if the layers are right)."""
    return sum(units * rate for units, rate in layers)


def use_layers(layers: list[list[float]], used: float) -> tuple[float, list[list[float]]]:
    """Take used units off the front: their cost, and the layers left.

    Using more than the layers hold costs the rest at the last layer's rate
    (the meter can't actually go below 0).
    """
    cost, left, rest = 0.0, max(0.0, used), []
    for units, rate in layers:
        take = min(units, left)
        cost += take * rate
        left -= take
        if units - take > 1e-9:
            rest.append([round(units - take, 3), rate])
    if left > 1e-9 and layers:
        cost += left * layers[-1][1]
    return cost, rest


def infer_layers(units: float, balance: float, tariff) -> list[list[float]]:
    """Best guess at the layers from the units and balance alone.

    The balance-per-unit ratio lies between two adjacent band rates, which
    gives how many units are at each (cheaper ones first, as within a month's
    top-ups). Outside the band rates, one layer at the ratio.
    """
    if units <= 0:
        return []
    ratio = balance / units
    rates = sorted({rate for _, rate in tariff})
    for low, high in zip(rates, rates[1:]):
        if low <= ratio <= high:
            at_low = min(units, max(0.0, (high * units - balance) / (high - low)))
            layers = [[round(at_low, 3), low], [round(units - at_low, 3), high]]
            return [layer for layer in layers if layer[0] > 0]
    return [[round(units, 3), round(ratio, 5)]]


def price_top_up(
    bought: float, amount: float, tariff, known_before: Optional[float]
) -> tuple[float, list[list[float]]]:
    """Units already bought this month before a top-up, and its layers.

    The known count is used if it explains the amount paid; otherwise (UZMZ
    missed an earlier top-up, or this one was made last month) it's worked
    out from the amount, as the fewest units before that make it cost that.
    """

    def cost(before: float) -> float:
        return tariff_cost(before + bought, tariff) - tariff_cost(before, tariff)

    # The amount is measured to a few cents (rounding, late usage reports).
    if known_before is not None and abs(cost(known_before) - amount) <= max(0.05, 0.01 * amount):
        before = known_before
    elif cost(0.0) >= amount - 0.05:
        before = 0.0
    else:
        # Cost only grows with the units before, so bisect up to the last band.
        low, high = 0.0, max(upper for upper, _ in tariff if upper is not None)
        if cost(high) >= amount - 0.05:
            for _ in range(60):
                mid = (low + high) / 2
                if cost(mid) >= amount - 0.05:
                    high = mid
                else:
                    low = mid
        before = round(high, 3)
    return before, tariff_slices(before, bought, tariff)


def month_top_ups(state: Optional[dict[str, Any]], today: dt.date) -> dict[str, Any]:
    """This month's top-up count, restarted on the 1st.

    `units` is what was bought this month, or None if unknown (UZMZ was set up
    mid-month and hasn't seen a top-up since).
    """
    month = today.isoformat()[:7]
    if state is None:
        return {"month": month, "units": None}
    if state.get("month") != month:
        return {"month": month, "units": 0.0}
    return state


def settle_layers(
    previous: Optional[dict[str, Any]],
    units: float,
    balance: float,
    used: float,
    tariff,
    top_ups: dict[str, Any],
) -> tuple[list[list[float]], dict[str, Any], Optional[float]]:
    """The layers at a new sign-in, from the previous one and usage since.

    Returns the layers, the month's top-ups, and the amount topped up since
    the previous sign-in (None if there wasn't a top-up).
    """
    if not previous:
        return infer_layers(units, balance, tariff), top_ups, None
    # Anchors from before 1.8.0 have no layers yet.
    old = previous.get("layers")
    if old is None:
        old = infer_layers(previous["units"], previous["balance"], tariff)

    cost, layers = use_layers(old, used)
    bought = units - (previous["units"] - used)
    amount = balance - (previous["balance"] - cost)
    topped_up = None
    if bought >= MIN_TOP_UP_UNITS and amount >= MIN_TOP_UP_AMOUNT:
        before, new = price_top_up(bought, amount, tariff, top_ups.get("units"))
        layers = layers + new
        top_ups = {**top_ups, "units": round(before + bought, 3)}
        topped_up = round(amount, 2)
    else:
        # No top-up: the meter's own drop is the most exact usage figure.
        _, layers = use_layers(old, max(0.0, previous["units"] - units))

    total = sum(layer_units for layer_units, _ in layers)
    if abs(total - units) > MIN_TOP_UP_UNITS or abs(layers_value(layers) - balance) > LAYERS_TOLERANCE:
        layers = infer_layers(units, balance, tariff)
    return layers, top_ups, topped_up


def top_up_plan(top_ups: dict[str, Any], tariff) -> dict[str, Any]:
    """What a top-up buys now: this month's spend, its price and room in the band."""
    bought = top_ups.get("units")
    if bought is None:
        return {"topped_up_this_month": None, "top_up_price": None, "top_up_left_at_price": None}
    rate = marginal_rate(bought, tariff)
    upper = next((u for u, r in tariff if u is not None and bought < u), None)
    return {
        "topped_up_this_month": round(tariff_cost(bought, tariff), 2),
        "bought_this_month": round(bought, 3),
        "top_up_price": rate,
        "top_up_left_at_price": round((upper - bought) * rate, 2) if upper is not None else None,
    }


def _money(value: float) -> str:
    return f"B${value:,.2f}"


def _price(rate: float, unit: str) -> str:
    return f"B${rate:.2f}/{unit}"


def top_up_advice(top_ups: dict[str, Any], tariff, unit: str) -> str:
    """One sentence on what to top up this month, for people, not automations."""
    first_upper, first_rate = tariff[0]
    bought = top_ups.get("units")
    if bought is None:
        return (
            f"Prices restart each month: the first {_money(first_upper * first_rate)} buys "
            f"{first_upper:g} {unit} at {_price(first_rate, unit)}. UZMZ will know this "
            "month's price after your next top-up, or from the 1st."
        )
    rate = marginal_rate(bought, tariff)
    upper = next((u for u, _ in tariff if u is not None and bought < u), None)
    if upper is None:
        return (
            f"This month's top-ups now buy at {_price(rate, unit)}, the highest price. "
            "Prices restart on the 1st."
        )
    room = upper - bought
    return (
        f"Up to {_money(room * rate)} more this month buys {room:,.{2 if room < 100 else 0}f} {unit} at "
        f"{_price(rate, unit)}; above that, {_price(marginal_rate(upper, tariff), unit)}. "
        "Prices restart on the 1st."
    )


def average_daily_use(usage_now: dict[str, float], today: dt.date, days: int = 7) -> Optional[float]:
    """Average use over the last few complete days the reports have posted.

    Days not posted yet (water lags a day or more) show 0 and are skipped.
    """
    posted = sorted(
        (day, value) for day, value in usage_now.items() if day < today.isoformat() and value > 0
    )[-days:]
    if not posted:
        return None
    return round(sum(value for _, value in posted) / len(posted), 3)


def next_price_change(
    layers: list[list[float]], daily_use: Optional[float], today: dt.date
) -> dict[str, Any]:
    """When the units in use now run out and the next price starts.

    Prices within a cent are the same price (a layer inferred from the
    balance can be a fraction off a band rate).
    """
    result: dict[str, Any] = {
        "price_change": None, "price_now": None, "next_price": None, "units_at_price_now": None,
    }
    if not layers:
        return result
    price_now = result["price_now"] = layers[0][1]
    units = 0.0
    for layer_units, rate in layers:
        if round(rate, 2) != round(price_now, 2):
            result["next_price"] = rate
            break
        units += layer_units
    result["units_at_price_now"] = round(units, 3)
    if result["next_price"] is not None and daily_use:
        days = int(units / daily_use)
        result["price_change"] = (today + dt.timedelta(days=days)).isoformat()
    result["daily_use"] = daily_use
    return result


def make_anchor(
    units: float,
    balance: float,
    today: dt.date,
    usage: dict[str, float],
    approximate: bool,
    start: Optional[dt.date] = None,
    layers: Optional[list[list[float]]] = None,
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
    if layers is not None:
        anchor["layers"] = layers
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

    With a tariff, the units used come off the anchor's price layers (see
    the module docstring); otherwise they're charged at a flat rate (learned,
    or the average).
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
        layers = anchor.get("layers")
        if layers is None:
            layers = infer_layers(anchor["units"], anchor["balance"], tariff)
        spent, rest = use_layers(layers, used)
        rate = rest[0][1] if rest else (layers[-1][1] if layers else 0.0)
        result.update(
            {
                "remaining_balance": round(max(0.0, anchor["balance"] - spent), 2),
                # What the units being used now cost.
                "rate": rate,
                "rate_is_average": False,
                "balance_method": "top-up prices",
                "price_layers": rest,
                "month_to_date": _month_to_date(anchor, delta, usage_now),
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


def _month_to_date(anchor: dict[str, Any], delta: dict[str, float], usage_now: dict[str, float]) -> float:
    """This month's usage so far (for information; prices follow top-ups)."""
    month = max([day for day, d in delta.items() if d] or [anchor["date"]])[:7]
    month_used = sum(d for day, d in delta.items() if day[:7] == month)
    if month == anchor["date"][:7] and "month_to_date" in anchor:
        return round(anchor["month_to_date"] + month_used, 3)
    return round(sum(v for day, v in usage_now.items() if day[:7] == month), 3)


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
    "price_layers",
    "price_change",
    "price_now",
    "next_price",
    "units_at_price_now",
    "daily_use",
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


def last_posted(usage_now: dict[str, float], now: dt.datetime) -> Optional[str]:
    """When USMS last posted usage, judged from the reports alone.

    Used before a change has been seen, so a meter USMS has stopped posting
    for isn't shown as unknown. A past day's total was posted after that day
    ended at the earliest; usage today means it is posting now.
    """
    days = [day for day, value in usage_now.items() if value > 0]
    if not days:
        return None
    latest = dt.date.fromisoformat(max(days))
    if latest >= now.date():
        return now.isoformat()
    return dt.datetime.combine(latest + dt.timedelta(days=1), dt.time(), now.tzinfo).isoformat()


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
