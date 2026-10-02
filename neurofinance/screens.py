"""Each screen of the storyline as a function that sends it to a chat."""

from aiogram import Bot

from . import keyboards as kb
from . import media
from . import texts as t
from .config import Config
from .db import Database


async def step1(bot: Bot, chat_id: int, config: Config, db: Database) -> None:
    # The first message also clears the old bottom keyboard; inline buttons ride on the second.
    sent = await media.send_photo(
        bot, chat_id, "portrait", config, db, caption=t.STEP1_CAPTION, reply_markup=kb.remove_keyboard
    )
    if sent is None:
        await bot.send_message(chat_id, t.STEP1_CAPTION, reply_markup=kb.remove_keyboard)
    await bot.send_message(chat_id, t.STEP1, reply_markup=kb.step1)


async def step2(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.STEP2, reply_markup=kb.step2)


async def branch(bot: Bot, chat_id: int, key: str) -> None:
    await bot.send_message(chat_id, t.BRANCHES[key][1], reply_markup=kb.branch)


async def step3(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.STEP3, reply_markup=kb.step3)


async def step4(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.STEP4, reply_markup=kb.step4)


async def step5(bot: Bot, chat_id: int, config: Config, db: Database) -> None:
    await media.send_photo(bot, chat_id, "program", config, db, caption=t.STEP5_CAPTION)
    await bot.send_message(chat_id, "\n\n➖➖➖\n\n".join(t.STEP5_DAYS), reply_markup=kb.step5)


async def doubt(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.DOUBT, reply_markup=kb.doubt)


async def step6(bot: Bot, chat_id: int, config: Config, db: Database) -> None:
    if await media.send_video_note(bot, chat_id, config, db) is None:
        await bot.send_message(chat_id, t.STEP6_FALLBACK)
    await bot.send_message(chat_id, t.STEP6, reply_markup=kb.step6)


async def step7(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.STEP7, reply_markup=kb.choose_group)


async def choose_group(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.CHOOSE_GROUP, reply_markup=kb.choose_group)


async def price(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.PRICE_TEXT, reply_markup=kb.price)


async def step8(bot: Bot, chat_id: int, db: Database) -> None:
    user = await db.get_user(chat_id)
    group = t.GROUPS.get(user["group_key"]) if user else None
    if group is None:
        await choose_group(bot, chat_id)
        return
    await bot.send_message(chat_id, t.step8(group), reply_markup=kb.step8)


async def step9(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.STEP9, reply_markup=kb.step9)


async def payment_methods(bot: Bot, chat_id: int, db: Database) -> None:
    user = await db.get_user(chat_id)
    currency = user["currency"] if user else None
    if currency not in t.CURRENCY_AMOUNT:
        await step8(bot, chat_id, db)
        return
    await bot.send_message(
        chat_id, t.payment_methods_text(currency), reply_markup=kb.payment_methods(currency)
    )


async def payment_method(bot: Bot, chat_id: int, db: Database, key: str) -> None:
    user = await db.get_user(chat_id)
    method = t.PAYMENT_METHODS[key]
    currency = user["currency"] if user and user["currency"] in method.currencies else method.currencies[0]
    group = t.GROUPS.get(user["group_key"]) if user else None
    await db.set_method(chat_id, key)
    await bot.send_message(
        chat_id,
        t.payment_method_text(method, currency, group),
        reply_markup=kb.payment_method(method),
    )


async def step10(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    user = await db.get_user(chat_id)
    group = t.GROUPS.get(user["group_key"]) if user else None
    if group is None:
        group = next(iter(t.GROUPS.values()))
    link = config.group_links.get(group.key) or None
    await bot.send_message(chat_id, t.step10(group, bool(link)), reply_markup=kb.step10(link))


async def faq(bot: Bot, chat_id: int) -> None:
    await bot.send_message(chat_id, t.FAQ_TITLE, reply_markup=kb.faq_list())


async def faq_answer(bot: Bot, chat_id: int, index: int) -> None:
    question, answer = t.FAQ[index]
    if not answer:
        await faq(bot, chat_id)
        return
    await bot.send_message(chat_id, f"<b>{question}</b>\n\n{answer}", reply_markup=kb.faq_answer)
