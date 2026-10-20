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


def test_balance_follows_the_top_up_prices():
    # 00:16 sign-in: 224.17 BND for 2,472 kWh, so the oldest units cost 0.08.
    before = _month({1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0, 5: 100.0, 6: 100.0,
                     7: 108.573, 8: 105.547, 9: 96.426, 10: 0.0})
    anchor = usage.make_anchor(2472.175, 224.17, TODAY, before, False, start=dt.date(2026, 10, 1))
    start, _ = usage.window_start(anchor, TODAY)
    now = {**before, "2026-10-10": 36.99}

    current = usage.current_readings(anchor, now, start, rate=None, tariff=usage.ELECTRIC_TARIFF)

    assert current["remaining_balance"] == 221.21  # what the 11:01 sign-in showed
    assert current["balance_method"] == "top-up prices"
    assert current["rate"] == 0.08
    assert current["month_to_date"] == 947.536


def test_units_are_used_oldest_first():
    # Real readings: 18 Oct 00:53 sign-in 1578.857 kWh / 152.70 BND, about
    # 259 kWh of them bought at 0.08 and the rest at 0.10. The next sign-in
    # (19 Oct 05:50) showed 141.24 after 143.307 kWh: all still at 0.08.
    signed_in = dt.date(2026, 10, 18)
    before = _month({1: 1881.042, 18: 0.0})
    anchor = usage.make_anchor(1578.857, 152.70, signed_in, before, False, start=dt.date(2026, 10, 1))
    start, _ = usage.window_start(anchor, dt.date(2026, 10, 19))

    current = usage.current_readings(
        anchor, {**before, "2026-10-18": 118.168, "2026-10-19": 25.139}, start,
        rate=None, tariff=usage.ELECTRIC_TARIFF,
    )

    assert current["remaining_balance"] == 141.24
    assert current["rate"] == 0.08
    assert current["price_layers"] == [[115.978, 0.08], [1319.572, 0.1]]

    # Another 131.6 kWh uses the last 116 kWh at 0.08, then 0.10: what the
    # 20 Oct 10:50 sign-in showed.
    later = usage.current_readings(
        anchor, {**before, "2026-10-18": 118.168, "2026-10-19": 156.739}, start,
        rate=None, tariff=usage.ELECTRIC_TARIFF,
    )
    assert later["remaining_balance"] == 130.39
    assert later["rate"] == 0.1


def test_prices_dont_restart_on_the_first_of_the_month():
    # Units bought in October still cost what they cost in November.
    signed_in = dt.date(2026, 10, 30)
    before = _month({1: 1890.0, 30: 60.0})
    anchor = usage.make_anchor(1000.0, 100.0, signed_in, before, False, start=dt.date(2026, 10, 1))
    later = dt.date(2026, 11, 1)
    start, trimmed = usage.window_start(anchor, later)
    now = {**before, "2026-10-31": 60.0, "2026-11-01": 100.0}

    current = usage.current_readings(anchor, now, start, rate=None, tariff=usage.ELECTRIC_TARIFF)

    assert not trimmed
    assert current["used_since_sign_in"] == 160.0
    assert current["remaining_balance"] == 84.0  # 160 kWh at 0.10
    assert current["month_to_date"] == 100.0
    assert current["rate"] == 0.1


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
    assert current["remaining_balance"] == 99.5  # 5 kWh at 0.10 (100 BND / 1000 kWh)


# ----- top-ups -----

OCTOBER = {"month": "2026-10", "units": 0.0}


def test_layers_are_inferred_from_the_balance():
    assert usage.infer_layers(1578.857, 152.70, usage.ELECTRIC_TARIFF) == [[259.285, 0.08], [1319.572, 0.1]]
    assert usage.infer_layers(1000.0, 100.0, usage.ELECTRIC_TARIFF) == [[1000.0, 0.1]]
    # Water after the 10 Oct top-up: about 69 m3 at 0.11, then 54.5 at 0.44.
    assert usage.infer_layers(123.58, 31.59, usage.WATER_TARIFF) == [[69.046, 0.11], [54.534, 0.44]]
    # Below the cheapest band (other charges): one layer at the ratio.
    assert usage.infer_layers(27.667, 3.04, usage.WATER_TARIFF) == [[27.667, 0.10988]]
    assert usage.infer_layers(0.0, 0.0, usage.ELECTRIC_TARIFF) == []


def test_electric_top_up_is_priced_by_the_month_so_far():
    # Real: 1 Oct, B$249.61 bought 3316.13 kWh (600 at 0.01, 1400 at 0.08, the rest at 0.10).
    previous = {"units": 105.658, "balance": 10.56, "layers": [[105.658, 0.1]]}

    layers, top_ups, amount = usage.settle_layers(
        previous, 3421.788, 260.17, 0.0, usage.ELECTRIC_TARIFF, OCTOBER
    )

    assert amount == 249.61
    assert layers == [[105.658, 0.1], [600.0, 0.01], [1400, 0.08], [1316.13, 0.1]]
    assert top_ups == {"month": "2026-10", "units": 3316.13}
    assert usage.top_up_plan(top_ups, usage.ELECTRIC_TARIFF) == {
        "topped_up_this_month": 249.61,
        "bought_this_month": 3316.13,
        "top_up_price": 0.1,
        "top_up_left_at_price": 68.39,  # up to 4,000 kWh
    }


def test_water_top_up_past_the_first_band_costs_0_44():
    # Real: 10 Oct, B$30 bought 109.087 m3 (54.54 at 0.11, 54.547 at 0.44).
    previous = {"units": 27.667, "balance": 3.04, "layers": [[27.667, 0.10988]]}

    layers, top_ups, amount = usage.settle_layers(
        previous, 136.754, 33.04, 0.0, usage.WATER_TARIFF, OCTOBER
    )

    assert amount == 30.0
    assert layers[1:] == [[54.54, 0.11], [54.547, 0.44]]
    plan = usage.top_up_plan(top_ups, usage.WATER_TARIFF)
    assert plan["top_up_price"] == 0.44
    assert plan["top_up_left_at_price"] is None  # the last band


def test_usage_between_sign_ins_comes_off_the_front():
    previous = {"units": 1578.857, "balance": 152.70, "layers": [[259.285, 0.08], [1319.572, 0.1]]}

    layers, top_ups, amount = usage.settle_layers(
        previous, 1435.55, 141.24, 143.0, usage.ELECTRIC_TARIFF, OCTOBER
    )

    assert amount is None
    assert top_ups == OCTOBER
    # The meter's own drop (143.307) is used, not the reports' 143.0.
    assert layers == [[115.978, 0.08], [1319.572, 0.1]]


def test_layers_that_dont_match_the_balance_are_inferred_again():
    previous = {"units": 1000.0, "balance": 100.0, "layers": [[1000.0, 0.1]]}

    layers, _, _ = usage.settle_layers(previous, 900.0, 80.0, 100.0, usage.ELECTRIC_TARIFF, OCTOBER)

    assert layers == usage.infer_layers(900.0, 80.0, usage.ELECTRIC_TARIFF)


def test_anchor_from_before_1_8_still_sees_a_top_up():
    previous = {"units": 105.658, "balance": 10.56}

    _, top_ups, amount = usage.settle_layers(
        previous, 3421.788, 260.17, 0.0, usage.ELECTRIC_TARIFF, OCTOBER
    )

    assert amount == 249.61
    assert top_ups["units"] == 3316.13


def test_earlier_top_ups_are_worked_out_from_the_price():
    # B$20 for 250 kWh: only possible at 0.08, so at least 600 kWh were bought before.
    before, layers = usage.price_top_up(250.0, 20.0, usage.ELECTRIC_TARIFF, None)
    assert 599 < before <= 600
    assert sum(u for u, r in layers if r == 0.08) > 249
    # A known count that explains the price is kept ("B$20 after B$30 earlier").
    assert usage.price_top_up(250.0, 20.0, usage.ELECTRIC_TARIFF, 900.0) == (900.0, [[250.0, 0.08]])
    # A first top-up of the month starts at the cheapest band.
    assert usage.price_top_up(775.0, 20.0, usage.ELECTRIC_TARIFF, None)[0] == 0.0


def test_month_top_ups_restart_on_the_first():
    assert usage.month_top_ups(None, TODAY) == {"month": "2026-10", "units": None}
    assert usage.month_top_ups({"month": "2026-10", "units": 50.0}, TODAY) == {"month": "2026-10", "units": 50.0}
    assert usage.month_top_ups({"month": "2026-10", "units": 50.0}, dt.date(2026, 11, 1)) == {
        "month": "2026-11", "units": 0.0,
    }


def test_top_up_plan_unknown_until_a_top_up_or_the_1st():
    plan = usage.top_up_plan({"month": "2026-10", "units": None}, usage.ELECTRIC_TARIFF)
    assert plan == {"topped_up_this_month": None, "top_up_price": None, "top_up_left_at_price": None}
    fresh = usage.top_up_plan({"month": "2026-11", "units": 0.0}, usage.ELECTRIC_TARIFF)
    assert fresh["top_up_price"] == 0.01
    assert fresh["top_up_left_at_price"] == 6.0  # 600 kWh at 0.01


def test_new_session_window_reaches_the_first_of_the_month():
    assert usage.new_session_start(TODAY) == dt.date(2026, 10, 1)
    assert usage.new_session_start(dt.date(2026, 10, 2)) == dt.date(2026, 9, 29)


# ----- water tariff -----


def test_water_tariff_bands():
    assert round(usage.tariff_cost(54.54, usage.WATER_TARIFF), 4) == 5.9994
    assert round(usage.tariff_cost(64.54, usage.WATER_TARIFF), 4) == 10.3994  # +10 m3 at 0.44
    assert usage.marginal_rate(10, usage.WATER_TARIFF) == 0.11
    assert usage.marginal_rate(60, usage.WATER_TARIFF) == 0.44


def test_water_large_top_up_costs_more_once_cheap_units_run_out():
    # Real 20 Oct: 123.58 m3 for 31.59 BND, 69 m3 at 0.11 then 54.5 at 0.44.
    before = _month({1: 20.0})
    anchor = usage.make_anchor(123.58, 31.59, TODAY, before, False, start=dt.date(2026, 10, 1))
    start, _ = usage.window_start(anchor, TODAY)

    current = usage.current_readings(
        anchor, {**before, "2026-10-09": 70.0}, start, rate=None, tariff=usage.WATER_TARIFF
    )

    # 69.046 m3 at 0.11 + 0.954 m3 at 0.44 = 8.015
    assert current["remaining_balance"] == 23.58
    assert current["rate"] == 0.44


def test_recent_usage_keeps_the_lookback_window():
    now = {**PAST_DAYS, "2026-10-06": 99.0, "2026-10-10": 39.952}
    assert usage.recent_usage(now, TODAY) == {**PAST_DAYS, "2026-10-10": 39.952}


def test_usage_changed_when_a_day_grows():
    before = {**PAST_DAYS, "2026-10-10": 39.952}
    assert usage.usage_changed(before, {**PAST_DAYS, "2026-10-10": 43.1})


def test_usage_unchanged_when_usms_posts_nothing():
    before = {**PAST_DAYS, "2026-10-10": 39.952}
    assert not usage.usage_changed(before, dict(before))


def test_usage_changed_when_a_new_day_has_usage():
    before = {"2026-10-09": 1.9}
    assert usage.usage_changed(before, {"2026-10-09": 1.9, "2026-10-10": 0.4})
    # A new day with nothing in it yet (just after midnight) isn't new data.
    assert not usage.usage_changed(before, {"2026-10-09": 1.9, "2026-10-10": 0.0})


def test_usage_changed_ignores_days_leaving_the_window():
    before = {"2026-10-06": 99.0, **PAST_DAYS}
    assert not usage.usage_changed(before, dict(PAST_DAYS))


def test_usage_changed_needs_a_previous_poll():
    assert not usage.usage_changed(None, PAST_DAYS)


def test_last_estimate_keeps_the_report_based_readings():
    # 11 Oct 00:55: a report failed and the login snapshot (2399.519) replaced
    # the last estimate (2378.41), so the units jumped back up.
    previous = {
        "status": "ACTIVE",
        "remaining_unit": 2378.41,
        "remaining_balance": 217.8,
        "used_since_sign_in": 21.109,
        "sign_in_units": 2399.519,
        "usage_updated": "2026-10-10T23:55:37+08:00",
        "live": True,
    }
    kept = usage.last_estimate(previous)
    assert kept["remaining_unit"] == 2378.41
    assert kept["remaining_balance"] == 217.8
    assert kept["usage_updated"] == "2026-10-10T23:55:37+08:00"
    assert "status" not in kept and "live" not in kept


def test_last_estimate_carries_over_repeated_failures():
    reused = {"remaining_unit": 2378.41, "used_since_sign_in": 21.109, "live": False}
    assert usage.last_estimate(reused)["remaining_unit"] == 2378.41


def test_last_estimate_needs_a_report_based_previous_poll():
    assert usage.last_estimate(None) == {}
    # Only the login snapshot (e.g. the first poll after a restart).
    assert usage.last_estimate({"remaining_unit": 2399.519, "live": False}) == {}


BRUNEI = dt.timezone(dt.timedelta(hours=8))


def test_last_posted_is_after_the_latest_day_with_usage():
    # Water on 12 Oct: 9 Oct posted, 10 Oct still 0, 11 Oct missing.
    now = dt.datetime(2026, 10, 12, 20, 30, tzinfo=BRUNEI)
    posted = usage.last_posted({"2026-10-09": 0.876, "2026-10-10": 0.0}, now)
    assert posted == "2026-10-10T00:00:00+08:00"


def test_last_posted_is_now_with_usage_today():
    now = dt.datetime(2026, 10, 12, 20, 30, tzinfo=BRUNEI)
    assert usage.last_posted({"2026-10-11": 126.1, "2026-10-12": 95.8}, now) == now.isoformat()


def test_last_posted_unknown_without_usage():
    now = dt.datetime(2026, 10, 12, 20, 30, tzinfo=BRUNEI)
    assert usage.last_posted({"2026-10-12": 0.0}, now) is None
    assert usage.last_posted({}, now) is None
