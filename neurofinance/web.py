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
import os
from urllib.parse import parse_qsl, urlencode, urlsplit

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from workbot import web as workbot_web

from . import ai_admin, courses_admin, journey_admin, leads, panel
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


async def health(request: Request) -> JSONResponse:
    # Only reports whether settings are present, never their values.
    env = os.environ
    db_url = env.get("DATABASE_URL") or env.get("POSTGRES_URL") or ""
    host = database_host()
    return JSONResponse({
        **({"DANGER": "staging is connected to the LIVE database; all bot actions are blocked"}
           if staging_on_live_database() else {}),
        "app_env": env.get("APP_ENV", ""),
        "bot": "НейроФинансы",
        "environment": env.get("VERCEL_ENV", "local"),
        "branch": env.get("VERCEL_GIT_COMMIT_REF", ""),
        "database_host": host.split(".", 1)[0],
        "catalog_enabled": env.get("CATALOG_ENABLED", "off"),
        "ai_agent": env.get("AI_AGENT", "off"),
        "ANTHROPIC_API_KEY": bool(env.get("ANTHROPIC_API_KEY")),
        "BOT_TOKEN": bool(env.get("BOT_TOKEN")),
        "WEBHOOK_SECRET": bool(env.get("WEBHOOK_SECRET")),
        "CRON_SECRET": bool(env.get("CRON_SECRET")),
        "ADMIN_CHAT_ID": bool(env.get("ADMIN_CHAT_ID")),
        "ADMIN_PASSWORD": bool(env.get("ADMIN_PASSWORD")),
        "database": "postgres" if db_url.startswith("postgres") else "MISSING (add Neon in Storage)",
        "workbot": {
            name: bool(env.get(name))
            for name in ("WORK_BOT_TOKEN", "WORK_WEBHOOK_SECRET", "WORK_CHAT_ID", "TRELLO_KEY", "TRELLO_TOKEN")
        },
    })


async def telegram(request: Request) -> JSONResponse:
    if staging_on_live_database():
        return live_db_refusal()
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
    if staging_on_live_database():
        return live_db_refusal()
    _init()
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    supplied = supplied or request.query_params.get("key", "")
    if not _same(supplied, S.config.cron_secret):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    sent = await send_reminders(_bot(), S.db, S.config)
    return JSONResponse({"ok": True, "reminders_sent": sent})


async def setup(request: Request) -> JSONResponse:
    if staging_on_live_database():
        return live_db_refusal()
    try:
        _init()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"configuration: {exc}"}, status_code=500)
    if not _same(request.query_params.get("key", ""), S.config.webhook_secret):
        return JSONResponse({"error": "forbidden: pass ?key=<WEBHOOK_SECRET>"}, status_code=403)
    try:
        return await _setup(request)
    except Exception as exc:
        log.exception("Setup failed")
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500)


def is_preview() -> bool:
    return os.getenv("VERCEL_ENV", "") == "preview"


def database_host() -> str:
    db_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""
    return (urlsplit(db_url).hostname or "") if db_url.startswith("postgres") else ""


def staging_on_live_database() -> bool:
    """A staging copy must never read or write the live database."""
    if os.getenv("APP_ENV", "").lower() != "staging":
        return False
    live = os.getenv("PRODUCTION_DATABASE_HOST", "ep-withered-dawn-b7wsvhzp").lower()
    return bool(live) and database_host().lower().startswith(live)


def live_db_refusal() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "APP_ENV=staging but DATABASE_URL points at the live database. "
                               "Connect the staging project to its own Neon database."},
        status_code=503,
    )


def production_bot_username() -> str:
    return os.getenv("PRODUCTION_BOT_USERNAME", "neurogoals_bot").lstrip("@").lower()


def webhook_url(base: str) -> str:
    url = f"{base}/api/telegram"
    # Preview deployments sit behind Vercel Deployment Protection; Telegram can only pass
    # the bypass secret in the URL. Vercel exposes it when "Protection Bypass for
    # Automation" is enabled for the project.
    bypass = os.getenv("VERCEL_AUTOMATION_BYPASS_SECRET", "")
    return f"{url}?x-vercel-protection-bypass={bypass}" if bypass else url


async def _setup(request: Request) -> JSONResponse:
    host = request.headers.get("x-forwarded-host") or request.url.hostname
    base = S.config.public_url or f"https://{host}"
    url = webhook_url(base)
    bot = _bot()
    me = await bot.get_me()
    if is_preview() and (me.username or "").lower() == production_bot_username():
        # Never let a staging/preview build take the live bot's webhook.
        return JSONResponse({
            "ok": False,
            "error": f"This is a preview deployment but BOT_TOKEN belongs to @{me.username} (production). "
                     "Set the test bot's token for the Preview environment in Vercel.",
        }, status_code=409)
    await S.db.connect()  # creates tables on first run
    await bot.set_webhook(
        url,
        secret_token=S.config.webhook_secret,
        allowed_updates=S.dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    await setup_profile(bot)
    info = await bot.get_webhook_info()
    return JSONResponse({
        "ok": True,
        "environment": os.getenv("VERCEL_ENV", "local"),
        "bot": f"@{me.username}",
        "webhook": info.url.split("?", 1)[0],
        "pending_updates": info.pending_update_count,
        "admin_chat_configured": bool(S.config.admin_chat_id),
    })


async def not_found(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": "not found", "path": request.scope.get("path"), "root_path": request.scope.get("root_path")},
        status_code=404,
    )


def _panel_config() -> Config:
    _init()
    return S.config


def _panel_db() -> Database:
    _init()
    return S.db


panel.deps.update(config=_panel_config, db=_panel_db, bot=lambda: (_init(), _bot())[1])

_routes = Starlette(
    routes=[
        Route("/", health),
        Route("/api/telegram", telegram, methods=["POST"]),
        Route("/api/cron", cron, methods=["GET", "POST"]),
        Route("/api/setup", setup, methods=["GET"]),
        *ai_admin.routes,
        *journey_admin.routes,
        *courses_admin.routes,
        *leads.routes,
        *panel.routes,
        *workbot_web.routes,
    ],
    exception_handlers={404: not_found},
)


async def app(scope, receive, send):
    """vercel.json rewrites every URL to /api/index?__path=<original path>.

    Route on that parameter so routing doesn't depend on which path the
    platform hands to the function.
    """
    if scope["type"] == "http":
        query = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
        original = next((v for k, v in query if k == "__path"), None)
        if original is not None:
            path = "/" + original.lstrip("/")
            rest = urlencode([(k, v) for k, v in query if k != "__path"])
            scope = dict(scope, path=path, raw_path=path.encode(), root_path="", query_string=rest.encode())
    await _routes(scope, receive, send)
