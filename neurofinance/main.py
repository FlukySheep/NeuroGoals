"""Long-polling runner: local testing, Docker, any always-on server."""

import asyncio
import logging

from .app import make_bot, make_dispatcher, send_reminders, setup_profile
from .config import load_config
from .db import Database

log = logging.getLogger(__name__)

REMINDER_CHECK_SECONDS = 600


async def reminder_loop(bot, db, config) -> None:
    while True:
        try:
            await send_reminders(bot, db, config)
        except Exception:
            log.exception("Reminder loop iteration failed")
        await asyncio.sleep(REMINDER_CHECK_SECONDS)


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    if not config.admin_chat_id:
        log.warning("ADMIN_CHAT_ID is not set: questions and payments will not reach the manager")

    db = Database(config.database_url)
    await db.connect()
    bot = make_bot(config)
    dp = make_dispatcher(db, config)

    await setup_profile(bot)
    # Polling and a webhook are mutually exclusive: this removes any webhook.
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
