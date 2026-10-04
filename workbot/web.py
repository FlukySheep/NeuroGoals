"""Webhook routes of the work bot, mounted into the shared Starlette app (neurofinance/web.py).

  POST /api/workbot          Telegram webhook (checks X-Telegram-Bot-Api-Secret-Token)
  GET  /api/workbot/setup    registers the webhook + command menu (?key=WORK_WEBHOOK_SECRET)
"""

import asyncio
import hmac
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .app import make_bot, make_dispatcher, setup_profile
from .config import WorkConfig, load_work_config
from .db import WorkDatabase

log = logging.getLogger(__name__)


class _State:
    config: WorkConfig | None = None
    db: WorkDatabase | None = None
    dp: Dispatcher | None = None
    bot: Bot | None = None
    bot_loop: asyncio.AbstractEventLoop | None = None


S = _State()


def _init() -> None:
    if S.dp is None:
        S.config = load_work_config()
        S.db = WorkDatabase(S.config.database_url)
        S.dp = make_dispatcher(S.db, S.config)


def _bot() -> Bot:
    # The HTTP session is bound to the event loop; recreate it if the loop changed.
    loop = asyncio.get_running_loop()
    if S.bot is None or S.bot_loop is not loop:
        S.bot, S.bot_loop = make_bot(S.config), loop
    return S.bot


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


async def telegram(request: Request) -> JSONResponse:
    try:
        _init()
    except Exception as exc:
        return JSONResponse({"error": f"configuration: {exc}"}, status_code=503)
    if not _same(request.headers.get("x-telegram-bot-api-secret-token", ""), S.config.webhook_secret):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    bot = _bot()
    try:
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await S.dp.feed_update(bot, update)
    except Exception:
        # Always 200: otherwise Telegram keeps re-sending the same update.
        log.exception("Failed to process work bot update")
    return JSONResponse({"ok": True})


async def setup(request: Request) -> JSONResponse:
    try:
        _init()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"configuration: {exc}"}, status_code=500)
    if not _same(request.query_params.get("key", ""), S.config.webhook_secret):
        return JSONResponse({"error": "forbidden: pass ?key=<WORK_WEBHOOK_SECRET>"}, status_code=403)
    try:
        host = request.headers.get("x-forwarded-host") or request.url.hostname
        url = f"{S.config.public_url or f'https://{host}'}/api/workbot"
        bot = _bot()
        await S.db.connect()  # creates tables on first run
        await bot.set_webhook(
            url, secret_token=S.config.webhook_secret,
            allowed_updates=S.dp.resolve_used_update_types(), drop_pending_updates=False,
        )
        await setup_profile(bot)
        me = await bot.get_me()
        info = await bot.get_webhook_info()
        trello_user = None
        trello = S.dp["trello"]
        if trello.configured:
            trello_user = (await trello.me())["username"]
        return JSONResponse({
            "ok": True,
            "bot": f"@{me.username}",
            "webhook": info.url,
            "work_chats": sorted(S.config.chat_ids),
            "trello_account": trello_user,
            "board": (await S.db.board_get() or {}).get("url"),
        })
    except Exception as exc:
        log.exception("Work bot setup failed")
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500)


routes = [
    Route("/api/workbot", telegram, methods=["POST"]),
    Route("/api/workbot/setup", setup, methods=["GET"]),
]
