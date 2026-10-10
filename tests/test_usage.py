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


# ----- electricity tariff -----


def test_tariff_matches_the_published_totals():
    # Cumulative charges from https://www.des.gov.bn/electricity-tariff/
    for kwh, charge in [(600, 6.00), (650, 10.00), (1000, 38.00), (2000, 118.00),
                        (2050, 123.00), (4000, 318.00), (4100, 330.00), (5500, 498.00)]:
        assert round(usage.tariff_cost(kwh), 2) == charge


def test_marginal_rate():
    assert usage.marginal_rate(0) == 0.01
    assert usage.marginal_rate(600) == 0.08
    assert usage.marginal_rate(1999.9) == 0.08
    assert usage.marginal_rate(2000) == 0.10
    assert usage.marginal_rate(9000) == 0.12


def _month(days: dict[int, float], month: int = 10) -> dict[str, float]:
    return {f"2026-{month:02d}-{d:02d}": v for d, v in days.items()}


def test_tariff_balance_matches_a_fresh_login():
    # 00:16 sign-in: 224.17 BND, about 1,000 kWh into October (0.08 band).
    before = _month({1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0, 5: 100.0, 6: 100.0,
                     7: 108.573, 8: 105.547, 9: 96.426, 10: 0.0})
    anchor = usage.make_anchor(2472.175, 224.17, TODAY, before, False, start=dt.date(2026, 10, 1))
    start, _ = usage.window_start(anchor, TODAY)
    now = {**before, "2026-10-10": 36.99}

    current = usage.current_readings(anchor, now, start, rate=None, tariff=usage.ELECTRIC_TARIFF)

    assert current["remaining_balance"] == 221.21  # what the 11:01 sign-in showed
    assert current["balance_method"] == "tariff"
    assert current["rate"] == 0.08
    assert current["month_to_date"] == 947.536


def test_tariff_charges_each_band_when_crossing_2000():
    before = _month({1: 1990.0, 10: 0.0})
    anchor = usage.make_anchor(1000.0, 100.0, TODAY, before, False, start=dt.date(2026, 10, 1))
    start, _ = usage.window_start(anchor, TODAY)

    current = usage.current_readings(
        anchor, {**before, "2026-10-10": 20.0}, start, rate=None, tariff=usage.ELECTRIC_TARIFF
    )

    assert current["remaining_balance"] == 98.2  # 10 kWh at 0.08 + 10 kWh at 0.10
    assert current["rate"] == 0.10


def test_tariff_restarts_on_the_first_of_the_month():
    signed_in = dt.date(2026, 10, 30)
    before = _month({1: 1890.0, 30: 60.0})  # 1,950 kWh so far in October
    anchor = usage.make_anchor(1000.0, 100.0, signed_in, before, False, start=dt.date(2026, 10, 1))
    later = dt.date(2026, 11, 1)
    start, trimmed = usage.window_start(anchor, later)
    now = {**before, "2026-10-31": 60.0, "2026-11-01": 100.0}

    current = usage.current_readings(anchor, now, start, rate=None, tariff=usage.ELECTRIC_TARIFF)

    assert not trimmed
    # October: 1,950 -> 2,010 is 50 at 0.08 + 10 at 0.10 = 5.00.
    # November: 0 -> 100 at 0.01 = 1.00.
    assert current["remaining_balance"] == 94.0
    assert current["used_since_sign_in"] == 160.0
    assert current["month_to_date"] == 100.0
    assert current["rate"] == 0.01


def test_older_anchor_counts_only_days_it_covered():
    # A 1.3.x anchor: no "from", and usage only for the last three days. The
    # month's earlier days come back in the reports now but aren't new usage.
    anchor = usage.make_anchor(1000.0, 100.0, TODAY, _month({7: 10.0, 8: 10.0, 9: 10.0, 10: 0.0}), False)
    del anchor["from"]
    start, _ = usage.window_start(anchor, TODAY)
    assert start == dt.date(2026, 10, 1)
    now = _month({1: 300.0, 2: 300.0, 3: 300.0, 4: 300.0, 5: 300.0, 6: 300.0,
                  7: 10.0, 8: 10.0, 9: 10.0, 10: 5.0})

    current = usage.current_readings(anchor, now, start, rate=None, tariff=usage.ELECTRIC_TARIFF)

    assert current["used_since_sign_in"] == 5.0
    assert current["month_to_date"] == 1835.0
    assert current["remaining_balance"] == 99.6  # 5 kWh at 0.08


def test_new_session_window_reaches_the_first_of_the_month():
    assert usage.new_session_start(TODAY) == dt.date(2026, 10, 1)
    assert usage.new_session_start(dt.date(2026, 10, 2)) == dt.date(2026, 9, 29)
