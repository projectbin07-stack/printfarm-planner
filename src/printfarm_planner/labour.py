"""Labour windows (when someone is there to change plates) and operator booking.

All times inside the planner are minutes from the plan start. Wall-clock
times are naive local datetimes; daylight-saving transitions are not modelled.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta

__all__ = [
    "ALWAYS",
    "Calendar",
    "LabourError",
    "OperatorPool",
    "Weekly",
    "parse_weekly",
]

#: Weekly schedule: weekday (Monday = 0) -> list of (start, end) minutes after
#: that day's midnight. ``end`` may exceed 1440 for windows that run past midnight.
Weekly = dict[int, list[tuple[int, int]]]

ALWAYS = "24/7"
_DAY_MIN = 1440
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_CHUNK_DAYS = 28
_MAX_DAYS = 3660
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


class LabourError(ValueError):
    """Invalid labour configuration, or no window long enough for a task."""


def _parse_clock(text: str) -> int:
    match = _TIME.match(text.strip())
    if match is None:
        raise LabourError(f"bad time {text!r}; expected HH:MM")
    hours, minutes = int(match.group(1)), int(match.group(2))
    if minutes > 59 or hours > 24 or (hours == 24 and minutes):
        raise LabourError(f"bad time {text!r}")
    return hours * 60 + minutes


def _parse_days(spec: str) -> list[int]:
    days: list[int] = []
    for part in spec.lower().replace(" ", "").split(","):
        if part in ("daily", "all", "everyday"):
            days.extend(range(7))
            continue
        first, sep, last = part.partition("-")
        if first not in _DAYS or (sep and last not in _DAYS):
            raise LabourError(f"bad day spec {spec!r}; use e.g. mon, mon-fri, sat,sun, daily")
        a = _DAYS.index(first)
        b = _DAYS.index(last) if sep else a
        span = (b - a) % 7
        days.extend((a + k) % 7 for k in range(span + 1))
    return days


def _parse_ranges(value: str | Sequence[str]) -> list[tuple[int, int]]:
    items = value.split(",") if isinstance(value, str) else list(value)
    ranges: list[tuple[int, int]] = []
    for item in items:
        start_text, sep, end_text = item.partition("-")
        if not sep:
            raise LabourError(f"bad time range {item!r}; expected HH:MM-HH:MM")
        start, end = _parse_clock(start_text), _parse_clock(end_text)
        if start >= _DAY_MIN:
            raise LabourError(f"range {item!r} starts at 24:00")
        if end <= start:
            end += _DAY_MIN  # runs past midnight
        ranges.append((start, end))
    return ranges


def parse_weekly(spec: str | Mapping[str, str | Sequence[str]]) -> Weekly:
    """Parse ``"24/7"`` or ``{"mon-fri": "08:00-18:00", "sat": ["09:00-13:00"]}``."""
    if isinstance(spec, str):
        if spec.strip().lower() != ALWAYS:
            raise LabourError(f"bad windows value {spec!r}; use {ALWAYS!r} or a mapping")
        return {day: [(0, _DAY_MIN)] for day in range(7)}
    weekly: Weekly = {day: [] for day in range(7)}
    for day_spec, ranges in spec.items():
        parsed = _parse_ranges(ranges)
        for day in _parse_days(day_spec):
            weekly[day].extend(parsed)
    if not any(weekly.values()):
        raise LabourError("labour windows are empty")
    return weekly


class Calendar:
    """Merged labour windows on a minutes-from-start axis, generated lazily."""

    def __init__(self, start: datetime, weekly: Weekly) -> None:
        if not any(weekly.values()):
            raise LabourError("labour windows are empty")
        self.start = start
        self.weekly = weekly
        self._windows: list[tuple[float, float]] = []
        self._days = 0
        self._midnight = datetime(start.year, start.month, start.day)
        self._offset = (start - self._midnight).total_seconds() / 60.0
        self._extend()

    @property
    def covered_until(self) -> float:
        return self._days * _DAY_MIN - self._offset

    def _extend(self) -> bool:
        if self._days >= _MAX_DAYS:
            return False
        first = self._days - 1 if self._days == 0 else self._days
        raw = list(self._windows)
        for day in range(first, self._days + _CHUNK_DAYS):
            date = self._midnight + timedelta(days=day)
            base = day * _DAY_MIN - self._offset
            for a, b in self.weekly.get(date.weekday(), []):
                lo, hi = base + a, base + b
                if hi > 0:
                    raw.append((max(lo, 0.0), hi))
        self._days += _CHUNK_DAYS
        raw.sort()
        merged: list[tuple[float, float]] = []
        for lo, hi in raw:
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        self._windows = merged
        return True

    def next_window(self, t: float) -> tuple[float, float]:
        """The first window whose end is after ``t`` (it may already have started)."""
        while True:
            index = bisect.bisect_right(self._windows, t, key=lambda w: w[1])
            if index < len(self._windows):
                window = self._windows[index]
                # A window touching the generated horizon may continue beyond it.
                if window[1] < self.covered_until or not self._extend():
                    return window
                continue
            if not self._extend():
                raise LabourError(f"no labour window within {_MAX_DAYS} days of the plan start")

    def earliest_fit(self, t: float, duration: float) -> float:
        """Earliest ``s >= t`` with ``[s, s + duration]`` inside one window."""
        s = t
        while True:
            lo, hi = self.next_window(s)
            s = max(s, lo)
            if s + duration <= hi:
                return s
            if hi - lo < duration and self._longest() < duration:
                raise LabourError(f"no labour window is long enough for a {duration:g} minute task")
            s = hi

    def _longest(self) -> float:
        return max(hi - lo for lo, hi in self._windows)

    def windows_between(self, a: float, b: float) -> Iterator[tuple[float, float]]:
        """Windows overlapping ``[a, b]``, clipped to it."""
        s = a
        while s < b:
            try:
                lo, hi = self.next_window(s)
            except LabourError:
                return
            if lo >= b:
                return
            yield (max(lo, a), min(hi, b))
            s = hi

    def to_datetime(self, minutes: float) -> datetime:
        return self.start + timedelta(minutes=minutes)


class OperatorPool:
    """``count`` interchangeable operators; each does one task at a time."""

    def __init__(self, calendar: Calendar, count: int = 1) -> None:
        if count < 1:
            raise LabourError("operators must be at least 1")
        self.calendar = calendar
        self.bookings: list[list[tuple[float, float]]] = [[] for _ in range(count)]

    def earliest(self, t: float, duration: float) -> tuple[float, int]:
        """Earliest start ``>= t`` inside a window with an operator free; (start, operator)."""
        s = t
        while True:
            s = self.calendar.earliest_fit(s, duration)
            if duration <= 0:
                return s, 0
            pushes: list[float] = []
            for index, booked in enumerate(self.bookings):
                clash = next((e for b, e in booked if b < s + duration and e > s), None)
                if clash is None:
                    return s, index
                pushes.append(clash)
            s = min(pushes)

    def book(self, operator: int, start: float, duration: float) -> None:
        if duration > 0:
            bisect.insort(self.bookings[operator], (start, start + duration))

    def copy(self) -> OperatorPool:
        """Independent bookings on the same calendar."""
        other = OperatorPool(self.calendar, len(self.bookings))
        other.bookings = [list(booked) for booked in self.bookings]
        return other
