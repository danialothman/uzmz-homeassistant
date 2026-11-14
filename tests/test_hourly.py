"""Tests for writing USMS's usage at the hours it happened.

The gap is real: USMS posted nothing for electricity from 8 Nov 2026 02:12
until 9 Nov 08:37, then filled it in. Home Assistant's own counter put
16.75 kWh on 8 Nov and 204.14 on 9 Nov; USMS's Daily totals were 101.834
and 118.9.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from uzmz import hourly

TZ = ZoneInfo("Asia/Brunei")


def at(day: int, hour: int) -> dt.datetime:
    return dt.datetime(2026, 11, day, hour, tzinfo=TZ)


def test_row_n_is_the_hour_ending_at_n():
    rows = hourly.hourly_rows(dt.date(2026, 11, 14), {1: 4.6, 2: 4.2, 11: 3.9}, TZ)
    assert rows == {at(14, 0): 4.6, at(14, 1): 4.2, at(14, 10): 3.9}


def test_late_usage_fills_the_hours_that_had_none():
    # Written on 8 Nov before USMS stopped: 00:00-02:00.
    written = {at(8, 0): 5.3, at(8, 1): 5.9}

    day = hourly.fill_day(dt.date(2026, 11, 8), written, 101.834, TZ)

    assert len(day) == 24
    assert day[at(8, 0)] == 5.3 and day[at(8, 1)] == 5.9  # posted hours kept
    assert day[at(8, 2)] == day[at(8, 23)] == pytest.approx((101.834 - 11.2) / 22)
    assert round(sum(day.values()), 3) == 101.834


def test_posted_hours_are_never_lowered():
    # Yesterday's Daily total not posted yet (0), or lower than written so far.
    written = {at(8, hour): 4.0 for hour in range(24)}
    assert hourly.fill_day(dt.date(2026, 11, 8), written, 0.0, TZ) == written
    assert hourly.fill_day(dt.date(2026, 11, 8), written, 90.0, TZ) == written


def test_extra_usage_on_a_full_day_is_spread_in_proportion():
    written = {at(8, hour): 4.0 for hour in range(24)}  # 96 kWh
    day = hourly.fill_day(dt.date(2026, 11, 8), written, 100.0, TZ)
    assert sum(day.values()) == pytest.approx(100.0)
    assert day[at(8, 5)] == pytest.approx(4.0 * 100 / 96)


def test_a_daily_figure_alone_is_spread_over_the_day():
    day = hourly.fill_day(dt.date(2026, 11, 8), {}, 1.68, TZ)  # water, or history
    assert len(day) == 24
    assert all(v == pytest.approx(0.07) for v in day.values())


def test_the_8_november_gap_lands_on_the_right_days():
    # 9 Nov 11:00: USMS has caught up. Hours written while 8 Nov was today,
    # then the Daily report for both days and today's Hourly report.
    written = {at(8, 0): 5.3, at(8, 1): 5.9}
    usage = {"2026-11-08": 101.834, "2026-11-09": 25.0}
    hours = {n: 2.5 for n in range(1, 11)}  # 9 Nov, hours 1-10

    rows = hourly.day_rows(usage, hours, written, dt.date(2026, 11, 9), TZ)

    assert round(sum(v for h, v in rows.items() if h.day == 8), 3) == 101.834
    assert round(sum(v for h, v in rows.items() if h.day == 9), 3) == 25.0
    assert at(9, 10) not in rows  # not posted yet


def test_today_without_an_hourly_report_waits():
    # Water: only daily figures, and none for today.
    rows = hourly.day_rows({"2026-11-13": 1.2, "2026-11-14": 0.0}, None, {}, dt.date(2026, 11, 14), TZ)
    assert {h.day for h in rows} == {13}


def test_todays_hours_are_rewritten_as_usms_posts_more():
    written = {at(14, 10): 1.2}  # partly posted at the last poll
    rows = hourly.day_rows({"2026-11-14": 4.1}, {11: 4.1}, written, dt.date(2026, 11, 14), TZ)
    assert rows == {at(14, 10): 4.1}


def test_rewritten_hours_keep_their_price():
    rows = {at(8, 0): 5.3, at(8, 2): 4.12}
    written, cost = {at(8, 0): 5.3}, {at(8, 0): 0.424}  # B$0.08/kWh then
    priced = hourly.costs(rows, written, cost, 0.10)
    assert priced == {at(8, 0): 0.424, at(8, 2): 0.412}


def test_sums_run_on_from_the_last_one():
    rows = {at(8, 1): 2.0, at(8, 0): 1.0}
    assert hourly.running_sums(rows, 3287.5) == [(at(8, 0), 3288.5), (at(8, 1), 3290.5)]
