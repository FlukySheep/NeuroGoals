"""Course catalog: courses with prices, payment methods and cohorts.

Hidden behind CATALOG_ENABLED. The live НейроФинансы flow keeps using texts.py until
the switch is turned on; the catalog starts with НейроФинансы seeded from the same data.
"""

import re

from . import schedule
from . import texts as t
from .config import Config
from .db import Database

STATUSES = {
    "draft": "Черновик (скрыт)",
    "waitlist": "Лист ожидания",
    "open": "Открыта продажа",
    "closed": "Набор закрыт",
}
KINDS = {
    "cohort": "Группы с датами",
    "open": "Всегда открыт (без дат)",
}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
SEED_FLAG = "catalog:seeded"
SCHEDULE_FLAG = "catalog:schedule_v1"
NEUROFINANCE = "neurofinance"

# НейроФинансы sessions as real dates (Moscow time; 3–4 h, ending by 16:00).
NEUROFINANCE_SESSIONS = {
    "weekend": ["2026-10-10", "2026-10-11", "2026-10-17", "2026-10-18"],
    "weekday": ["2026-10-13", "2026-10-15", "2026-10-20", "2026-10-22"],
}
NEUROFINANCE_NOTE = "3–4 часа с перерывом"


def schedule_fields(sessions: list, tz: str, extra_tz: str | None, note: str) -> dict:
    """Cohort columns for a structured schedule, including the generated texts."""
    dates, time_text = schedule.render(sessions, tz, extra_tz, note)
    return {"sessions": schedule.dump_sessions(sessions), "timezone": tz, "extra_timezone": extra_tz or "",
            "time_note": note, "dates": dates, "time_text": time_text}


def _neurofinance_schedule(key: str) -> dict:
    days = NEUROFINANCE_SESSIONS[key]
    sessions = schedule.parse_sessions(days, ["12:00"] * len(days), ["16:00"] * len(days))
    return schedule_fields(sessions, schedule.DEFAULT_TZ, schedule.DEFAULT_EXTRA_TZ, NEUROFINANCE_NOTE)


def methods_of(course: dict) -> list[str]:
    keys = [k for k in (course.get("methods") or "").split(",") if k in t.PAYMENT_METHODS]
    return keys


def price_line(course: dict) -> str:
    parts = []
    if course.get("price_eur"):
        parts.append(f"{course['price_eur']} €")
    if course.get("price_rub"):
        parts.append(f"{course['price_rub']:,} ₽".replace(",", " "))
    return " / ".join(parts) or "—"


async def ensure_seeded(db: Database, config: Config) -> None:
    """Add НейроФинансы as the first course once (deleting it later is respected)."""
    if await db.kv_get(SEED_FLAG):
        await upgrade_schedules(db)
        return
    if await db.course_get(NEUROFINANCE) is None:
        await db.course_save(
            NEUROFINANCE,
            title="НейроФинансы",
            summary="Четырёхдневная практическая программа Зои Зарайской о финансовом сценарии, "
                    "скрытых денежных правилах и реальных изменениях в поведении.",
            status="open",
            kind="cohort",
            price_eur=t.PRICE_EUR,
            price_rub=t.PRICE_RUB,
            methods=",".join(t.PAYMENT_METHODS),
            sort_order=0,
        )
        for order, group in enumerate(t.GROUPS.values()):
            # Cohort ids match users.group_key, so existing students map onto them.
            await db.cohort_save(
                group.key,
                course_id=NEUROFINANCE,
                title=group.title,
                group_link=config.group_links.get(group.key, ""),
                sort_order=order,
                **_neurofinance_schedule(group.key),
            )
        await db.kv_set(SCHEDULE_FLAG, "1")
    await db.kv_set(SEED_FLAG, "1")


async def upgrade_schedules(db: Database) -> None:
    """Catalogs seeded before structured schedules: give НейроФинансы its real sessions once."""
    if await db.kv_get(SCHEDULE_FLAG):
        return
    for key in NEUROFINANCE_SESSIONS:
        cohort = await db.cohort_get(key)
        if cohort and cohort["course_id"] == NEUROFINANCE and not cohort.get("sessions"):
            await db.cohort_save(key, **_neurofinance_schedule(key))
    await db.kv_set(SCHEDULE_FLAG, "1")
