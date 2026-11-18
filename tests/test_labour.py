from __future__ import annotations

from datetime import datetime

import pytest

from helpers import MONDAY_8, WORKDAY
from printfarm_planner.labour import Calendar, LabourError, OperatorPool, parse_weekly


def test_parse_weekday_range_and_single_day() -> None:
    weekly = parse_weekly({"mon-fri": "08:00-18:00", "sat": ["09:00-13:00"]})
    assert weekly[0] == [(480, 1080)]
    assert weekly[4] == [(480, 1080)]
    assert weekly[5] == [(540, 780)]
    assert weekly[6] == []


def test_parse_split_shift_wrapping_days_and_daily() -> None:
    weekly = parse_weekly({"fri-mon": "08:00-12:00, 13:00-17:00", "daily": "20:00-21:00"})
    # fri, sat, sun, mon get the split shift; everyone gets the evening slot
    assert weekly[4] == [(480, 720), (780, 1020), (1200, 1260)]
    assert weekly[0] == [(480, 720), (780, 1020), (1200, 1260)]
    assert weekly[2] == [(1200, 1260)]


def test_overnight_range_extends_past_midnight() -> None:
    assert parse_weekly({"mon": "22:00-06:00"})[0] == [(1320, 1800)]


def test_always() -> None:
    assert parse_weekly("24/7")[3] == [(0, 1440)]


@pytest.mark.parametrize(
    "spec",
    [
        "weekdays",
        {"monday": "08:00-18:00"},
        {"mon": "8-18"},
        {"mon": "08:60-18:00"},
        {"mon": "25:00-26:00"},
        {"mon": "24:00-01:00"},
        {"mon": "24:30-01:00"},
        {},
    ],
)
def test_bad_specs(spec: str | dict[str, str]) -> None:
    with pytest.raises(LabourError):
        parse_weekly(spec)


def test_calendar_rejects_empty_weekly() -> None:
    with pytest.raises(LabourError):
        Calendar(MONDAY_8, {d: [] for d in range(7)})


def test_earliest_fit_inside_and_across_days() -> None:
    cal = Calendar(MONDAY_8, parse_weekly(WORKDAY))
    assert cal.earliest_fit(0, 5) == 0
    # 17:58 + 5 min does not fit before 18:00 -> Tuesday 08:00 = 24 h after start
    assert cal.earliest_fit(598, 5) == 1440
    # 17:55 + 5 fits exactly
    assert cal.earliest_fit(595, 5) == 595
    # Saturday 10:00 -> Monday 08:00: from Monday 08:00 that is 7 days
    assert cal.earliest_fit(5 * 1440 + 120, 5) == 7 * 1440


def test_weekend_start() -> None:
    friday_5pm = datetime(2026, 11, 27, 17, 0)
    cal = Calendar(friday_5pm, parse_weekly(WORKDAY))
    assert cal.next_window(0) == (0, 60)
    # Fri 17:00 -> Mon 08:00 is 2 days 15 hours
    assert cal.earliest_fit(60, 5) == 2 * 1440 + 15 * 60


def test_overnight_window_from_previous_day_is_visible() -> None:
    tuesday_midnight = datetime(2026, 11, 24, 0, 0)
    cal = Calendar(tuesday_midnight, parse_weekly({"mon": "22:00-06:00"}))
    assert cal.next_window(0) == (0, 360)


def test_always_is_one_long_window() -> None:
    cal = Calendar(MONDAY_8, parse_weekly("24/7"))
    lo, hi = cal.next_window(0)
    assert lo == 0
    assert hi > 365 * 1440
    assert cal.earliest_fit(12345.5, 30) == 12345.5


def test_window_too_short_raises() -> None:
    cal = Calendar(MONDAY_8, parse_weekly({"mon": "08:00-08:03"}))
    with pytest.raises(LabourError, match="long enough"):
        cal.earliest_fit(0, 5)


def test_windows_between_clips() -> None:
    cal = Calendar(MONDAY_8, parse_weekly(WORKDAY))
    # Mon 12:00 .. Tue 10:00
    assert list(cal.windows_between(240, 1560)) == [(240, 600), (1440, 1560)]
    assert list(cal.windows_between(700, 1400)) == []


def test_to_datetime() -> None:
    cal = Calendar(MONDAY_8, parse_weekly(WORKDAY))
    assert cal.to_datetime(90) == datetime(2026, 11, 23, 9, 30)


def test_operator_pool_single_operator_serialises_tasks() -> None:
    pool = OperatorPool(Calendar(MONDAY_8, parse_weekly(WORKDAY)), 1)
    assert pool.earliest(0, 5) == (0, 0)
    pool.book(0, 0, 5)
    assert pool.earliest(0, 5) == (5, 0)
    pool.book(0, 5, 5)
    pool.book(0, 12, 5)
    # gap 10..12 is too short for 5 minutes -> after 17
    assert pool.earliest(0, 5) == (17, 0)
    # a 2 minute task fits in the gap
    assert pool.earliest(0, 2) == (10, 0)


def test_operator_pool_two_operators_run_in_parallel() -> None:
    pool = OperatorPool(Calendar(MONDAY_8, parse_weekly(WORKDAY)), 2)
    pool.book(0, 0, 5)
    assert pool.earliest(0, 5) == (0, 1)


def test_operator_pool_pushes_task_to_next_window() -> None:
    pool = OperatorPool(Calendar(MONDAY_8, parse_weekly(WORKDAY)), 1)
    pool.book(0, 590, 8)  # busy 17:50-17:58
    assert pool.earliest(590, 5) == (1440, 0)


def test_zero_duration_task_needs_no_operator() -> None:
    pool = OperatorPool(Calendar(MONDAY_8, parse_weekly(WORKDAY)), 1)
    pool.book(0, 0, 0)
    assert pool.bookings == [[]]
    assert pool.earliest(700, 0) == (1440, 0)


def test_operator_count_must_be_positive() -> None:
    with pytest.raises(LabourError):
        OperatorPool(Calendar(MONDAY_8, parse_weekly(WORKDAY)), 0)
