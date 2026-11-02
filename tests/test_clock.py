"""Business hours and holidays in Campo Grande (BR-11)."""

from datetime import datetime

import pytest

from emporio import clock

TZ = clock.TZ


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


@pytest.mark.parametrize(
    ("moment", "open_"),
    [
        ("2026-11-03T10:00", True),  # Tuesday
        ("2026-11-03T08:59", False),
        ("2026-11-03T18:00", False),  # closes at 18:00
        ("2026-11-07T12:59", True),  # Saturday morning
        ("2026-11-07T13:00", False),  # Saturday after 13h
        ("2026-11-08T10:00", False),  # Sunday
        ("2026-11-02T10:00", False),  # Finados
        ("2026-10-11T10:00", False),  # MS state holiday (Sunday anyway)
        ("2026-08-26T10:00", False),  # Campo Grande anniversary (Wednesday)
    ],
)
def test_is_open(moment, open_):
    assert clock.is_open(at(moment)) is open_


def test_next_opening_saturday_afternoon_is_monday_morning():
    assert clock.next_opening(at("2026-11-07T15:00")) == at("2026-11-09T09:00")


def test_next_opening_skips_holiday():
    # Sunday 01/11 -> Monday 02/11 is Finados -> Tuesday 03/11.
    assert clock.next_opening(at("2026-11-01T10:00")) == at("2026-11-03T09:00")


def test_next_opening_before_hours_is_same_day():
    assert clock.next_opening(at("2026-11-03T07:30")) == at("2026-11-03T09:00")


def test_next_opening_when_open_is_now():
    moment = at("2026-11-03T10:00")
    assert clock.next_opening(moment) == moment


def test_store_status_and_description(monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-07T15:00:00")
    status = clock.store_status()
    assert not status.is_open and status.closes_at is None
    assert clock.describe_moment(status.next_opening) == "segunda-feira, 09/11 às 09:00"

    open_status = clock.store_status(at("2026-11-03T10:00"))
    assert open_status.is_open and open_status.closes_at == at("2026-11-03T18:00")
    assert clock.holiday_name(at("2026-11-02T10:00").date()) == "Finados"
