"""Store clock in America/Campo_Grande, overridable through EMPORIO_NOW (DD-§7)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from emporio import config

TZ = ZoneInfo(config.TIMEZONE)


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
