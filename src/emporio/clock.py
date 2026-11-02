"""Store clock and business hours in America/Campo_Grande (SPEC BR-11, DD-§7).

The current time can be pinned through ``EMPORIO_NOW`` so examples and evaluations are
reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

from emporio import config

TZ = ZoneInfo(config.TIMEZONE)

WEEKDAYS_PT = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)

# weekday() -> (opens, closes); Sunday is closed (manual §2).
_WEEKDAY_HOURS = (time(9), time(18))
_SATURDAY_HOURS = (time(9), time(13))

# Municipal holiday not covered by the `holidays` library (BR-11).
_MUNICIPAL_HOLIDAYS = {(8, 26): "Aniversário de Campo Grande"}

# Safety net for next_opening(); the longest real closure is a few days.
_MAX_DAYS_AHEAD = 30


def now() -> datetime:
    """Current time in Campo Grande. EMPORIO_NOW wins (naive values are read as local time)."""
    override = config.now_override()
    if override is None:
        return datetime.now(TZ)
    parsed = datetime.fromisoformat(override)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def today() -> date:
    return now().date()


@lru_cache(maxsize=8)
def _holidays_for(year: int) -> dict[date, str]:
    calendar = holidays.country_holidays("BR", subdiv="MS", years=year, language="pt_BR")
    result = dict(calendar.items())
    for (month, day), name in _MUNICIPAL_HOLIDAYS.items():
        result.setdefault(date(year, month, day), name)
    return result


def holiday_name(day: date) -> str | None:
    """Name of the national, MS state or Campo Grande holiday on ``day``, if any."""
    return _holidays_for(day.year).get(day)


def opening_hours(day: date) -> tuple[time, time] | None:
    """(opens, closes) on ``day``, or None when the store is closed all day."""
    if holiday_name(day) is not None:
        return None
    weekday = day.weekday()
    if weekday < 5:
        return _WEEKDAY_HOURS
    if weekday == 5:
        return _SATURDAY_HOURS
    return None


def is_open(at: datetime | None = None) -> bool:
    at = _local(at)
    hours = opening_hours(at.date())
    return hours is not None and hours[0] <= at.time() < hours[1]


def next_opening(at: datetime | None = None) -> datetime:
    """Start of the next opening window strictly after ``at`` (or ``at`` itself if open)."""
    at = _local(at)
    if is_open(at):
        return at
    for offset in range(_MAX_DAYS_AHEAD + 1):
        day = at.date() + timedelta(days=offset)
        hours = opening_hours(day)
        if hours is None:
            continue
        opens = datetime.combine(day, hours[0], tzinfo=TZ)
        if opens > at:
            return opens
    raise RuntimeError(f"no opening found within {_MAX_DAYS_AHEAD} days of {at.isoformat()}")


@dataclass(frozen=True)
class StoreStatus:
    now: datetime
    is_open: bool
    closes_at: datetime | None  # when open
    next_opening: datetime | None  # when closed
    holiday: str | None


def store_status(at: datetime | None = None) -> StoreStatus:
    at = _local(at)
    open_now = is_open(at)
    hours = opening_hours(at.date())
    return StoreStatus(
        now=at,
        is_open=open_now,
        closes_at=datetime.combine(at.date(), hours[1], tzinfo=TZ) if open_now and hours else None,
        next_opening=None if open_now else next_opening(at),
        holiday=holiday_name(at.date()),
    )


def describe_moment(moment: datetime) -> str:
    """``moment`` in customer-facing pt-BR: ``segunda-feira, 09/11 às 09:00``."""
    return f"{WEEKDAYS_PT[moment.weekday()]}, {moment:%d/%m} às {moment:%H:%M}"


def _local(at: datetime | None) -> datetime:
    if at is None:
        return now()
    if at.tzinfo is None:
        return at.replace(tzinfo=TZ)
    return at.astimezone(TZ)
