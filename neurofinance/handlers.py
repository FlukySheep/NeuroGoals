"""User-facing handlers (private chat with the bot)."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from . import content
from . import keyboards as kb
from . import screens
from . import texts as t
from .config import Config
from .db import Database
from .notify import to_admin

router = Router(name="user")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class Form(StatesGroup):
    question = State()
    receipt = State()


# ---------------------------------------------------------------------------
# Shared actions
# ---------------------------------------------------------------------------


async def open_screen(name: str, bot: Bot, chat_id: int, db: Database, config: Config, state: FSMContext) -> None:
    if name == "ask":
        await start_question(bot, chat_id, db, state)
        return
    screen = {
        "step1": screens.step1,
        "step2": screens.step2,
        "step3": screens.step3,
        "step4": screens.step4,
        "step5": screens.step5,
        "step6": screens.step6,
        "doubt": screens.doubt,
        "step7": screens.step7,
        "choose": screens.choose_group,
        "price": screens.price,
        "step8": screens.step8,
        "step9": screens.step9,
        "methods": screens.payment_methods,
        "faq": screens.faq,
    }.get(name)
    if screen:
        await screen(bot, chat_id, db, config)


async def start_question(bot: Bot, chat_id: int, db: Database, state: FSMContext) -> None:
    await state.set_state(Form.question)
    await content.send(bot, chat_id, db, "ask_question", kb.cancel_input)


async def tell_admin_unavailable(message: Message, bot: Bot, db: Database, config: Config) -> None:
    await content.send(
        bot, message.chat.id, db, "admin_unavailable", kb.manager_link(config.manager_url),
        manager=config.manager_username,
    )


# ---------------------------------------------------------------------------
# Commands and the persistent menu
# ---------------------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await state.clear()
    await screens.step1(bot, message.chat.id, db, config)


MENU_ROUTES = {
    t.MENU_ABOUT: "step1",
    t.MENU_PROGRAM: "step5",
    t.MENU_FORMAT: "step7",
    t.MENU_PRICE: "price",
    t.MENU_ZOE: "step6",
    t.MENU_FAQ: "faq",
    t.MENU_SIGNUP: "choose",
    t.MENU_ASK: "ask",
}
COMMAND_ROUTES = {
    "about": "step1",
    "program": "step5",
    "format": "step7",
    "price": "price",
    "zoe": "step6",
    "faq": "faq",
    "signup": "choose",
    "ask": "ask",
}


# Old reply-keyboard labels (still on some clients) work from any state (e.g. the user changes their mind mid-question).
@router.message(F.text.in_(MENU_ROUTES), StateFilter("*"))
async def menu_button(message: Message, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await state.clear()
    await open_screen(MENU_ROUTES[message.text], bot, message.chat.id, db, config, state)


@router.message(Command(*COMMAND_ROUTES), StateFilter("*"))
async def menu_command(message: Message, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await state.clear()
    command = message.text.split()[0].lstrip("/").split("@")[0]
    await open_screen(COMMAND_ROUTES[command], bot, message.chat.id, db, config, state)


# ---------------------------------------------------------------------------
# Storyline callbacks
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("go:"), StateFilter("*"))
async def cb_go(call: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await open_screen(call.data[3:], bot, call.message.chat.id, db, config, state)


@router.callback_query(F.data.startswith("br:"))
async def cb_branch(call: CallbackQuery, bot: Bot, db: Database) -> None:
    await call.answer()
    key = call.data[3:]
    if key in t.BRANCHES:
        await screens.branch(bot, call.message.chat.id, db, key)


@router.callback_query(F.data.startswith("grp:"))
async def cb_group(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    key = call.data[4:]
    if key not in t.GROUPS:
        await call.answer()
        return
    await call.answer(t.GROUPS[key].title)
    await db.set_group(call.from_user.id, key)
    await screens.step8(bot, call.message.chat.id, db, config)


@router.callback_query(F.data.startswith("cur:"))
async def cb_currency(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    await call.answer()
    currency = call.data[4:]
    if currency in t.CURRENCY_AMOUNT:
        await db.set_currency(call.from_user.id, currency)
        await screens.step9(bot, call.message.chat.id, db, config)


@router.callback_query(F.data.startswith("pay:"))
async def cb_method(call: CallbackQuery, bot: Bot, db: Database) -> None:
    await call.answer()
    key = call.data[4:]
    if key in t.PAYMENT_METHODS:
        await screens.payment_method(bot, call.message.chat.id, db, key)


@router.callback_query(F.data.startswith("faq:"))
async def cb_faq(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    await call.answer()
    index = int(call.data[4:])
    if 0 <= index < len(t.FAQ):
        await screens.faq_answer(bot, call.message.chat.id, db, config, index)


# ---------------------------------------------------------------------------
# Payment confirmation, manager contact, questions
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "manager", StateFilter("*"))
async def cb_manager(call: CallbackQuery, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await to_admin(bot, config, db, call.from_user.id, "📞 Просит связаться: ни один способ оплаты не подошёл")
    await content.send(
        bot, call.message.chat.id, db, "contact_manager", kb.manager_link(config.manager_url),
        manager=config.manager_username,
    )


@router.callback_query(F.data == "paid")
async def cb_paid(call: CallbackQuery, bot: Bot, db: Database, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Form.receipt)
    await content.send(bot, call.message.chat.id, db, "ask_receipt", kb.cancel_input)


@router.callback_query(F.data == "cancel", StateFilter("*"))
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Отменено")
    await state.clear()
    await call.message.edit_reply_markup(reply_markup=None)


@router.message(Form.receipt)
async def on_receipt(message: Message, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if user and user["status"] != "paid":
        await db.set_status(message.from_user.id, "pending")
    delivered = await to_admin(
        bot, config, db, message.from_user.id, "💳 Новая оплата — проверьте платёж",
        original=message, reply_markup=kb.admin_payment(message.from_user.id),
    )
    if delivered:
        await content.send(bot, message.chat.id, db, "receipt_received")
    else:
        await tell_admin_unavailable(message, bot, db, config)


@router.message(Form.question)
async def on_question(message: Message, bot: Bot, db: Database, config: Config, state: FSMContext) -> None:
    await state.clear()
    await forward_question(message, bot, db, config)


async def forward_question(message: Message, bot: Bot, db: Database, config: Config) -> None:
    if await to_admin(bot, config, db, message.from_user.id, "❓ Вопрос", original=message):
        await content.send(bot, message.chat.id, db, "question_sent")
    else:
        await tell_admin_unavailable(message, bot, db, config)


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"Ваш id: <code>{message.chat.id}</code>")


# Anything else the user writes is treated as a question to the team.
@router.message()
async def fallback(message: Message, bot: Bot, db: Database, config: Config) -> None:
    if message.text and message.text.startswith("/"):
        await message.answer("Не знаю такой команды. Нажмите кнопку меню слева от поля ввода.")
        return
    await forward_question(message, bot, db, config)
