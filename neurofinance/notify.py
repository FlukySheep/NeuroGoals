"""Messages to the manager/admin chat."""

import html
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message

from . import texts as t
from .config import Config
from .db import Database

log = logging.getLogger(__name__)


async def user_card(db: Database, user_id: int) -> str:
    user = await db.get_user(user_id)
    if user is None:
        return f"id <code>{user_id}</code>"
    name = html.escape(user["full_name"] or "")
    username = f" @{html.escape(user['username'])}" if user["username"] else ""
    lines = [f"👤 <a href=\"tg://user?id={user_id}\">{name}</a>{username} · id <code>{user_id}</code>"]
    group = t.GROUPS.get(user["group_key"] or "")
    if group:
        lines.append(f"Группа: {group.title}")
    if user["currency"]:
        lines.append(f"Сумма: {t.CURRENCY_AMOUNT.get(user['currency'], user['currency'])}")
    method = t.PAYMENT_METHODS.get(user["method"] or "")
    if method:
        lines.append(f"Способ: {method.label}")
    lines.append(f"Статус: {user['status']}")
    return "\n".join(lines)


async def to_admin(
    bot: Bot,
    config: Config,
    db: Database,
    user_id: int,
    title: str,
    original: Message | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Send a notification about a user to the admin chat.

    Every message sent there is remembered, so the manager can simply *reply*
    to it and the reply is delivered to the user.
    """
    if not config.admin_chat_id:
        log.warning("ADMIN_CHAT_ID is not set; dropping notification: %s", title)
        return False
    try:
        header = await bot.send_message(
            config.admin_chat_id,
            f"<b>{title}</b>\n\n{await user_card(db, user_id)}\n\n"
            "<i>Ответьте на это сообщение, чтобы написать пользователю.</i>",
            reply_markup=reply_markup,
        )
        await db.save_relay(header.message_id, user_id)
        if original is not None:
            copied = await bot.copy_message(
                config.admin_chat_id,
                original.chat.id,
                original.message_id,
                reply_to_message_id=header.message_id,
            )
            await db.save_relay(copied.message_id, user_id)
    except Exception:
        log.exception("Failed to notify admin chat")
        return False
    return True
