"""Write USMS's usage and cost to Home Assistant's long-term statistics.

Each meter gets `uzmz:<meter>_consumption` and `uzmz:<meter>_cost`, hour by
hour at the hours the usage happened (`hourly.py` works the hours out). For
the Energy dashboard these replace the helper counters, which can only add
usage at the moment UZMZ notices it.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant

from .const import BRUNEI_TZ, DOMAIN
from .hourly import costs, day_rows, running_sums

_LOGGER = logging.getLogger(__name__)

# How far back to look for the last sum before the rewritten window.
SUM_LOOKBACK = dt.timedelta(days=45)

UNITS = {"electric": ("kWh", "energy"), "water": ("m³", "volume")}


def statistic_id(meter: str, kind: str) -> str:
    return f"{DOMAIN}:{meter}_{kind}"


def _metadata(meter: str, kind: str) -> dict[str, Any]:
    unit, unit_class = UNITS[meter] if kind == "consumption" else ("BND", None)
    label = "Usage" if kind == "consumption" else "Cost"
    return {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": f"UZMZ {meter.title()} {label}",
        "source": DOMAIN,
        "statistic_id": statistic_id(meter, kind),
        "unit_class": unit_class,
        "unit_of_measurement": unit,
    }


async def _async_read(
    hass: HomeAssistant, ids: set[str], start: dt.datetime, end: Optional[dt.datetime]
) -> dict[str, list[dict[str, Any]]]:
    return await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, ids, "hour", None, {"change", "sum"}
    )


async def async_has_history(hass: HomeAssistant, meter: str) -> bool:
    """Whether this meter's usage statistic has any hours yet."""
    ids = {statistic_id(meter, "consumption")}
    now = dt.datetime.now(BRUNEI_TZ)
    found = await _async_read(hass, ids, now - SUM_LOOKBACK, None)
    return bool(found.get(statistic_id(meter, "consumption")))


async def async_publish(
    hass: HomeAssistant,
    meter: str,
    usage: dict[str, float],
    hours: Optional[dict[int, float]],
    price: Optional[float],
) -> None:
    """Rewrite the meter's hours from the first day in usage to now."""
    if not usage:
        return
    today = dt.datetime.now(BRUNEI_TZ).date()
    first = dt.date.fromisoformat(min(usage))
    start = dt.datetime.combine(first, dt.time(), BRUNEI_TZ)
    use_id, cost_id = statistic_id(meter, "consumption"), statistic_id(meter, "cost")

    written = await _async_read(hass, {use_id, cost_id}, start, None)
    existing = _changes(written.get(use_id, []))
    existing_cost = _changes(written.get(cost_id, []))
    before = await _async_read(hass, {use_id, cost_id}, start - SUM_LOOKBACK, start)

    rows = day_rows(usage, hours, existing, today, BRUNEI_TZ)
    if not rows:
        return
    for kind, values in (("consumption", rows), ("cost", costs(rows, existing, existing_cost, price))):
        base = _last_sum(before.get(statistic_id(meter, kind), []))
        stats = [
            {"start": hour, "state": total, "sum": total}
            for hour, total in running_sums(values, base)
        ]
        if stats:
            async_add_external_statistics(hass, _metadata(meter, kind), stats)
    _LOGGER.debug("Wrote %d hours of %s usage from %s", len(rows), meter, first)


def _changes(rows: list[dict[str, Any]]) -> dict[dt.datetime, float]:
    return {
        dt.datetime.fromtimestamp(row["start"], BRUNEI_TZ): row["change"]
        for row in rows
        if row.get("change") is not None
    }


def _last_sum(rows: list[dict[str, Any]]) -> float:
    sums = [row["sum"] for row in rows if row.get("sum") is not None]
    return sums[-1] if sums else 0.0
