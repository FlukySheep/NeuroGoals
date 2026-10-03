"""Work bot tests: the parser, and whole Telegram updates run through the dispatcher
against an in-memory Trello and a fake Telegram API. Run: python -m pytest tests"""

import asyncio
import itertools
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, GetChatMember, SendMessage, SetMessageReaction,
)
from aiogram.types import Chat, ChatMemberMember, ChatMemberOwner, Message, Update, User

from workbot import handlers
from workbot.app import make_dispatcher
from workbot.config import WorkConfig
from workbot.db import WorkDatabase
from workbot.parse import card_ref, parse
from workbot.trello import TrelloError

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 3, 20, 15, tzinfo=TZ)  # Saturday evening


# -- parser -----------------------------------------------------------------------


def test_parse_full_line():
    p = parse("Обновить лендинг @anna !срочно до пятницы 15:00 #маркетинг\n- цены\n- фото\nСм. макет", NOW)
    assert p.title == "Обновить лендинг"
    assert p.mentions == ["anna"] and p.priority == "high" and p.tags == ["маркетинг"]
    assert p.due == datetime(2026, 10, 9, 15, 0, tzinfo=TZ)
    assert p.checklist == ["цены", "фото"] and p.description == ["См. макет"]


@pytest.mark.parametrize("text,due", [
    ("Позвонить завтра", datetime(2026, 10, 4, 18, 0, tzinfo=TZ)),
    ("Вебинар 12.10 в 19:30", datetime(2026, 10, 12, 19, 30, tzinfo=TZ)),
    ("Созвон в 10:00", datetime(2026, 10, 4, 10, 0, tzinfo=TZ)),          # 10:00 passed today -> tomorrow
    ("Встреча в субботу", datetime(2026, 10, 10, 18, 0, tzinfo=TZ)),      # today's 18:00 passed -> next week
    ("Отчёт через 3 дня", datetime(2026, 10, 6, 18, 0, tzinfo=TZ)),
    ("Сдать через 2 часа", datetime(2026, 10, 3, 22, 15, tzinfo=TZ)),
    ("Оплата 05.01", datetime(2027, 1, 5, 18, 0, tzinfo=TZ)),             # past date this year -> next year
    ("Починить сегодня", datetime(2026, 10, 3, 23, 59, tzinfo=TZ)),      # after 18:00 -> end of day
])
def test_parse_dates(text, due):
    assert parse(text, NOW).due == due


@pytest.mark.parametrize("text", ["Купить 1.5 литра", "Обновить прайс на 2026", "Оплата до 31.02"])
def test_parse_no_false_dates(text):
    p = parse(text, NOW)
    assert p.due is None and p.title == text


def test_parse_me_and_card_ref():
    assert parse("Отчёт @я", NOW).mentions == ["me"]
    assert card_ref("#42 в работе") == (42, "в работе")
    assert card_ref("в работе") == (None, "в работе")


# -- fakes ------------------------------------------------------------------------------


class FakeTrello:
    configured = True

    def __init__(self):
        self.ids = itertools.count(1)
        self.cards: dict[str, dict] = {}
        self.comments: list[tuple[str, str]] = []
        self.board_members_ids: set[str] = set()
        self.people = {"anna_t": {"id": "m-anna", "username": "anna_t", "fullName": "Anna T"},
                       "ivan_t": {"id": "m-ivan", "username": "ivan_t", "fullName": "Ivan T"}}
        self.labels: dict[str, str] = {}

    def _id(self, prefix):
        return f"{prefix}{next(self.ids)}"

    async def me(self):
        return {"id": "m-bot", "username": "bot", "fullName": "Bot"}

    async def member(self, username):
        if username not in self.people:
            raise TrelloError(404, "member not found")
        return self.people[username]

    async def create_board(self, name, workspace=""):
        return {"id": "b1", "name": name, "shortUrl": "https://trello.com/b/x"}

    async def create_list(self, board_id, name, pos):
        return {"id": self._id("l")}

    async def create_label(self, board_id, name, color):
        label_id = self._id("lb")
        self.labels[label_id] = name
        return {"id": label_id}

    async def board_members(self, board_id):
        return [p for p in self.people.values() if p["id"] in self.board_members_ids]

    async def board_add_member(self, board_id, member_id):
        self.board_members_ids.add(member_id)

    async def board_cards(self, board_id):
        return [dict(c) for c in self.cards.values() if not c["closed"]]

    def _find(self, ref):
        for c in self.cards.values():
            if ref in (c["id"], c["shortLink"]):
                return c
        raise TrelloError(404, "card not found")

    async def board_card(self, board_id, number):
        for c in self.cards.values():
            if c["idShort"] == number:
                return {"id": c["id"]}
        raise TrelloError(404, "card not found")

    async def card(self, ref):
        c = dict(self._find(ref))
        c["labels"] = [{"id": i, "name": self.labels.get(i, "")} for i in c["idLabels"]]
        c["members"] = [p for p in self.people.values() if p["id"] in c["idMembers"]]
        return c

    async def create_card(self, **f):
        n = len(self.cards) + 1
        card = {"id": f"c{n}", "shortLink": f"s{n}", "shortUrl": f"https://trello.com/c/s{n}", "idShort": n,
                "name": f["name"], "desc": f.get("desc", ""), "idList": f["idList"],
                "idLabels": [x for x in f.get("idLabels", "").split(",") if x],
                "idMembers": [x for x in f.get("idMembers", "").split(",") if x],
                "due": f.get("due"), "dueComplete": False, "closed": False, "badges": {}}
        if "dueReminder" in f:
            card["dueReminder"] = f["dueReminder"]
        self.cards[card["id"]] = card
        return dict(card)

    async def update_card(self, ref, **f):
        self._find(ref).update(f)
        return dict(self._find(ref))

    async def delete_card(self, ref):
        del self.cards[self._find(ref)["id"]]

    async def add_card_member(self, ref, member_id):
        card = self._find(ref)
        if member_id not in card["idMembers"]:
            card["idMembers"].append(member_id)

    async def remove_card_member(self, ref, member_id):
        self._find(ref)["idMembers"].remove(member_id)

    async def comment(self, ref, text):
        self.comments.append((self._find(ref)["id"], text))

    async def add_checklist(self, ref, name, items):
        self._find(ref)["badges"] = {"checkItems": len(items), "checkItemsChecked": 0}


class FakeSession(BaseSession):
    """Records Bot API calls and answers them like Telegram would."""

    def __init__(self, admins: set[int]):
        super().__init__()
        self.calls: list = []
        self.admins = admins
        self.msg_ids = itertools.count(1000)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=next(self.msg_ids), date=NOW, text=method.text,
                           chat=Chat(id=method.chat_id, type="supergroup"), from_user=BOT_USER)
        if isinstance(method, EditMessageText):
            return Message(message_id=method.message_id, date=NOW, text=method.text,
                           chat=Chat(id=method.chat_id, type="supergroup"))
        if isinstance(method, GetChatMember):
            user = User(id=method.user_id, is_bot=False, first_name="x")
            if method.user_id in self.admins:
                return ChatMemberOwner(user=user, is_anonymous=False)
            return ChatMemberMember(user=user)
        if isinstance(method, (AnswerCallbackQuery, SetMessageReaction, EditMessageReplyMarkup)):
            return True
        raise AssertionError(f"unexpected call {type(method).__name__}")

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    def sent(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, (SendMessage, EditMessageText))]


GROUP = -1001234567890
BOT_USER = User(id=42, is_bot=True, first_name="WorkBot", username="work_bot")
ANNA = User(id=1, is_bot=False, first_name="Anna", username="anna")
IVAN = User(id=2, is_bot=False, first_name="Ivan", username="ivan")


class Harness:
    def __init__(self, tmp_path):
        self.config = WorkConfig(
            bot_token="42:TEST", chat_ids=frozenset({GROUP}), admin_ids=frozenset(), trello_key="k",
            trello_token="t", trello_workspace="", tz=TZ, database_url=str(tmp_path / "w.db"),
            webhook_secret="s", public_url="",
        )
        self.db = WorkDatabase(self.config.database_url)
        self.dp = make_dispatcher(self.db, self.config)
        self.trello = FakeTrello()
        self.dp["trello"] = self.trello
        self.session = FakeSession(admins={ANNA.id})
        self.bot = Bot(self.config.bot_token, session=self.session)
        self.updates = itertools.count(1)
        self.msgs = itertools.count(1)

    async def say(self, user: User, text: str, reply_to: int | None = None, reply_from: User = BOT_USER,
                  chat_id: int = GROUP) -> int:
        chat = Chat(id=chat_id, type="supergroup" if chat_id < 0 else "private")
        data = {"message_id": next(self.msgs), "date": NOW, "chat": chat, "from_user": user, "text": text}
        if text.startswith("/"):
            data["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if reply_to:
            data["reply_to_message"] = Message(message_id=reply_to, date=NOW, chat=chat, from_user=reply_from,
                                               text="original message text")
        await self.dp.feed_update(self.bot, Update(update_id=next(self.updates), message=Message(**data)))
        return data["message_id"]

    async def press(self, user: User, data: str, message_id: int):
        message = {"message_id": message_id, "date": NOW, "chat": {"id": GROUP, "type": "supergroup"},
                   "from": BOT_USER.model_dump(), "text": "card"}
        cb = {"id": str(next(self.updates)), "from": user.model_dump(), "chat_instance": "x", "data": data,
              "message": message}
        await self.dp.feed_update(self.bot, Update.model_validate(
            {"update_id": next(self.updates), "callback_query": cb}, context={"bot": self.bot}))

    def last(self) -> str:
        return self.session.sent()[-1]

    def card_message(self, short_link: str) -> int:
        """Id of the latest bot message showing this card."""
        sent = [c for c in self.session.calls if isinstance(c, SendMessage)]
        ids = range(1000, 1000 + len(sent))
        return max(m for m in ids if run(self.db.msg_card(GROUP, m)) == short_link)


LOOP: asyncio.AbstractEventLoop | None = None


def run(coro):
    return LOOP.run_until_complete(coro)


@pytest.fixture
def h(tmp_path, monkeypatch):
    global LOOP
    monkeypatch.setattr(handlers, "now_in", lambda config: NOW)
    handlers.router._parent_router = None  # the module-level router joins a new dispatcher per test
    LOOP = asyncio.new_event_loop()
    harness = Harness(tmp_path)
    yield harness
    run(harness.db.close())  # an open aiosqlite connection keeps the process alive
    LOOP.close()


# -- flows --------------------------------------------------------------------------------


def test_full_flow(h):
    run(h.say(IVAN, "/setup"))
    assert "только админ" in h.last()
    run(h.say(ANNA, "/task Без доски"))
    assert "Доска ещё не создана" in h.last()

    run(h.say(ANNA, "/setup"))
    assert "Доска создана" in h.last()
    run(h.say(ANNA, "/link anna_t"))
    assert "Anna T" in h.last()
    run(h.say(IVAN, "/link nobody_here"))
    assert "нет пользователя" in h.last()
    run(h.say(IVAN, "/link ivan_t"))

    run(h.say(ANNA, "/task Обновить лендинг @ivan @ghost !срочно до пятницы #маркетинг\n- цены\n- фото"))
    card = h.trello.cards["c1"]
    assert card["name"] == "Обновить лендинг" and card["idMembers"] == ["m-ivan"]
    assert card["due"].startswith("2026-10-09T18:00")
    texts = h.session.sent()
    assert "#1 · Обновить лендинг" in texts[-2] and "🔴 высокий" in texts[-2] and "#маркетинг" in texts[-2]
    assert "☑️ 0/2" in texts[-2]
    assert "@ghost" in texts[-1]  # not linked warning

    run(h.say(IVAN, "/move 1 в работе"))
    assert "🔄 В работе" in h.last()
    run(h.say(IVAN, "/due 1 завтра 12:00"))
    assert card["due"].startswith("2026-10-04T12:00")
    run(h.say(IVAN, "/list мои"))
    assert "Обновить лендинг" in h.last()
    run(h.say(ANNA, "/list мои"))
    assert "Ничего нет" in h.last()

    # Reply to the bot's card message: plain text is a comment, /done acts on that card.
    card_msg = h.card_message("s1")
    run(h.say(ANNA, "Цены согласованы", reply_to=card_msg))
    assert h.trello.comments and "Цены согласованы" in h.trello.comments[-1][1]
    run(h.say(ANNA, "/done", reply_to=card_msg))
    assert card["dueComplete"] is True and "Готово" in h.last()


def test_create_from_reply_event_and_buttons(h):
    run(h.say(ANNA, "/setup"))
    run(h.say(ANNA, "/link anna_t"))
    src = run(h.say(IVAN, "hello"))
    run(h.say(ANNA, "/note", reply_to=src, reply_from=IVAN))
    assert h.trello.cards["c1"]["name"] == "original message text"

    run(h.say(ANNA, "/event Вебинар 12.10 19:00"))
    event = h.trello.cards["c2"]
    assert event["due"].startswith("2026-10-12T19:00") and event["dueReminder"] == 60
    msg = h.card_message("s2")

    run(h.press(ANNA, "w:me:s2", msg))
    assert event["idMembers"] == ["m-anna"]
    run(h.press(ANNA, "w:dd:s2:none", msg))
    assert event["due"] is None
    run(h.press(ANNA, "w:mv:s2:review", msg))
    assert "На проверке" in h.last()
    run(h.press(ANNA, "w:ay:s2", msg))
    assert event["closed"] is True
    run(h.say(ANNA, "/restore 2"))
    assert event["closed"] is False
    run(h.press(IVAN, "w:ry:s2", msg))           # not an admin: refused
    assert "c2" in h.trello.cards
    run(h.press(ANNA, "w:ry:s2", msg))
    assert "c2" not in h.trello.cards and "удалена навсегда" in h.last()


def test_access(h):
    run(h.say(ANNA, "/task чужая группа", chat_id=-100999))
    assert h.session.sent() == []
    run(h.say(ANNA, "/chatid", chat_id=-100999))
    assert "-100999" in h.last()
    run(h.say(IVAN, "/list", chat_id=IVAN.id))  # private, not linked
    assert "Добавьте меня в рабочую группу" in h.last()
