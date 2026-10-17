"""Bot side of the AI consultant: a short interview at /start, then a personal path through the products."""

import html
import json
import logging
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import ai, content, journey, journey_bot
from . import keyboards as kb
from .config import Config
from .db import Database
from .notify import to_admin

log = logging.getLogger(__name__)

router = Router(name="agent")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

GREETING_KEY = "ai:greeting"
DEFAULT_GREETING = (
    "Здравствуйте! Я ИИ-консультант Зои Зарайской.\n\n"
    "Задам 3–5 коротких вопросов и предложу программы, которые подойдут именно вам, "
    "и в каком порядке их лучше проходить. Это займёт пару минут."
)
THINKING = "Секунду, я ещё думаю над предыдущим ответом…"
FAILED = ("Не получилось ответить — попробуйте ещё раз чуть позже. "
          "А пока можно посмотреть все программы или написать менеджеру.")
LIMIT = "Сегодня вы уже несколько раз проходили подбор. Посмотрите все программы или напишите менеджеру."
ROLE_ICONS = {"start": "🟢", "next": "➡️", "later": "⏳"}


class Interview(StatesGroup):
    answer = State()


def _btn(text: str, data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data)]


def _fallback_kb(config: Config) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        _btn("Все программы", "ai:catalog"),
        [InlineKeyboardButton(text="Написать менеджеру", url=config.manager_url)],
    ])


async def _products(db: Database, config: Config, user_id: int) -> list[dict]:
    return journey.visible_courses(await db.courses_all(), journey_bot._is_admin(config, user_id))


async def greeting(db: Database) -> str:
    text = await db.kv_get(GREETING_KEY)
    return DEFAULT_GREETING if text is None else text


async def start(bot: Bot, chat_id: int, db: Database, config: Config) -> bool:
    """/start with the consultant on. Returns False when there is nothing to recommend yet."""
    from . import catalog
    await catalog.ensure_seeded(db, config)
    if not await _products(db, config, chat_id):
        return False
    markup = InlineKeyboardMarkup(inline_keyboard=[_btn("✨ Подобрать программу", "ai:go"),
                                                   _btn("Посмотреть все программы", "ai:catalog")])
    await bot.send_message(chat_id, html.escape(await greeting(db)), reply_markup=markup)
    return True


async def _send_reply(bot: Bot, chat_id: int, db: Database, config: Config, user_id: int,
                      reply: dict, state: FSMContext) -> None:
    text = html.escape(reply["message"])
    if reply["kind"] == "question":
        rows = [_btn(o, f"ai:o:{i}") for i, o in enumerate(reply["options"])]
        await bot.send_message(chat_id, text + "\n\n<i>Выберите вариант или напишите своими словами.</i>",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
        await state.set_state(Interview.answer)
        return

    await state.clear()
    titles = {c["id"]: c["title"] for c in await db.courses_all()}
    rows = [_btn(f"{ROLE_ICONS[r['role']]} {titles.get(r['product_id'], r['product_id'])}", f"jc:{r['product_id']}")
            for r in reply["recommendations"]]
    rows += [_btn("💬 Обсудить с Зоей", "ai:manager"), _btn("🔄 Пройти подбор заново", "ai:go")]
    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    path = "\n".join(f"{ROLE_ICONS[r['role']]} {html.escape(titles.get(r['product_id'], r['product_id']))}"
                     for r in reply["recommendations"]) or "— ничего не подошло"
    summary = html.escape(reply.get("summary") or "")
    await to_admin(bot, config, db, user_id, "🤖 ИИ-подбор завершён",
                   details=f"{summary}\n\n<b>Путь:</b>\n{path}")


async def _advance(bot: Bot, chat_id: int, user_id: int, db: Database, config: Config,
                   state: FSMContext, answer: str | None) -> None:
    chat = await db.ai_chat_current(user_id)
    if chat is None or chat["status"] != "active":
        await bot.send_message(chat_id, "Начнём подбор заново?",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[_btn("✨ Подобрать программу", "ai:go")]))
        return
    if not await db.ai_chat_claim(chat["id"]):
        await bot.send_message(chat_id, THINKING)
        return
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        reply = await ai.interview_turn(db, config, chat, await _products(db, config, user_id), answer)
    except ai.AIError as exc:
        log.warning("AI consultant failed for %s: %s", user_id, exc)
        await bot.send_message(chat_id, FAILED, reply_markup=_fallback_kb(config))
        return
    finally:
        await db.ai_chat_release(chat["id"])
    await _send_reply(bot, chat_id, db, config, user_id, reply, state)


@router.callback_query(F.data == "ai:go")
async def cb_go(call: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await call.answer()
    user_id, chat_id = call.from_user.id, call.message.chat.id
    if not config.ai_enabled_for(user_id):
        await journey_bot.send_catalog(bot, chat_id, db, config)
        return
    is_admin = journey_bot._is_admin(config, user_id)
    if not is_admin and await db.ai_chats_since(user_id, time.time() - 86400) >= ai.DAILY_INTERVIEWS:
        await bot.send_message(chat_id, LIMIT, reply_markup=_fallback_kb(config))
        return
    await db.ai_chat_new(user_id)
    await _advance(bot, chat_id, user_id, db, config, state, None)


@router.callback_query(F.data.startswith("ai:o:"))
async def cb_option(call: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    chat = await db.ai_chat_current(call.from_user.id)
    options = json.loads((chat or {}).get("options") or "[]")
    index = int(call.data[5:]) if call.data[5:].isdigit() else -1
    if not chat or chat["status"] != "active" or not 0 <= index < len(options):
        await call.answer("Этот вопрос уже позади", show_alert=False)
        return
    await call.answer()
    try:  # keep the chosen answer visible, drop the other buttons
        await call.message.edit_text(f"{call.message.html_text}\n\n✔️ <b>{html.escape(options[index])}</b>",
                                     reply_markup=None)
    except Exception:
        pass
    await _advance(bot, call.message.chat.id, call.from_user.id, db, config, state, options[index])


@router.message(Interview.answer, F.text, ~F.text.startswith("/"))
async def on_answer(message: Message, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await _advance(bot, message.chat.id, message.from_user.id, db, config, state, message.text)


@router.callback_query(F.data == "ai:catalog")
async def cb_catalog(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    await call.answer()
    await journey_bot.send_catalog(bot, call.message.chat.id, db, config)


@router.callback_query(F.data == "ai:manager")
async def cb_manager(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    await call.answer()
    chat = await db.ai_chat_current(call.from_user.id)
    summary = html.escape((chat or {}).get("summary") or "")
    await to_admin(bot, config, db, call.from_user.id, "📞 Хочет обсудить подбор", details=summary)
    await content.send(bot, call.message.chat.id, db, "contact_manager", kb.manager_link(config.manager_url),
                       manager=config.manager_username)
