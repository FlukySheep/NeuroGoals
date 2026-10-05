"""Manager/admin side: payment confirmation, reply relay, broadcasts, stats."""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import content
from . import keyboards as kb
from . import screens
from . import texts as t
from .config import Config
from .db import Database

log = logging.getLogger(__name__)
router = Router(name="admin")


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, config: Config) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and user.id in config.admin_ids)


class InAdminChat(Filter):
    async def __call__(self, message: Message, config: Config) -> bool:
        return bool(config.admin_chat_id) and message.chat.id == config.admin_chat_id


@router.message(Command("myid"), F.chat.type != "private")
async def cmd_myid_group(message: Message) -> None:
    await message.answer(f"id этого чата: <code>{message.chat.id}</code>")


# ---------------------------------------------------------------------------
# Payment confirmation
# ---------------------------------------------------------------------------


async def confirm_payment(bot: Bot, db: Database, config: Config, user_id: int) -> bool:
    if await db.get_user(user_id) is None:
        return False
    await db.set_status(user_id, "paid")
    await db.bump_step(user_id, 11)
    await screens.step10(bot, user_id, db, config)
    return True


@router.callback_query(F.data.startswith("adm:ok:"), IsAdmin())
async def cb_confirm(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    user_id = int(call.data.split(":")[2])
    try:
        ok = await confirm_payment(bot, db, config, user_id)
    except TelegramForbiddenError:
        await db.set_status(user_id, "paid")
        await db.mark_blocked(user_id)
        ok = False
    await call.answer("Оплата подтверждена" if ok else "Статус обновлён, но сообщение не доставлено")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.reply(f"✅ Оплата подтверждена ({call.from_user.full_name})")


@router.callback_query(F.data.startswith("adm:no:"), IsAdmin())
async def cb_reject(call: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    user_id = int(call.data.split(":")[2])
    await db.set_status(user_id, "chose_group")
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    try:
        await content.send(bot, user_id, db, "payment_rejected", kb.manager_link(config.manager_url))
    except TelegramForbiddenError:
        await db.mark_blocked(user_id)
    await call.message.reply(f"❌ Отмечено: платёж не найден ({call.from_user.full_name})")


@router.message(Command("paid"), IsAdmin())
async def cmd_paid(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config) -> None:
    """/paid <user_id> — confirm a payment manually (e.g. paid outside the bot)."""
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /paid &lt;user_id&gt;")
        return
    ok = await confirm_payment(bot, db, config, int(command.args.strip()))
    await message.answer("✅ Готово" if ok else "Пользователь не найден (он должен сначала запустить бота)")


# ---------------------------------------------------------------------------
# Stats and broadcast
# ---------------------------------------------------------------------------


@router.message(Command("stats"), IsAdmin())
async def cmd_stats(message: Message, db: Database) -> None:
    stats = await db.stats()
    labels = {"new": "Смотрят", "chose_group": "Выбрали группу", "pending": "Ждут проверки оплаты", "paid": "Оплатили"}
    lines = []
    for row in sorted(stats, key=lambda r: (r["status"], r["group_key"] or "")):
        group = t.GROUPS.get(row["group_key"] or "")
        group_title = group.title if group else "без группы"
        lines.append(f"{labels.get(row['status'], row['status'])} · {group_title}: <b>{row['n']}</b>")
    await message.answer("<b>Статистика</b>\n\n" + ("\n".join(lines) or "Пока пусто"))


@router.message(Command("closing"), IsAdmin())
async def cmd_closing(message: Message, db: Database) -> None:
    count = len(await db.closing_recipients())
    await message.answer(
        f"Отправить сообщение о закрытии записи {count} пользователям "
        "(все, кто не оплатил и ещё не получал это сообщение)?\n\n"
        "Текст сообщения — в админке, раздел «Рассылки».",
        reply_markup=kb.closing_confirm,
    )


@router.callback_query(F.data.startswith("adm:closing:"), IsAdmin())
async def cb_closing(call: CallbackQuery, bot: Bot, db: Database) -> None:
    await call.message.edit_reply_markup(reply_markup=None)
    if call.data.endswith(":no"):
        await call.answer("Отменено")
        return
    await call.answer("Отправляю…")
    sent = failed = 0
    post = await content.render(db, "closing")
    for user_id in await db.closing_recipients():
        # Claim first: a retried webhook or a double click never sends twice.
        if not await db.claim_closing(user_id):
            continue
        try:
            await content.send_rendered(bot, user_id, post, kb.closing)
            sent += 1
        except TelegramForbiddenError:
            await db.mark_blocked(user_id)
            failed += 1
        except Exception:
            log.exception("Broadcast to %s failed", user_id)
            failed += 1
        await asyncio.sleep(0.05)  # stay well below Telegram's 30 msg/s limit
    await call.message.answer(f"Рассылка завершена: отправлено {sent}, не доставлено {failed}.")


# ---------------------------------------------------------------------------
# Reply relay: a reply to a notification in the admin chat goes to the user.
# ---------------------------------------------------------------------------


class IsRelayReply(Filter):
    """Matches a reply (in the admin chat) to a message that belongs to a user conversation."""

    async def __call__(self, message: Message, db: Database) -> bool | dict:
        if message.reply_to_message is None:
            return False
        user_id = await db.relay_user(message.reply_to_message.message_id)
        return {"target_user_id": user_id} if user_id else False


@router.message(InAdminChat(), IsRelayReply())
async def relay_reply(message: Message, bot: Bot, db: Database, target_user_id: int) -> None:
    try:
        await bot.copy_message(target_user_id, message.chat.id, message.message_id)
    except TelegramForbiddenError:
        await db.mark_blocked(target_user_id)
        await message.reply("⚠️ Пользователь заблокировал бота, сообщение не доставлено.")
        return
    await db.save_relay(message.message_id, target_user_id)
    await message.reply("✉️ Отправлено")


# ---------------------------------------------------------------------------
# Media upload: the manager sends a photo / video note to the bot in private.
# ---------------------------------------------------------------------------

PHOTO_SLOTS = {"портрет": "portrait", "portrait": "portrait", "программа": "program", "program": "program"}
SLOT_TITLES = {"portrait": "портрет (шаг 1)", "program": "карточка программы (шаг 5)", "video_note": "кружочек (шаг 6)"}
MEDIA_HELP = (
    "Чтобы заменить медиа, пришлите мне сюда:\n"
    "— фото с подписью <b>портрет</b> — для первого экрана;\n"
    "— фото с подписью <b>программа</b> — для карточки программы;\n"
    "— видеокружочек — для шага 6."
)


@router.message(F.chat.type == "private", IsAdmin(), F.video_note)
async def upload_video_note(message: Message, db: Database) -> None:
    await db.kv_set("media:video_note", message.video_note.file_id)
    await message.reply(f"✅ Сохранено: {SLOT_TITLES['video_note']}")


@router.message(F.chat.type == "private", IsAdmin(), F.photo)
async def upload_photo(message: Message, db: Database) -> None:
    slot = PHOTO_SLOTS.get((message.caption or "").strip().lower())
    if slot is None:
        await message.reply(MEDIA_HELP)
        return
    await db.kv_set(f"media:{slot}", message.photo[-1].file_id)
    await message.reply(f"✅ Сохранено: {SLOT_TITLES[slot]}")


@router.message(Command("media"), IsAdmin())
async def cmd_media(message: Message, db: Database) -> None:
    lines = []
    for slot, title in SLOT_TITLES.items():
        mark = "✅" if await db.kv_get(f"media:{slot}") else "—"
        lines.append(f"{mark} {title}")
    await message.answer("<b>Медиа</b>\n\n" + "\n".join(lines) + "\n\n" + MEDIA_HELP)
