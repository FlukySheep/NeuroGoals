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
# The product ecosystem: every product has a type and a place on the long-term path.
PRODUCT_TYPES = {
    "book": "📖 Книга",
    "group": "👥 Групповой онлайн-курс",
    "private": "🎯 Личная онлайн-консультация",
    "selfpaced": "🎧 Курс в своём темпе",
    "game": "🎲 Интерактивная игра",
}
STAGES = {
    0: "— не указан —",
    1: "1 · Знакомство — первый лёгкий шаг",
    2: "2 · Основа — главная трансформация",
    3: "3 · Углубление — личная работа",
    4: "4 · Сопровождение — закрепление и рост",
}
ECOSYSTEM_KEY = "ai:ecosystem"
ECOSYSTEM_FLAG = "catalog:ecosystem_v1"
DEFAULT_ECOSYSTEM = """Миссия: Зоя Зарайская помогает людям выстроить спокойные, взрослые и честные отношения с деньгами, \
собой и своей жизнью — не через мотивацию и аффирмации, а через понимание своих сценариев и реальные изменения в поведении.

Путь человека в экосистеме (от простого к глубокому):
1. Знакомство — недорогой и лёгкий вход: книга, интерактивная игра. Человек узнаёт подход, видит свои первые паттерны.
2. Основа — групповой онлайн-курс или курс в своём темпе. Главная трансформация: от осознания сценария к новым действиям.
3. Углубление — личная онлайн-консультация: разбор своей ситуации один на один, когда нужен индивидуальный подход.
4. Сопровождение — повторные игры, продвинутые курсы и консультации, чтобы закрепить изменения и расти дальше.

Как продукты дополняют друг друга:
- Книга и игра готовят к курсу и помогают закрепить его.
- Групповой курс даёт структуру и поддержку группы; курс в своём темпе — для тех, кому неудобно расписание.
- Личная консультация — когда запрос очень личный, срочный или человеку важно разобрать свою ситуацию отдельно.

Принципы рекомендаций: начинать с того, что подходит человеку сейчас (по запросу, времени, бюджету и готовности), \
не продавать всё сразу, честно говорить, если продукт не подходит."""

NEUROFINANCE_KB = """Что это: четырёхдневная практическая онлайн-программа Зои Зарайской о финансовом сценарии — \
наборе неосознанных правил о деньгах, усвоенных в семье и детстве.

Для кого: для тех, у кого деньги приходят и уходят, кто недооценивает свой труд, боится называть цену и продавать, \
избегает финансовых решений, чувствует стыд или тревогу из-за денег, конфликтует из-за денег с партнёром \
или хочет зарабатывать больше, но что-то внутри мешает.

Как устроено: 4 встречи по 3–4 часа в маленькой группе (Zoom), работа на четырёх уровнях — история, осознание, \
новый выбор, действие.
День 1. Откуда это взялось — семейная финансовая история, карта происхождения сценария.
День 2. Как это работает сейчас — повторяющиеся ситуации, страхи, избегание, ресурсные части сценария.
День 3. Что нужно изменить — какие правила больше не ваши, что перестать делать.
День 4. Новые действия — конкретный план изменений в поведении.

Результат: понимание, почему вы поступаете с деньгами именно так, и план конкретных новых действий.

Чего тут нет: это не курс по инвестициям и не обещание быстро разбогатеть.

Связи: хороший первый серьёзный шаг. После курса — личная консультация для разбора своей ситуации глубже."""
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
        await upgrade_ecosystem(db)
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
            product_type="group",
            stage=2,
            knowledge_base=NEUROFINANCE_KB,
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
        await db.kv_set(ECOSYSTEM_FLAG, "1")
    await db.kv_set(SEED_FLAG, "1")


async def upgrade_ecosystem(db: Database) -> None:
    """Catalogs seeded before the ecosystem fields: describe НейроФинансы once."""
    if await db.kv_get(ECOSYSTEM_FLAG):
        return
    nf = await db.course_get(NEUROFINANCE)
    if nf and not nf.get("product_type"):
        await db.course_save(NEUROFINANCE, product_type="group", stage=nf.get("stage") or 2,
                             knowledge_base=nf.get("knowledge_base") or NEUROFINANCE_KB)
    await db.kv_set(ECOSYSTEM_FLAG, "1")


async def ecosystem_text(db: Database) -> str:
    text = await db.kv_get(ECOSYSTEM_KEY)
    return DEFAULT_ECOSYSTEM if text is None else text


def type_label(course: dict) -> str:
    return PRODUCT_TYPES.get(course.get("product_type") or "group", PRODUCT_TYPES["group"])


async def upgrade_schedules(db: Database) -> None:
    """Catalogs seeded before structured schedules: give НейроФинансы its real sessions once."""
    if await db.kv_get(SCHEDULE_FLAG):
        return
    for key in NEUROFINANCE_SESSIONS:
        cohort = await db.cohort_get(key)
        if cohort and cohort["course_id"] == NEUROFINANCE and not cohort.get("sessions"):
            await db.cohort_save(key, **_neurofinance_schedule(key))
    await db.kv_set(SCHEDULE_FLAG, "1")
