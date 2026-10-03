"""Long-polling runner for local testing: python -m workbot"""

import asyncio
import logging

from .app import make_bot, make_dispatcher, setup_profile
from .config import load_work_config
from .db import WorkDatabase


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_work_config()
    db = WorkDatabase(config.database_url)
    await db.connect()
    bot = make_bot(config)
    dp = make_dispatcher(db, config)
    await setup_profile(bot)
    # Polling and a webhook are mutually exclusive: this removes any webhook.
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
