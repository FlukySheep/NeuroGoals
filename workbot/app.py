"""Pieces shared by the polling runner (main.py) and the webhook (web.py)."""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, CallbackQuery, Message, TelegramObject

from . import handlers
from . import texts as t
from .config import WorkConfig
from .db import WorkDatabase
from .trello import Trello

log = logging.getLogger(__name__)

OPEN_COMMANDS = {"chatid"}             # answered in any chat, so an admin can find the chat id
PRIVATE_OPEN_COMMANDS = {"start", "help"}


def _command(event: TelegramObject) -> str:
    text = (event.text or event.caption or "") if isinstance(event, Message) else ""
    if not text.startswith("/"):
        return ""
    return text.split(maxsplit=1)[0][1:].split("@", 1)[0].lower()


class AccessMiddleware(BaseMiddleware):
    """The bot works only in WORK_CHAT_ID groups, and in private for people linked to Trello."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat, user = data.get("event_chat"), data.get("event_from_user")
        config: WorkConfig = data["config"]
        db: WorkDatabase = data["db"]
        if chat is None or user is None:
            return None
        command = _command(event)
        if chat.id in config.chat_ids:
            await db.touch_member(user.id, user.username, user.full_name)
            return await handler(event, data)
        if command in OPEN_COMMANDS:
            return await handler(event, data)
        if chat.type == "private":
            if (command in PRIVATE_OPEN_COMMANDS or user.id in config.admin_ids
                    or await db.member_by_tg(user.id)):
                return await handler(event, data)
            if isinstance(event, Message):
                await event.answer(t.START_PRIVATE)
            return None
        if isinstance(event, CallbackQuery):
            await event.answer(t.NOT_ALLOWED, show_alert=True)
        return None  # a group that is not ours: stay silent


def make_bot(config: WorkConfig, **kwargs: Any) -> Bot:
    return Bot(
        config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
        **kwargs,
    )


def make_dispatcher(db: WorkDatabase, config: WorkConfig) -> Dispatcher:
    dp = Dispatcher(db=db, config=config, trello=Trello(config.trello_key, config.trello_token))
    access = AccessMiddleware()
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(handlers.router)
    return dp


async def setup_profile(bot: Bot) -> None:
    try:
        commands = [BotCommand(command=c, description=d) for c, d in t.COMMANDS]
        await bot.set_my_commands(commands)
        await bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_short_description("Задачи, события и заметки команды → Trello")
    except Exception:
        log.exception("Failed to update bot profile")
