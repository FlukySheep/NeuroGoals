"""Bot side of the course catalog and journeys (only for users with the catalog switched on)."""

import html

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from . import catalog, content, journey, screens
from . import keyboards as kb
from .config import Config
from .db import Database
from .notify import to_admin

router = Router(name="journey")
router.callback_query.filter(F.message.chat.type == "private")

CATALOG_TEXT = "<b>Программы Зои Зарайской</b>\n\nВыберите курс, чтобы узнать подробнее:"


def _is_admin(config: Config, user_id: int) -> bool:
    return user_id in config.admin_ids or user_id == config.admin_chat_id


async def _visible(db: Database, config: Config, user_id: int) -> list[dict]:
    return journey.visible_courses(await db.courses_all(), _is_admin(config, user_id))


async def send_catalog(bot: Bot, chat_id: int, db: Database, config: Config) -> None:
    rows = []
    for c in await _visible(db, config, chat_id):
        label = c["title"]
        if c["status"] == "waitlist":
            label += " · лист ожидания"
        elif c["status"] in ("draft", "closed"):
            label = f"🔒 {label} ({catalog.STATUSES[c['status']].split(' ')[0].lower()})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"jc:{c['id']}")])
    await bot.send_message(chat_id, CATALOG_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def open_course(bot: Bot, chat_id: int, db: Database, config: Config, course: dict) -> None:
    """The course's start step, or a generated course card when it has no journey yet."""
    if course.get("start_step"):
        step = await db.step_get(course["id"], course["start_step"])
        if step:
            await journey.send_step(bot, chat_id, course["id"], step)
            return
    lines = [f"<b>{html.escape(course['title'])}</b>\n{catalog.type_label(course)}"]
    if course.get("summary"):
        lines.append(course["summary"])
    for g in await db.cohorts_for(course["id"]):
        lines.append(f"<b>{html.escape(g['title'])}</b>\n{html.escape(g['dates'] or '')}\n{html.escape(g['time_text'] or '')}")
    lines.append(f"Стоимость: <b>{catalog.price_line(course)}</b>")
    buttons = [journey.Button("Открыть", "url", course["url"])] if course.get("url") else []
    if course["status"] == "open":
        buttons.append(journey.Button("Записаться", "enroll"))
    elif course["status"] == "waitlist":
        buttons.append(journey.Button("Записаться в лист ожидания", "waitlist"))
    buttons += [journey.Button("Задать вопрос", "ask"), journey.Button("Все курсы", "catalog")]
    await content.send_rendered(bot, chat_id, content.Post(html="\n\n".join(lines), photo=course.get("photo")),
                                journey.keyboard(course["id"], buttons))


async def start(bot: Bot, chat_id: int, db: Database, config: Config, args: str | None) -> bool:
    """Entry point from /start. Returns False to fall back to the classic НейроФинансы flow."""
    await catalog.ensure_seeded(db, config)
    visible = await _visible(db, config, chat_id)
    if args and args.startswith("c_"):
        course = next((c for c in visible if c["id"] == args[2:]), None)
        if course:
            await open_course(bot, chat_id, db, config, course)
            return True
    if not visible:
        return False
    if len(visible) == 1:
        await open_course(bot, chat_id, db, config, visible[0])
    else:
        await send_catalog(bot, chat_id, db, config)
    return True


async def _course_for(call: CallbackQuery, db: Database, config: Config, course_id: str) -> dict | None:
    course = await db.course_get(course_id)
    if course is None or course not in journey.visible_courses([course], _is_admin(config, call.from_user.id)):
        await call.answer("Этот курс сейчас недоступен", show_alert=True)
        return None
    return course


@router.callback_query(F.data.startswith("jc:"))
async def cb_open_course(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    course = await _course_for(call, db, config, call.data[3:])
    if course:
        await call.answer()
        await open_course(bot, call.message.chat.id, db, config, course)


@router.callback_query(F.data.startswith("j:"))
async def cb_step(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    _, course_id, key = call.data.split(":", 2)
    if not await _course_for(call, db, config, course_id):
        return
    step = await db.step_get(course_id, key)
    await call.answer()
    if step is None:
        await send_catalog(bot, call.message.chat.id, db, config)
        return
    await journey.send_step(bot, call.message.chat.id, course_id, step)


@router.callback_query(F.data.startswith("ja:"))
async def cb_action(call: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    _, course_id, action = call.data.split(":", 2)
    course = await _course_for(call, db, config, course_id)
    if not course:
        return
    await call.answer()
    chat_id, user_id = call.message.chat.id, call.from_user.id
    title = html.escape(course["title"])
    if action == "catalog":
        await send_catalog(bot, chat_id, db, config)
    elif action == "faq":
        await screens.faq(bot, chat_id, db, config)
    elif action == "ask":
        from .handlers import start_question
        await start_question(bot, chat_id, db, state)
    elif action == "manager":
        await to_admin(bot, config, db, user_id, f"📞 Просит связаться · {title}")
        await content.send(bot, chat_id, db, "contact_manager", kb.manager_link(config.manager_url),
                           manager=config.manager_username)
    elif action == "enroll":
        if course_id == catalog.NEUROFINANCE:
            await screens.choose_group(bot, chat_id, db, config)  # the existing payment flow
        else:
            # Online enrollment for other courses comes with the next catalog step.
            await to_admin(bot, config, db, user_id, f"📝 Хочет записаться · {title}")
            await bot.send_message(chat_id, f"Спасибо! Менеджер свяжется с вами, чтобы оформить запись на «{title}».",
                                   reply_markup=kb.manager_link(config.manager_url))
    elif action == "waitlist":
        added = await db.waitlist_add(user_id, course_id)
        if added:
            await to_admin(bot, config, db, user_id, f"⏳ Лист ожидания · {title}")
        await bot.send_message(chat_id, f"Вы в листе ожидания курса «{title}». "
                                        "Мы напишем, как только откроется запись.")
