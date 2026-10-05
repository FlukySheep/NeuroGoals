"""Editable bot messages ("posts").

Every message users see is a post: Telegram-HTML text plus an optional photo.
Defaults come from texts.py; the admin panel (/admin) stores overrides in the
`content` table. Texts may contain variables like {group} that are filled in
when the message is sent.
"""

import html as _html
import re
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message

from . import media
from . import texts as t
from .config import Config
from .db import Database

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


@dataclass(frozen=True)
class PostDef:
    key: str
    title: str
    section: str
    default: str
    variables: tuple[str, ...] = ()
    # Media slot used when the post has no photo of its own (photos uploaded via the bot).
    fallback_slot: str | None = None
    note: str = ""


@dataclass
class Post:
    html: str
    photo: str | None = None  # Telegram file_id
    extra: dict = field(default_factory=dict)


VARIABLE_HELP = {
    "group": "название группы",
    "dates": "даты группы (4 строки)",
    "time": "время встреч",
    "amount": "сумма к оплате (230 € или 23 000 ₽)",
    "manager": "ник менеджера",
    "group_access": "фраза про ссылку на группу (есть ссылка / пришлёт менеджер)",
}


def _method_default(m: t.PaymentMethod) -> str:
    return (
        f"<b>{m.label}</b>\n\n"
        "Сумма: <b>{amount}</b>\nГруппа: {group}\n\n"
        f"{m.details}\n\n"
        "После оплаты нажмите «Я оплатил(а)» и пришлите скриншот или квитанцию — "
        "мы проверим платёж и откроем доступ к группе."
    )


def _build_catalog() -> dict[str, PostDef]:
    defs: list[PostDef] = [
        PostDef("step1", "Шаг 1 · Первый экран", "Начало", t.STEP1, fallback_slot="portrait",
                note="Картинка: портрет Зои. Если текст длиннее 1024 символов, картинка уйдёт отдельным сообщением."),
        PostDef("step2", "Шаг 2 · Начнём с вас", "Начало", t.STEP2),
    ]
    for key, (label, text) in t.BRANCHES.items():
        defs.append(PostDef(f"branch_{key}", f"Ситуация · {label}", "Ситуации", text))
    defs += [
        PostDef("step3", "Шаг 3 · Что такое финансовый сценарий", "Объяснение", t.STEP3),
        PostDef("step4", "Шаг 4 · Что будет меняться", "Объяснение", t.STEP4),
        PostDef("step5", "Шаг 5 · Программа 4 дней", "Программа", "\n\n➖➖➖\n\n".join(t.STEP5_DAYS),
                fallback_slot="program", note="Картинка: карточка «4 дня — от сценария к действию»."),
        PostDef("doubt", "Ветка «Пока сомневаюсь»", "Программа", t.DOUBT),
        PostDef("step6", "Шаг 6 · Текст под кружочком", "Зоя", t.STEP6,
                note="Сам кружочек Зоя отправляет боту видеосообщением (см. /media)."),
        PostDef("step6_fallback", "Шаг 6 · Текст вместо кружочка", "Зоя", t.STEP6_FALLBACK,
                note="Показывается, только пока кружочек не загружен."),
        PostDef("step7", "Шаг 7 · Формат, даты и цена", "Запись", t.STEP7),
        PostDef("choose_group", "Выбор группы (меню «Записаться»)", "Запись", t.CHOOSE_GROUP),
        PostDef("price", "Стоимость (меню)", "Запись", t.PRICE_TEXT),
        PostDef("step8", "Шаг 8 · Выбор валюты", "Запись",
                "Вы выбрали:\n\n<b>{group}</b>\n{dates}\n\nОсталось выбрать удобный способ оплаты.",
                ("group", "dates")),
        PostDef("step9", "Шаг 9 · Перед оплатой", "Запись", t.STEP9),
        PostDef("payment_methods", "Способы оплаты · список", "Оплата",
                "<b>Сумма к оплате: {amount}</b>\n\n"
                "Выберите способ оплаты — я покажу реквизиты.\n\n"
                "Если ни один способ не подходит, нажмите «Связаться с менеджером».",
                ("amount",)),
    ]
    for m in t.PAYMENT_METHODS.values():
        defs.append(PostDef(f"method_{m.key}", f"Способ оплаты · {m.label}", "Оплата",
                            _method_default(m), ("amount", "group")))
    defs += [
        PostDef("ask_receipt", "После «Я оплатил(а)»", "Оплата", t.ASK_RECEIPT),
        PostDef("receipt_received", "Квитанция получена", "Оплата", t.RECEIPT_RECEIVED),
        PostDef("payment_rejected", "Платёж не найден", "Оплата", t.PAYMENT_REJECTED),
        PostDef("contact_manager", "Связаться с менеджером", "Оплата",
                t.CONTACT_MANAGER_USER, ("manager",)),
        PostDef("step10", "Шаг 10 · Вы внутри", "Оплата",
                "<b>Вы внутри.</b>\n\nДобро пожаловать на «НейроФинансы» 🖤\n\nВы выбрали:\n\n"
                "<b>{group}</b>\n{dates}\n\n{time}\n\n{group_access}\n\n"
                "Там появятся:\n\n— инструкция по подготовке;\n— рабочие материалы;\n"
                "— предварительное задание;\n— ссылки на онлайн-встречи.\n\nДо встречи.\n\n"
                "Будет честно, местами неприятно, иногда смешно и, надеюсь, очень важно.",
                ("group", "dates", "time", "group_access")),
        PostDef("ask_question", "Задать вопрос", "Вопросы", t.ASK_QUESTION),
        PostDef("question_sent", "Вопрос отправлен", "Вопросы", t.QUESTION_SENT),
        PostDef("admin_unavailable", "Менеджер не подключён", "Вопросы", t.ADMIN_UNAVAILABLE, ("manager",)),
        PostDef("faq", "Частые вопросы · заголовок", "Частые вопросы", t.FAQ_TITLE),
    ]
    for i, (question, answer) in enumerate(t.FAQ):
        defs.append(PostDef(
            f"faq_{i}", f"FAQ · {question}", "Частые вопросы",
            f"<b>{question}</b>\n\n{answer}" if answer else "",
            note="" if answer else "Пока пусто — вопрос скрыт в боте. Заполните, чтобы он появился.",
        ))
    defs += [
        PostDef("reminder", "Напоминание тем, кто не оплатил", "Рассылки", t.REMINDER,
                note="Уходит один раз, через 20 часов после выбора группы."),
        PostDef("closing", "Сообщение перед закрытием записи", "Рассылки", t.CLOSING,
                note="Отправляется командой /closing."),
    ]
    return {d.key: d for d in defs}


CATALOG = _build_catalog()


# Funnel stages for the lead database: the furthest one a person reached.
STAGES = {
    1: "Открыл бота",
    2: "Ответил, что за ситуация",
    3: "Финансовый сценарий",
    4: "Что будет меняться",
    5: "Смотрел программу",
    6: "Смотрел видео Зои",
    7: "Смотрел формат и цену",
    8: "Выбрал группу",
    9: "Смотрел способы оплаты",
    10: "Отправил чек",
    11: "Оплатил",
}

_POST_STAGE = {
    "step1": 1, "step2": 2, "step3": 3, "step4": 4, "step5": 5, "doubt": 5,
    "step6": 6, "step6_fallback": 6, "step7": 7, "choose_group": 7, "price": 7,
    "step8": 8, "step9": 9, "payment_methods": 9, "ask_receipt": 9,
    "receipt_received": 10, "step10": 11,
}


def stage_of(key: str) -> int:
    if key.startswith("branch_"):
        return 2
    if key.startswith("method_"):
        return 9
    return _POST_STAGE.get(key, 0)


# ---------------------------------------------------------------------------
# Rendering and sending
# ---------------------------------------------------------------------------


def visible_length(html: str) -> int:
    """Length Telegram counts for limits: text without tags, entities decoded."""
    return len(_html.unescape(re.sub(r"<[^>]+>", "", html)))


def fill(html: str, variables: dict[str, str]) -> str:
    for name, value in variables.items():
        html = html.replace("{" + name + "}", _html.escape(str(value), quote=False))
    return html


async def load(db: Database, key: str) -> Post:
    d = CATALOG[key]
    row = await db.content_get(key)
    text = row["html"] if row and row["html"] is not None else d.default
    photo = row["photo"] if row else None
    return Post(html=text, photo=photo)


async def render(db: Database, key: str, **variables: str) -> Post:
    post = await load(db, key)
    post.html = fill(post.html, variables)
    return post


async def send_rendered(
    bot: Bot,
    chat_id: int,
    post: Post,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    photo_source=None,
) -> Message | None:
    photo = post.photo or photo_source
    text = post.html.strip()
    if photo is not None:
        if text and visible_length(text) <= CAPTION_LIMIT:
            return await bot.send_photo(chat_id, photo, caption=text, reply_markup=reply_markup)
        sent = await bot.send_photo(chat_id, photo, reply_markup=None if text else reply_markup)
        if not text:
            return sent
    if not text:
        return None
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)


async def send(
    bot: Bot,
    chat_id: int,
    db: Database,
    key: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    config: Config | None = None,
    *,
    track: bool = True,
    **variables: str,
) -> Message | None:
    if track and chat_id > 0 and stage_of(key):
        await db.bump_step(chat_id, stage_of(key))
    post = await render(db, key, **variables)
    fallback = None
    slot = CATALOG[key].fallback_slot
    if not post.photo and slot and config is not None:
        fallback = await media.photo_source(slot, config, db)
    msg = await send_rendered(bot, chat_id, post, reply_markup, photo_source=fallback)
    if fallback is not None and msg is not None and not isinstance(fallback, str):
        await media.remember_upload(slot, config, db, msg)
    return msg


async def faq_visible(db: Database) -> list[tuple[int, str]]:
    """(index, question) of FAQ entries that currently have an answer."""
    rows = {r["key"]: r for r in await db.content_all()}
    visible = []
    for i, (question, _) in enumerate(t.FAQ):
        key = f"faq_{i}"
        row = rows.get(key)
        text = row["html"] if row and row["html"] is not None else CATALOG[key].default
        if (text or "").strip():
            visible.append((i, question))
    return visible


# ---------------------------------------------------------------------------
# Editor <-> Telegram HTML
# ---------------------------------------------------------------------------

from html.parser import HTMLParser  # noqa: E402

_INLINE = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "ins": "u",
           "s": "s", "strike": "s", "del": "s", "code": "code"}
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
_SAFE_SCHEMES = ("http://", "https://", "tg://", "mailto:")


class _EditorToTelegram(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []  # (kind, html)
        self.cur: list[str] = []
        self.kind = "p"
        self.stack: list[str] = []  # open telegram inline tags (or "" for ignored)
        self.in_block = False
        self.skip = 0  # inside <script>/<style>

    def _flush(self) -> None:
        if self.in_block or self.cur:
            self.blocks.append((self.kind, "".join(self.cur)))
        self.cur, self.in_block, self.kind = [], False, "p"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in _BLOCK:
            self._flush()
            self.in_block = True
            self.kind = "blockquote" if tag == "blockquote" else "p"
            if tag == "li":
                self.cur.append("— ")
        elif tag == "br":
            # Quill marks an empty line as <p><br></p>; elsewhere <br> is a line break.
            self.cur.append("\n")
        elif tag in _INLINE:
            self.cur.append(f"<{_INLINE[tag]}>")
            self.stack.append(_INLINE[tag])
        elif tag == "a":
            href = (a.get("href") or "").strip()
            if href.startswith(_SAFE_SCHEMES):
                self.cur.append(f'<a href="{_html.escape(href)}">')
                self.stack.append("a")
            else:
                self.stack.append("")
        elif tag == "span" and "tg-spoiler" in (a.get("class") or ""):
            self.cur.append("<tg-spoiler>")
            self.stack.append("tg-spoiler")
        elif tag == "span":
            self.stack.append("")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self._flush()
        elif tag in _INLINE or tag == "a" or tag == "span":
            if self.stack:
                closing = self.stack.pop()
                if closing:
                    self.cur.append(f"</{closing}>")

    def handle_data(self, data):
        if self.skip:
            return
        self.cur.append(_html.escape(data, quote=False))

    def result(self) -> str:
        self._flush()
        lines: list[str] = []
        quote: list[str] = []
        for kind, text in self.blocks:
            text = "" if text == "\n" else text
            if kind == "blockquote":
                quote.append(text)
                continue
            if quote:
                lines.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
                quote = []
            lines.append(text)
        if quote:
            lines.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
        out = "\n".join(lines)
        out = re.sub(r"<(b|i|u|s|code)></\1>", "", out)
        return out.strip("\n")


def normalized_default(key: str) -> str:
    """The default text as it comes back from the editor (to detect 'unchanged')."""
    return editor_to_telegram(telegram_to_editor(CATALOG[key].default))


def editor_to_telegram(editor_html: str) -> str:
    parser = _EditorToTelegram()
    parser.feed(editor_html or "")
    return parser.result()


_TAG_RE = re.compile(r"<(/?)([a-z-]+)([^>]*)>")


def telegram_to_editor(text: str) -> str:
    """Telegram HTML (newline separated) -> one <p>/<blockquote> per line for the editor."""
    out: list[str] = []
    open_tags: list[tuple[str, str]] = []  # (name, full opening tag)
    in_quote = False
    for raw in (text or "").split("\n"):
        line = "".join(full for _, full in open_tags) + raw
        starts_quote = "<blockquote>" in raw
        ends_quote = "</blockquote>" in raw
        line = line.replace("<blockquote>", "").replace("</blockquote>", "")
        for closing, name, rest in _TAG_RE.findall(raw):
            if name == "blockquote":
                continue
            if closing:
                for j in range(len(open_tags) - 1, -1, -1):
                    if open_tags[j][0] == name:
                        open_tags.pop(j)
                        break
            else:
                open_tags.append((name, f"<{name}{rest}>"))
        line += "".join(f"</{name}>" for name, _ in reversed(open_tags))
        block = "blockquote" if (in_quote or starts_quote) else "p"
        out.append(f"<{block}>{line or '<br>'}</{block}>")
        if starts_quote:
            in_quote = True
        if ends_quote:
            in_quote = False
    return "".join(out)
