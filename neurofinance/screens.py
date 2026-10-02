"""Each screen of the storyline as a function that sends it to a chat.

Texts and photos come from content.py (editable in the admin panel).
"""

from aiogram import Bot

from . import content
from . import keyboards as kb
from . import media
from . import texts as t
from .config import Config
from .db import Database


def group_vars(group: t.Group) -> dict[str, str]:
    return {"group": group.title, "dates": group.dates, "time": t.TIME_TEXT}


async def step1(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "step1", kb.step1, config)


async def step2(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "step2", kb.step2)


async def branch(bot: Bot, chat_id: int, db: Database, key: str) -> None:
    await content.send(bot, chat_id, db, f"branch_{key}", kb.branch)


async def step3(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "step3", kb.step3)


async def step4(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "step4", kb.step4)


async def step5(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "step5", kb.step5, config)


async def doubt(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "doubt", kb.doubt)


async def step6(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    if await media.send_video_note(bot, chat_id, config, db) is None:
        await content.send(bot, chat_id, db, "step6_fallback")
    await content.send(bot, chat_id, db, "step6", kb.step6)


async def step7(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "step7", kb.choose_group)


async def choose_group(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "choose_group", kb.choose_group)


async def price(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "price", kb.price)


async def step8(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    user = await db.get_user(chat_id)
    group = t.GROUPS.get(user["group_key"]) if user else None
    if group is None:
        await choose_group(bot, chat_id, db, config)
        return
    await content.send(bot, chat_id, db, "step8", kb.step8, **group_vars(group))


async def step9(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "step9", kb.step9)


async def payment_methods(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    user = await db.get_user(chat_id)
    currency = user["currency"] if user else None
    if currency not in t.CURRENCY_AMOUNT:
        await step8(bot, chat_id, db, config)
        return
    await content.send(
        bot, chat_id, db, "payment_methods", kb.payment_methods(currency), amount=t.CURRENCY_AMOUNT[currency]
    )


async def payment_method(bot: Bot, chat_id: int, db: Database, key: str) -> None:
    user = await db.get_user(chat_id)
    method = t.PAYMENT_METHODS[key]
    currency = user["currency"] if user and user["currency"] in method.currencies else method.currencies[0]
    group = t.GROUPS.get(user["group_key"]) if user else None
    await db.set_method(chat_id, key)
    await content.send(
        bot, chat_id, db, f"method_{key}", kb.payment_method(method),
        amount=t.CURRENCY_AMOUNT[currency], group=group.title if group else "—",
    )


async def step10(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    user = await db.get_user(chat_id)
    group = t.GROUPS.get(user["group_key"]) if user else None
    if group is None:
        group = next(iter(t.GROUPS.values()))
    link = config.group_links.get(group.key) or None
    await content.send(
        bot, chat_id, db, "step10", kb.step10(link), **group_vars(group), group_access=group_access(bool(link)),
    )


def group_access(has_link: bool) -> str:
    return (
        "Сейчас вы можете перейти в закрытую группу участников."
        if has_link
        else "Ссылку на закрытую группу участников пришлёт менеджер в ближайшее время."
    )


async def faq(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    await content.send(bot, chat_id, db, "faq", kb.faq_list(await content.faq_visible(db)))


async def faq_answer(bot: Bot, chat_id: int, db: Database, config: Config, index: int) -> None:
    if index not in {i for i, _ in await content.faq_visible(db)}:
        await faq(bot, chat_id, db, config)
        return
    await content.send(bot, chat_id, db, f"faq_{index}", kb.faq_answer)
