"""Tests for keeping readings current from the usage reports.

The numbers are real USMS readings from 2026-10-10: the Home cards froze
at the 00:16 login snapshot, and a new login at 11:19 showed the truth.
"""

import datetime as dt

from uzmz import usage

TODAY = dt.date(2026, 10, 10)
PAST_DAYS = {"2026-10-07": 108.573, "2026-10-08": 105.547, "2026-10-09": 96.426}


def test_electric_matches_a_fresh_login():
    # 00:16 login: no hours reported for today yet.
    anchor = usage.make_anchor(2472.175, 224.17, TODAY, {**PAST_DAYS, "2026-10-10": 0.0}, False)
    start, trimmed = usage.window_start(anchor, TODAY)
    # 11:19: hours 1-11 reported, 39.952 kWh in total.
    now = {**PAST_DAYS, "2026-10-10": 39.952}

    current = usage.current_readings(anchor, now, start, rate=0.08)

    assert not trimmed
    assert current["used_since_sign_in"] == 39.952
    assert current["remaining_unit"] == 2432.223  # what the 11:19 login showed
    assert current["remaining_balance"] == 220.97  # likewise
    assert current["rate_is_average"] is False


def test_late_posted_days_count_as_usage():
    # Water's daily figures arrive a day or more late.
    anchor = usage.make_anchor(27.667, 3.04, TODAY, {"2026-10-07": 1.451, "2026-10-08": 2.134}, False)
    start, _ = usage.window_start(anchor, TODAY)
    now = {"2026-10-07": 1.451, "2026-10-08": 2.134, "2026-10-09": 1.9}

    current = usage.current_readings(anchor, now, start, rate=0.11)

    assert current["used_since_sign_in"] == 1.9
    assert current["remaining_unit"] == 25.767


def test_usage_is_measured_across_days():
    anchor = usage.make_anchor(2472.175, 224.17, TODAY, {**PAST_DAYS, "2026-10-10": 10.0}, False)
    later = TODAY + dt.timedelta(days=2)
    start, _ = usage.window_start(anchor, later)
    now = {**PAST_DAYS, "2026-10-10": 100.0, "2026-10-11": 95.0, "2026-10-12": 5.0}

    current = usage.current_readings(anchor, now, start, rate=0.08)

    assert current["used_since_sign_in"] == 190.0  # 90 + 95 + 5


def test_rate_learned_between_logins():
    previous = usage.make_anchor(2472.175, 224.17, TODAY, {}, False)
    assert usage.learn_rate(previous, 2435.185, 221.21, None) == 0.08002


def test_top_up_keeps_previous_rate():
    previous = usage.make_anchor(100.0, 9.0, TODAY, {}, False)
    assert usage.learn_rate(previous, 1200.0, 109.0, 0.08) == 0.08
    assert usage.learn_rate(None, 1200.0, 109.0, 0.08) == 0.08


def test_without_a_rate_the_average_is_used_and_flagged():
    anchor = usage.make_anchor(2000.0, 200.0, TODAY, {"2026-10-10": 0.0}, False)
    start, _ = usage.window_start(anchor, TODAY)

    current = usage.current_readings(anchor, {"2026-10-10": 10.0}, start, rate=None)

    assert current["rate_is_average"] is True
    assert current["rate"] == 0.1
    assert current["remaining_balance"] == 199.0


def test_long_sessions_are_capped_to_the_report_range():
    anchor = usage.make_anchor(2000.0, 200.0, TODAY, {}, False)
    start, trimmed = usage.window_start(anchor, TODAY + dt.timedelta(days=40))
    assert trimmed
    assert start == TODAY + dt.timedelta(days=10)


def test_never_negative():
    anchor = usage.make_anchor(5.0, 0.5, TODAY, {"2026-10-10": 0.0}, False)
    start, _ = usage.window_start(anchor, TODAY)
    current = usage.current_readings(anchor, {"2026-10-10": 50.0}, start, rate=0.08)
    assert current["remaining_unit"] == 0.0
    assert current["remaining_balance"] == 0.0


def test_parse_number():
    assert usage.parse_number("2432.223 kWh") == 2432.223
    assert usage.parse_number("$220.97") == 220.97
    assert usage.parse_number("") is None
