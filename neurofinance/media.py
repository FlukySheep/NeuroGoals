"""Media slots: portrait (step 1), program card (step 5), video note (step 6).

Lookup order for each slot:
1. a file the manager sent to the bot (stored as a Telegram file_id in the database);
2. MEDIA_* setting: a Telegram file_id, or a local file path (uploaded once, then cached).
Missing media is skipped. Photos attached to a post in the admin panel take precedence.
"""

import logging
import os

from aiogram import Bot
from aiogram.types import FSInputFile, Message

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

SLOTS = ("portrait", "program", "video_note")


async def photo_source(slot: str, config: Config, db: Database) -> str | FSInputFile | None:
    uploaded = await db.kv_get(f"media:{slot}")
    if uploaded:
        return uploaded
    value = config.media.get(slot, "")
    if not value:
        return None
    cached = await db.kv_get(f"upload:{value}")
    if cached:
        return cached
    if os.path.isfile(value):
        return FSInputFile(value)
    if "/" in value or "." in value:
        return None  # a path that does not exist (yet)
    return value  # Telegram file_id


async def remember_upload(slot: str, config: Config, db: Database, msg: Message) -> None:
    """After a local file was uploaded, cache its file_id so it is uploaded only once."""
    file = msg.photo[-1] if msg.photo else msg.video_note
    if file:
        await db.kv_set(f"upload:{config.media.get(slot, '')}", file.file_id)


async def send_video_note(bot: Bot, chat_id: int, config: Config, db: Database) -> Message | None:
    source = await photo_source("video_note", config, db)
    if source is None:
        return None
    try:
        msg = await bot.send_video_note(chat_id, source)
    except Exception:
        log.exception("Failed to send video note")
        return None
    if not isinstance(source, str):
        await remember_upload("video_note", config, db, msg)
    return msg
