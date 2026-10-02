"""Media for steps 1, 5 and 6.

Lookup order for each slot (portrait / program / video_note):
1. a file the manager sent to the bot (stored as a Telegram file_id in the database);
2. MEDIA_* setting: a Telegram file_id, or a local file path (uploaded once, then cached).
Missing media is skipped.
"""

import logging
import os

from aiogram import Bot
from aiogram.types import FSInputFile, Message

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

SLOTS = ("portrait", "program", "video_note")


async def _source(slot: str, config: Config, db: Database) -> tuple[str | FSInputFile | None, str | None]:
    """Return (what to send, cache key to store the resulting file_id under)."""
    uploaded = await db.kv_get(f"media:{slot}")
    if uploaded:
        return uploaded, None
    value = config.media.get(slot, "")
    if not value:
        return None, None
    cached = await db.kv_get(f"upload:{value}")
    if cached:
        return cached, None
    if os.path.isfile(value):
        return FSInputFile(value), f"upload:{value}"
    if "/" in value or "." in value:
        return None, None  # a path that does not exist (yet)
    return value, None  # Telegram file_id


async def _send(bot: Bot, chat_id: int, slot: str, config: Config, db: Database, **kwargs) -> Message | None:
    source, cache_key = await _source(slot, config, db)
    if source is None:
        return None
    try:
        if slot == "video_note":
            msg = await bot.send_video_note(chat_id, source)
        else:
            msg = await bot.send_photo(chat_id, source, **kwargs)
    except Exception:
        log.exception("Failed to send media %s", slot)
        return None
    if cache_key:
        file = msg.photo[-1] if msg.photo else msg.video_note
        if file:
            await db.kv_set(cache_key, file.file_id)
    return msg


async def send_photo(bot: Bot, chat_id: int, slot: str, config: Config, db: Database, **kwargs) -> Message | None:
    return await _send(bot, chat_id, slot, config, db, **kwargs)


async def send_video_note(bot: Bot, chat_id: int, config: Config, db: Database) -> Message | None:
    return await _send(bot, chat_id, "video_note", config, db)
