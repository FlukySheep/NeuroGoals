"""Course catalog: courses with prices, payment methods and cohorts.

Hidden behind CATALOG_ENABLED. The live НейроФинансы flow keeps using texts.py until
the switch is turned on; the catalog starts with НейроФинансы seeded from the same data.
"""

import re

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
NEUROFINANCE = "neurofinance"


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
                dates=group.dates,
                time_text=t.TIME_TEXT,
                group_link=config.group_links.get(group.key, ""),
                sort_order=order,
            )
    await db.kv_set(SEED_FLAG, "1")
