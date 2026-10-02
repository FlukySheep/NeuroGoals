import logging
import os

from aiogram import Bot
from aiogram.types import FSInputFile, Message

log = logging.getLogger(__name__)

# Uploaded file_ids are cached so each local file is uploaded only once per run.
_file_ids: dict[str, str] = {}


def _source(value: str) -> str | FSInputFile | None:
    if not value:
        return None
    if value in _file_ids:
        return _file_ids[value]
    if os.path.isfile(value):
        return FSInputFile(value)
    if "/" in value or "." in value:
        # Looks like a path that does not exist yet: skip instead of failing.
        return None
    return value  # Telegram file_id


def _remember(value: str, message: Message) -> None:
    file = message.photo[-1] if message.photo else message.video_note
    if file:
        _file_ids[value] = file.file_id


async def send_photo(bot: Bot, chat_id: int, value: str, **kwargs) -> Message | None:
    source = _source(value)
    if source is None:
        return None
    try:
        msg = await bot.send_photo(chat_id, source, **kwargs)
    except Exception:
        log.exception("Failed to send photo %s", value)
        return None
    _remember(value, msg)
    return msg


async def send_video_note(bot: Bot, chat_id: int, value: str) -> Message | None:
    source = _source(value)
    if source is None:
        return None
    try:
        msg = await bot.send_video_note(chat_id, source)
    except Exception:
        log.exception("Failed to send video note %s", value)
        return None
    _remember(value, msg)
    return msg
