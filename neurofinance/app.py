"""Pieces shared by the polling runner (main.py) and the Vercel webhook (web.py)."""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, CallbackQuery, TelegramObject

from . import admin, handlers
from . import keyboards as kb
from . import texts as t
from .config import Config
from .db import Database
from .storage import DbStorage

log = logging.getLogger(__name__)


class TrackUserMiddleware(BaseMiddleware):
    """Registers every private-chat user in the database."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user and chat and chat.type == "private":
            await data["db"].upsert_user(user.id, user.username, user.full_name)
        return await handler(event, data)


class ResetInputOnClickMiddleware(BaseMiddleware):
    """Pressing any storyline button abandons a pending question/receipt input."""

    KEEP = ("paid", "cancel", "adm:")

    async def __call__(self, handler, event: CallbackQuery, data: dict[str, Any]) -> Any:
        state: FSMContext | None = data.get("state")
        if state and event.data and not event.data.startswith(self.KEEP):
            await state.clear()
        return await handler(event, data)


def make_bot(config: Config, **kwargs: Any) -> Bot:
    return Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), **kwargs)


def make_dispatcher(db: Database, config: Config) -> Dispatcher:
    dp = Dispatcher(storage=DbStorage(db), db=db, config=config)
    dp.update.outer_middleware(TrackUserMiddleware())
    dp.callback_query.middleware(ResetInputOnClickMiddleware())
    dp.include_routers(admin.router, handlers.router)
    return dp


async def send_reminders(bot: Bot, db: Database, config: Config) -> int:
    sent = 0
    for user_id in await db.claim_reminders(config.reminder_delay_hours):
        try:
            await bot.send_message(user_id, t.REMINDER, reply_markup=kb.reminder)
            sent += 1
        except TelegramForbiddenError:
            await db.mark_blocked(user_id)
        except Exception:
            log.exception("Reminder to %s failed", user_id)
        await asyncio.sleep(0.05)
    return sent


async def setup_profile(bot: Bot) -> None:
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in t.COMMANDS])
        await bot.set_my_description(t.BOT_DESCRIPTION)
        await bot.set_my_short_description(t.BOT_SHORT_DESCRIPTION)
    except Exception:
        log.exception("Failed to update bot profile")
