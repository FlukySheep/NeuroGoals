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
from .config import Config, load_config
from .db import Database

log = logging.getLogger(__name__)

REMINDER_CHECK_SECONDS = 600


class TrackUserMiddleware(BaseMiddleware):
    """Registers every private-chat user in the database."""

    def __init__(self, db: Database):
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user and chat and chat.type == "private":
            await self.db.upsert_user(user.id, user.username, user.full_name)
        return await handler(event, data)


class ResetInputOnClickMiddleware(BaseMiddleware):
    """Pressing any storyline button abandons a pending question/receipt input."""

    KEEP = ("paid", "cancel", "adm:")

    async def __call__(self, handler, event: CallbackQuery, data: dict[str, Any]) -> Any:
        state: FSMContext | None = data.get("state")
        if state and event.data and not event.data.startswith(self.KEEP):
            await state.clear()
        return await handler(event, data)


async def reminder_loop(bot: Bot, db: Database, config: Config) -> None:
    while True:
        try:
            for user_id in await db.due_reminders(config.reminder_delay_hours):
                try:
                    await bot.send_message(user_id, t.REMINDER, reply_markup=kb.reminder)
                except TelegramForbiddenError:
                    await db.mark_blocked(user_id)
                await db.mark_reminded(user_id)
                await asyncio.sleep(0.05)
        except Exception:
            log.exception("Reminder loop iteration failed")
        await asyncio.sleep(REMINDER_CHECK_SECONDS)


async def setup_profile(bot: Bot) -> None:
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in t.COMMANDS])
        await bot.set_my_description(t.BOT_DESCRIPTION)
        await bot.set_my_short_description(t.BOT_SHORT_DESCRIPTION)
    except Exception:
        log.exception("Failed to update bot profile")


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    if not config.admin_chat_id:
        log.warning("ADMIN_CHAT_ID is not set: questions and payments will not reach the manager")

    db = Database(config.db_path)
    await db.connect()

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(db=db, config=config)
    dp.update.outer_middleware(TrackUserMiddleware(db))
    dp.callback_query.middleware(ResetInputOnClickMiddleware())
    dp.include_routers(admin.router, handlers.router)

    await setup_profile(bot)
    await bot.delete_webhook(drop_pending_updates=False)
    reminders = asyncio.create_task(reminder_loop(bot, db, config))
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()
        await db.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
