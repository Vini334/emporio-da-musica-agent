"""Clock, text normalization and pt-BR formatting."""

from datetime import date, datetime

from emporio import clock
from emporio.formatting import brl, date_br
from emporio.text import digits, normalize


def test_clock_override_naive_is_local(monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-07T15:00:00")
    now = clock.now()
    assert now.hour == 15
    assert now.utcoffset().total_seconds() == -4 * 3600
    assert clock.today() == date(2026, 11, 7)


def test_clock_override_aware_is_converted(monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2026-11-07T19:00:00+00:00")
    assert clock.now().hour == 15


def test_clock_without_override_uses_store_timezone(monkeypatch):
    monkeypatch.delenv("EMPORIO_NOW", raising=False)
    now = clock.now()
    assert isinstance(now, datetime) and str(now.tzinfo) == "America/Campo_Grande"


def test_normalize():
    assert normalize("  Violão   Elétrico AÇO ") == "violao eletrico aco"
    assert digits("(67) 99812-3456") == "67998123456"


def test_brl():
    assert brl(1967.18) == "R$ 1.967,18"
    assert brl(0.5) == "R$ 0,50"
    assert brl(11499) == "R$ 11.499,00"
    assert brl(-35) == "-R$ 35,00"


def test_date_br():
    assert date_br(date(2026, 2, 28)) == "28/02/2026"
