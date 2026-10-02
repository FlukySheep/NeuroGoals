"""Webhook app for Vercel (or any ASGI host).

Routes:
  POST /api/telegram           Telegram webhook (checks X-Telegram-Bot-Api-Secret-Token)
  GET  /api/cron               sends due reminders (Vercel Cron: "Authorization: Bearer CRON_SECRET",
                               or ?key=CRON_SECRET for an external scheduler)
  GET  /api/setup?key=...      registers the webhook + bot menu/description (key = WEBHOOK_SECRET)
  GET  /                       health check
"""

import asyncio
import hmac
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from .app import make_bot, make_dispatcher, send_reminders, setup_profile
from .config import Config, load_config
from .db import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


class _State:
    config: Config | None = None
    db: Database | None = None
    dp: Dispatcher | None = None
    bot: Bot | None = None
    bot_loop: asyncio.AbstractEventLoop | None = None


S = _State()


def _init() -> None:
    if S.dp is None:
        S.config = load_config()
        S.db = Database(S.config.database_url)
        S.dp = make_dispatcher(S.db, S.config)


def _bot() -> Bot:
    # The HTTP session is bound to the event loop; recreate it if the loop changed.
    loop = asyncio.get_running_loop()
    if S.bot is None or S.bot_loop is not loop:
        S.bot, S.bot_loop = make_bot(S.config), loop
    return S.bot


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("НейроФинансы bot is running")


async def telegram(request: Request) -> JSONResponse:
    _init()
    if not S.config.webhook_secret:
        return JSONResponse({"error": "WEBHOOK_SECRET is not configured"}, status_code=503)
    if not _same(request.headers.get("x-telegram-bot-api-secret-token", ""), S.config.webhook_secret):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    bot = _bot()
    try:
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await S.dp.feed_update(bot, update)
    except Exception:
        # Always 200: otherwise Telegram keeps re-sending the same update.
        log.exception("Failed to process update")
    return JSONResponse({"ok": True})


async def cron(request: Request) -> JSONResponse:
    _init()
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    supplied = supplied or request.query_params.get("key", "")
    if not _same(supplied, S.config.cron_secret):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    sent = await send_reminders(_bot(), S.db, S.config)
    return JSONResponse({"ok": True, "reminders_sent": sent})


async def setup(request: Request) -> JSONResponse:
    _init()
    if not _same(request.query_params.get("key", ""), S.config.webhook_secret):
        return JSONResponse({"error": "forbidden: pass ?key=<WEBHOOK_SECRET>"}, status_code=403)
    host = request.headers.get("x-forwarded-host") or request.url.hostname
    base = S.config.public_url or f"https://{host}"
    url = f"{base}/api/telegram"
    bot = _bot()
    await S.db.connect()  # creates tables on first run
    await bot.set_webhook(
        url,
        secret_token=S.config.webhook_secret,
        allowed_updates=S.dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    await setup_profile(bot)
    me = await bot.get_me()
    info = await bot.get_webhook_info()
    return JSONResponse({
        "ok": True,
        "bot": f"@{me.username}",
        "webhook": info.url,
        "pending_updates": info.pending_update_count,
        "admin_chat_configured": bool(S.config.admin_chat_id),
    })


app = Starlette(
    routes=[
        Route("/", health),
        Route("/api/telegram", telegram, methods=["POST"]),
        Route("/api/cron", cron, methods=["GET", "POST"]),
        Route("/api/setup", setup, methods=["GET"]),
    ]
)
