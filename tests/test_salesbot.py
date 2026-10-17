"""Sales bot (НейроФинансы) tests: whole Telegram updates through the dispatcher against
a fake Telegram API, the webhook/cron/setup endpoints, the admin panel and the leads page.

Runs on SQLite by default; set TEST_DATABASE_URL=postgresql://... to run on Postgres
(CI does both). Run: python -m pytest tests
"""

import asyncio
import datetime
import itertools
import os
import re
import sqlite3
import time

import pytest
from aiogram import Bot, methods
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, File, Message, MessageEntity, MessageId, PhotoSize, Update, User

from neurofinance import admin, agent_bot, content, handlers, journey_bot
from neurofinance import texts as t
from neurofinance.app import make_dispatcher, send_reminders
from neurofinance.config import load_config
from neurofinance.db import Database

ADMIN_ID = 999
STUDENT = User(id=42, is_bot=False, first_name="Анна", username="anna")
ZOE = User(id=ADMIN_ID, is_bot=False, first_name="Zoe", username="ZoeRai")
TABLES = ("users", "relay", "kv", "content", "fsm", "courses", "cohorts", "journey_steps", "waitlist", "ai_chats")

_ids = itertools.count(1000)


class FakeTelegram(BaseSession):
    """Records every Bot API call and returns plausible results."""

    def __init__(self, username: str = "neurogoals_bot"):
        super().__init__()
        self.calls: list = []
        self.username = username

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, methods.GetMe):
            return User(id=1, is_bot=True, first_name="bot", username=self.username)
        if isinstance(method, (methods.SendMessage, methods.SendPhoto, methods.SendVideoNote)):
            extra = {}
            if isinstance(method, methods.SendPhoto):
                extra["photo"] = [PhotoSize(file_id="PHOTO_ID", file_unique_id="u", width=1, height=1)]
            return Message(message_id=next(_ids), date=datetime.datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), **extra)
        if isinstance(method, methods.CopyMessage):
            return MessageId(message_id=next(_ids))
        if isinstance(method, methods.GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="photos/x.jpg")
        if isinstance(method, methods.GetWebhookInfo):
            from aiogram.types import WebhookInfo
            return WebhookInfo(url="https://example/api/telegram", has_custom_certificate=False,
                               pending_update_count=0)
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b"\xff\xd8JPEG"

    # -- helpers for assertions ---------------------------------------------------------

    def sent(self, chat_id: int | None = None) -> list:
        return [m for m in self.calls
                if isinstance(m, (methods.SendMessage, methods.SendPhoto, methods.CopyMessage))
                and (chat_id is None or m.chat_id == chat_id)]

    def texts(self, chat_id: int | None = None) -> list[str]:
        return [getattr(m, "text", None) or getattr(m, "caption", None) or "" for m in self.sent(chat_id)]

    def buttons(self, m) -> list[str]:
        markup = getattr(m, "reply_markup", None)
        return [b.text for row in getattr(markup, "inline_keyboard", None) or [] for b in row]

    def clear(self):
        self.calls.clear()


def _db_url(tmp_path) -> str:
    return os.getenv("TEST_DATABASE_URL") or str(tmp_path / "test.db")


def _reset_routers():
    # Module-level routers join a new dispatcher in every test.
    for router in (admin.router, agent_bot.router, journey_bot.router, handlers.router):
        router._parent_router = None


async def _wipe(db: Database) -> None:
    if db.is_pg:
        handle = await db._get()
        for table in TABLES:
            await handle.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        await db.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    values = {
        "BOT_TOKEN": "123:abc", "ADMIN_CHAT_ID": str(ADMIN_ID), "WEBHOOK_SECRET": "whsec",
        "CRON_SECRET": "crsec", "ADMIN_PASSWORD": "pw", "GROUP_LINK_WEEKEND": "https://t.me/+weekend",
        "DATABASE_URL": _db_url(tmp_path), "MEDIA_PORTRAIT": "", "MEDIA_PROGRAM": "", "MEDIA_VIDEO_NOTE": "",
    }
    for key in ("POSTGRES_URL", "VERCEL_ENV", "VERCEL_AUTOMATION_BYPASS_SECRET", "CATALOG_ENABLED", "PUBLIC_URL", "APP_ENV",
                "AI_AGENT", "AI_MODEL", "AI_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


class Harness:
    def __init__(self, url: str):
        self.loop = asyncio.new_event_loop()
        self.db = Database(url)
        self.run(_wipe(self.db))
        self.config = load_config()
        self.tg = FakeTelegram()
        self.bot = Bot("123:abc", session=self.tg, default=DefaultBotProperties(parse_mode="HTML"))
        _reset_routers()
        self.dp = make_dispatcher(self.db, self.config)
        self.update_ids = itertools.count(1)

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def feed(self, update: Update):
        self.run(self.dp.feed_update(self.bot, update))

    def say(self, user: User, text: str, chat_id: int | None = None, reply_to: int | None = None, **extra):
        chat = Chat(id=chat_id or user.id, type="private")
        data = dict(message_id=next(_ids), date=datetime.datetime.now(), chat=chat, from_user=user, text=text, **extra)
        if text and text.startswith("/"):
            data["entities"] = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
        if reply_to:
            data["reply_to_message"] = Message(message_id=reply_to, date=datetime.datetime.now(), chat=chat, text="x")
        self.feed(Update(update_id=next(self.update_ids), message=Message(**data)))

    def press(self, user: User, data: str, chat_id: int | None = None):
        from aiogram.types import CallbackQuery
        msg = Message(message_id=next(_ids), date=datetime.datetime.now(),
                      chat=Chat(id=chat_id or user.id, type="private"), text="x")
        self.feed(Update(update_id=next(self.update_ids), callback_query=CallbackQuery(
            id=str(next(_ids)), from_user=user, chat_instance="c", data=data, message=msg)))

    def user(self, user_id: int) -> dict:
        return self.run(self.db.get_user(user_id))

    def close(self):
        self.run(self.db.close())
        self.loop.close()


@pytest.fixture
def h(env):
    harness = Harness(env["DATABASE_URL"])
    yield harness
    harness.close()


# -- the student journey ------------------------------------------------------------------


def test_full_purchase_journey(h):
    h.say(STUDENT, "/start instagram")
    first = h.tg.sent(42)[-1]
    assert "НЕЙРОФИНАНСЫ" in h.tg.texts(42)[-1]
    assert h.tg.buttons(first) == ["Понять, подойдёт ли мне", "Посмотреть программу", "Выбрать группу"]

    for data in ("go:step2", "br:unstable", "go:step3", "go:step4", "go:step5", "go:step6", "go:step7"):
        h.press(STUDENT, data)
    h.press(STUDENT, "grp:weekend")
    assert "Группа выходного дня" in h.tg.texts(42)[-1]
    h.press(STUDENT, "cur:eur")
    h.press(STUDENT, "go:methods")
    assert h.tg.buttons(h.tg.sent(42)[-1])[:5] == ["PayPal", "UBS", "TWINT", "Revolut", "Криптовалюта"]
    h.press(STUDENT, "pay:ubs")
    assert "4347 3500 0419 4836" in h.tg.texts(42)[-1] and "230 €" in h.tg.texts(42)[-1]

    h.tg.clear()
    h.press(STUDENT, "paid")
    h.say(STUDENT, "перевела 230 €")
    admin_msgs = h.tg.sent(ADMIN_ID)
    assert "Новая оплата" in admin_msgs[0].text
    assert h.tg.buttons(admin_msgs[0]) == ["✅ Подтвердить оплату", "❌ Платёж не найден"]
    assert isinstance(admin_msgs[1], methods.CopyMessage)  # the receipt itself
    assert h.user(42)["status"] == "pending"

    h.press(ZOE, "adm:ok:42", chat_id=ADMIN_ID)
    welcome = h.tg.sent(42)[-1]
    assert "Вы внутри" in welcome.text and h.tg.buttons(welcome) == ["ПЕРЕЙТИ В ГРУППУ"]

    lead = h.user(42)
    assert lead["status"] == "paid" and lead["furthest_step"] == 11
    assert lead["source"] == "instagram" and lead["situation"] == "unstable"

    h.say(STUDENT, "/start facebook")
    assert h.user(42)["source"] == "instagram"  # first touch wins


def test_rub_methods_and_bad_source(h):
    h.say(STUDENT, "/start bad<script>")
    assert not h.user(42)["source"]
    h.press(STUDENT, "grp:weekday")
    h.press(STUDENT, "cur:rub")
    h.press(STUDENT, "go:methods")
    assert h.tg.buttons(h.tg.sent(42)[-1])[:2] == ["Т-Банк (Тинькофф)", "Криптовалюта"]


def test_non_admin_cannot_confirm(h):
    h.say(STUDENT, "/start")
    h.press(STUDENT, "adm:ok:42")
    assert h.user(42)["status"] == "new"


def test_question_relay(h):
    h.say(STUDENT, "/start")
    h.press(STUDENT, "go:ask")
    h.tg.clear()
    h.say(STUDENT, "Будут ли записи?")
    header = h.tg.sent(ADMIN_ID)[0]
    assert "Вопрос" in header.text
    relay_id = h.run(h.db.fetch("SELECT admin_msg_id FROM relay ORDER BY admin_msg_id"))[0]["admin_msg_id"]
    h.tg.clear()
    h.say(ZOE, "Да, будут", chat_id=ADMIN_ID, reply_to=relay_id)
    copy = h.tg.sent(42)[0]
    assert isinstance(copy, methods.CopyMessage) and copy.from_chat_id == ADMIN_ID


def test_input_state_survives_new_dispatcher(h):
    """Serverless: the next update may hit a fresh process."""
    h.say(STUDENT, "/start")
    h.press(STUDENT, "paid")
    _reset_routers()
    h.dp = make_dispatcher(h.db, h.config)  # new process: FSM state must come from the database
    h.tg.clear()
    h.say(STUDENT, "чек")
    assert "Новая оплата" in h.tg.sent(ADMIN_ID)[0].text


def test_reminder_once_and_closing_once(h):
    h.say(STUDENT, "/start")
    h.press(STUDENT, "grp:weekend")
    h.run(h.db.execute("UPDATE users SET group_chosen_at=? WHERE user_id=42", time.time() - 21 * 3600))
    assert h.run(send_reminders(h.bot, h.db, h.config)) == 1
    assert h.run(send_reminders(h.bot, h.db, h.config)) == 0

    h.tg.clear()
    h.press(ZOE, "adm:closing:yes", chat_id=ADMIN_ID)
    h.press(ZOE, "adm:closing:yes", chat_id=ADMIN_ID)  # retried webhook / double click
    assert sum("Запись на «НейроФинансы» заканчивается" in x for x in h.tg.texts(42)) == 1


def test_faq_appears_when_answer_is_filled(h):
    h.say(STUDENT, "/start")
    h.press(STUDENT, "go:faq")
    assert "Будут ли записи?" not in h.tg.buttons(h.tg.sent(42)[-1])
    h.run(h.db.content_set("faq_6", "<b>Будут ли записи?</b>\n\nДа.", None))
    h.press(STUDENT, "go:faq")
    assert "Будут ли записи?" in h.tg.buttons(h.tg.sent(42)[-1])


def test_post_photo_and_caption_split(h):
    h.run(h.db.content_set("step1", None, "PIC"))
    h.say(STUDENT, "/start")
    first = h.tg.sent(42)[-1]
    assert isinstance(first, methods.SendPhoto) and first.caption and h.tg.buttons(first)
    h.run(h.db.content_set("step5", None, "PIC2"))  # program text > 1024 chars
    h.tg.clear()
    h.press(STUDENT, "go:step5")
    photo, text = h.tg.sent(42)
    assert isinstance(photo, methods.SendPhoto) and not photo.caption and h.tg.buttons(text)


# -- content conversion -----------------------------------------------------------------


def test_editor_roundtrip_keeps_every_default():
    for key in content.CATALOG:
        assert content.editor_to_telegram(content.telegram_to_editor(content.CATALOG[key].default)) == \
            content.normalized_default(key)


def test_editor_html_is_sanitised():
    out = content.editor_to_telegram(
        '<p><strong>b</strong> <a href="javascript:alert(1)">x</a><script>alert(1)</script>'
        '<img src=x onerror=alert(1)></p><blockquote>q1</blockquote><blockquote>q2</blockquote>'
    )
    assert out == "<b>b</b> x\n<blockquote>q1\nq2</blockquote>"


# -- migration ----------------------------------------------------------------------------


def test_old_sqlite_database_gains_lead_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (user_id BIGINT PRIMARY KEY, username TEXT, full_name TEXT, "
                "created_at DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'new', group_key TEXT, "
                "currency TEXT, method TEXT, group_chosen_at DOUBLE PRECISION, "
                "reminder_sent INTEGER NOT NULL DEFAULT 0, closing_sent INTEGER NOT NULL DEFAULT 0, "
                "blocked INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO users (user_id, full_name, created_at, status) VALUES (7, 'Old', 1, 'paid')")
    con.commit()
    con.close()

    async def check():
        db = Database(str(path))
        row = await db.get_user(7)
        await db.close()
        return row

    loop = asyncio.new_event_loop()
    row = loop.run_until_complete(check())
    loop.close()
    assert row["status"] == "paid" and row["furthest_step"] == 0 and row["source"] is None


# -- web endpoints, admin panel, leads ----------------------------------------------------


@pytest.fixture
def web(env, monkeypatch):
    from starlette.testclient import TestClient

    import neurofinance.web as webmod

    tg = FakeTelegram()
    original = webmod.make_bot
    monkeypatch.setattr(webmod, "make_bot", lambda config: original(config, session=tg))
    for name in ("config", "db", "dp", "bot", "bot_loop"):
        setattr(webmod.S, name, None)
    _reset_routers()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_wipe(Database(env["DATABASE_URL"])))
    loop.close()
    with TestClient(webmod.app, base_url="https://testserver") as client:
        client.tg = tg
        yield client
        if webmod.S.db is not None:
            client.portal.call(webmod.S.db.close)  # an open aiosqlite connection keeps the process alive


def _update(user_id: int, text: str) -> dict:
    return {"update_id": next(_ids), "message": {
        "message_id": next(_ids), "date": 0, "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "T"}, "text": text,
        "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text[0] == "/" else [],
    }}


def test_webhook_requires_secret_and_tolerates_garbage(web):
    assert web.post("/api/telegram", json=_update(5, "/start")).status_code == 403
    ok = web.post("/api/telegram", json=_update(5, "/start"), headers={"X-Telegram-Bot-Api-Secret-Token": "whsec"})
    assert ok.status_code == 200 and web.tg.sent(5)
    garbage = web.post("/api/telegram", content=b"nope", headers={"X-Telegram-Bot-Api-Secret-Token": "whsec"})
    assert garbage.status_code == 200  # never make Telegram retry forever


def test_cron_auth(web):
    assert web.get("/api/cron").status_code == 403
    assert web.get("/api/cron", headers={"Authorization": "Bearer crsec"}).json()["ok"]
    assert web.get("/api/cron?key=crsec").json()["ok"]


def test_setup_registers_webhook(web):
    assert web.get("/api/setup?key=wrong").status_code == 403
    r = web.get("/api/setup?key=whsec", headers={"host": "neuro-goals.vercel.app"})
    assert r.json()["ok"]
    hook = next(m for m in web.tg.calls if isinstance(m, methods.SetWebhook))
    assert hook.url == "https://neuro-goals.vercel.app/api/telegram" and hook.secret_token == "whsec"


def test_preview_cannot_take_production_bot(web, monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    r = web.get("/api/setup?key=whsec")
    assert r.status_code == 409 and not any(isinstance(m, methods.SetWebhook) for m in web.tg.calls)


def test_preview_with_test_bot_and_protection_bypass(web, monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_AUTOMATION_BYPASS_SECRET", "bypass123")
    web.tg.username = "neurogoals_master_bot"
    r = web.get("/api/setup?key=whsec", headers={"host": "staging.vercel.app"})
    assert r.json()["ok"] and r.json()["environment"] == "preview"
    hook = next(m for m in web.tg.calls if isinstance(m, methods.SetWebhook))
    assert hook.url == "https://staging.vercel.app/api/telegram?x-vercel-protection-bypass=bypass123"
    # The bypass parameter must not break webhook routing.
    ok = web.post("/api/telegram?x-vercel-protection-bypass=bypass123", json=_update(6, "/start"),
                  headers={"X-Telegram-Bot-Api-Secret-Token": "whsec"})
    assert ok.status_code == 200 and web.tg.sent(6)


def _login(web):
    assert web.post("/admin/login", data={"password": "nope"}, follow_redirects=False).headers["location"] \
        .startswith("/admin/login?err=")
    assert web.post("/admin/login", data={"password": "pw"}, follow_redirects=False).headers["location"] == "/admin"


def test_admin_panel_edit_photo_preview_reset(web):
    assert web.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    _login(web)
    page = web.get("/admin").text
    assert page.count('href="/admin/edit/') == len(content.CATALOG)

    edited = "<p>Отлично! <strong>{group}</strong></p><p>{dates}</p>"
    assert "msg=" in web.post("/admin/edit/step8", data={"html": edited, "action": "save"},
                              follow_redirects=False).headers["location"]
    r = web.post("/admin/edit/step1", data={"html": content.telegram_to_editor(t.STEP1), "action": "test"},
                 files={"photo": ("p.jpg", b"\xff\xd8x", "image/jpeg")}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    previews = [m for m in web.tg.calls if isinstance(m, methods.SendPhoto) and m.caption]
    assert previews and previews[-1].chat_id == ADMIN_ID
    assert web.get("/admin/photo/step1").content.startswith(b"\xff\xd8")

    assert "err=" in web.post("/admin/edit/step2", data={"html": "<p><br></p>", "action": "save"},
                              follow_redirects=False).headers["location"]
    web.post("/admin/edit/step8", data={"action": "reset"})
    assert web.get("/admin").text.count("изменено") == 0

    web.get("/admin/logout")
    assert web.get("/admin/edit/step1", follow_redirects=False).status_code == 303


def test_leads_page_filters_notes_and_csv(web):
    hdr = {"X-Telegram-Bot-Api-Secret-Token": "whsec"}
    for user_id, text in ((101, "/start instagram"), (102, "/start webinar"), (ADMIN_ID, "/start")):
        web.post("/api/telegram", json=_update(user_id, text), headers=hdr)
    _login(web)
    page = web.get("/admin/leads").text
    assert re.search(r'<b>2</b><span>всего', page)  # the manager is not a lead
    assert len(re.findall('class="lead"', web.get("/admin/leads?source=instagram").text)) == 1

    web.post("/admin/leads/101", data={"notes": "=спросить про рассрочку"})
    assert len(re.findall('class="lead"', web.get("/admin/leads?q=рассрочк").text)) == 1
    csv = web.get("/admin/leads.csv").content.decode("utf-8-sig")
    assert csv.splitlines()[0].startswith("ID;Имя;Username")
    assert "'=спросить про рассрочку" in csv  # spreadsheet formulas neutralised

    web.get("/admin/logout")
    assert web.get("/admin/leads.csv").status_code == 403


def test_staging_refuses_live_database(web, monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-withered-dawn-b7wsvhzp-pooler.eu.aws.neon.tech/db")
    hdr = {"X-Telegram-Bot-Api-Secret-Token": "whsec"}
    assert web.post("/api/telegram", json=_update(7, "/start"), headers=hdr).status_code == 503
    assert web.get("/api/cron?key=crsec").status_code == 503
    assert web.get("/api/setup?key=whsec").status_code == 503
    assert "DANGER" in web.get("/").json()
    assert not web.tg.calls  # nothing was sent anywhere


def test_staging_guard_ignores_other_databases(monkeypatch):
    import neurofinance.web as webmod

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-other-staging-123-pooler.neon.tech/db")
    assert not webmod.staging_on_live_database()
    monkeypatch.setenv("APP_ENV", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-withered-dawn-b7wsvhzp-pooler.neon.tech/db")
    assert not webmod.staging_on_live_database()  # production itself is fine
