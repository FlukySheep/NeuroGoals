"""Course journeys: a course's conversation as steps (message + buttons).

A button either opens another step ("goto") or triggers an action (choose a cohort
and pay, join the waitlist, ask a question, …). Steps are edited in the admin
(/admin/courses/<id>/journey) and run by the bot when the catalog is switched on.
"""

import json
import re
import secrets
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import content
from . import texts as t
from .db import Database

MAX_BUTTONS = 8
MAX_LABEL = 60
KEY_RE = re.compile(r"^[a-z0-9_]{1,12}$")

# action -> (admin label, map icon). "goto" and "url" need a target.
ACTIONS = {
    "goto": ("Перейти к шагу", "→"),
    "enroll": ("Выбрать группу и оплатить", "💳"),
    "waitlist": ("Записаться в лист ожидания", "⏳"),
    "ask": ("Задать вопрос", "❓"),
    "faq": ("Частые вопросы", "📚"),
    "manager": ("Связаться с менеджером", "📞"),
    "catalog": ("Все курсы", "🗂"),
    "url": ("Открыть ссылку", "🔗"),
}
SAFE_URL = ("https://", "http://", "tg://")


@dataclass
class Button:
    label: str
    action: str
    target: str = ""

    def as_dict(self) -> dict:
        return {"label": self.label, "action": self.action, "target": self.target}


class JourneyError(ValueError):
    pass


def load_buttons(raw: str | None) -> list[Button]:
    try:
        return [Button(str(b["label"]), str(b["action"]), str(b.get("target", ""))) for b in json.loads(raw or "[]")]
    except (ValueError, KeyError, TypeError):
        return []


def dump_buttons(buttons: list[Button]) -> str:
    return json.dumps([b.as_dict() for b in buttons], ensure_ascii=False)


def parse_buttons(labels: list[str], actions: list[str], targets: list[str], urls: list[str],
                  step_keys: set[str]) -> list[Button]:
    """Rows from the editor form; empty rows are ignored."""
    buttons = []
    for n, (label, action, target, url) in enumerate(zip(labels, actions, targets, urls), start=1):
        label = label.strip()
        if not label:
            continue
        if len(label) > MAX_LABEL:
            raise JourneyError(f"Кнопка {n}: текст длиннее {MAX_LABEL} символов.")
        if action not in ACTIONS:
            raise JourneyError(f"Кнопка {n}: выберите действие.")
        target = target.strip()
        if action == "goto":
            if target not in step_keys:
                raise JourneyError(f"Кнопка «{label}»: выберите шаг, на который она ведёт.")
        elif action == "url":
            target = url.strip()
            if not target.startswith(SAFE_URL):
                raise JourneyError(f"Кнопка «{label}»: ссылка должна начинаться с https://")
        buttons.append(Button(label, action, target if action in ("goto", "url") else ""))
    if len(buttons) > MAX_BUTTONS:
        raise JourneyError(f"Не больше {MAX_BUTTONS} кнопок на шаге.")
    return buttons


def new_key(existing: set[str]) -> str:
    while True:
        key = "s" + secrets.token_hex(3)
        if key not in existing:
            return key


# ---------------------------------------------------------------------------
# Template: today's НейроФинансы flow as a journey
# ---------------------------------------------------------------------------


def _b(label: str, action: str, target: str = "") -> dict:
    return {"label": label, "action": action, "target": target}


TEMPLATE = [
    # (key, admin title, content post, buttons)
    ("step1", "Первый экран", "step1", [
        _b("Понять, подойдёт ли мне", "goto", "step2"),
        _b("Посмотреть программу", "goto", "step5"),
        _b("Выбрать группу", "enroll"),
    ]),
    ("step2", "Начнём с вас", "step2",
     [_b(label, "goto", f"br_{key}") for key, (label, _) in t.BRANCHES.items()]),
    *[(f"br_{key}", f"Ситуация · {label}", f"branch_{key}",
       [_b("А что такое финансовый сценарий?", "goto", "step3")]) for key, (label, _) in t.BRANCHES.items()],
    ("step3", "Что такое финансовый сценарий", "step3", [_b("Что с этим можно сделать?", "goto", "step4")]),
    ("step4", "Что будет меняться", "step4", [_b("Посмотреть программу четырёх дней", "goto", "step5")]),
    ("step5", "Программа 4 дней", "step5", [
        _b("Это мне подходит", "goto", "step6"),
        _b("Узнать о формате", "goto", "step7"),
        _b("Пока сомневаюсь", "goto", "doubt"),
    ]),
    ("doubt", "Пока сомневаюсь", "doubt", [
        _b("Посмотреть формат", "goto", "step7"),
        _b("Частые вопросы", "faq"),
        _b("Задать вопрос", "ask"),
    ]),
    ("step6", "Зоя (кружочек)", "step6", [_b("Формат, даты и стоимость", "goto", "step7")]),
    ("step7", "Формат, даты и цена", "step7", [
        _b("Выбрать группу и оплатить", "enroll"),
        _b("Задать вопрос", "ask"),
    ]),
]
TEMPLATE_START = "step1"
# Photos that came from the manager's /media uploads rather than the post itself.
TEMPLATE_MEDIA = {"step1": "portrait", "step5": "program"}


async def apply_template(db: Database, course_id: str) -> int:
    """Copy the live НейроФинансы flow (current texts, photos and video note) into a course."""
    video_note = await db.kv_get("media:video_note")
    for order, (key, title, post_key, buttons) in enumerate(TEMPLATE):
        post = await content.load(db, post_key)
        html = post.html
        photo = post.photo or (await db.kv_get(f"media:{TEMPLATE_MEDIA[key]}") if key in TEMPLATE_MEDIA else None)
        step_video = None
        if key == "step6":
            if video_note:
                step_video = video_note
            else:  # no video yet: keep the transcript the live bot shows instead
                html = (await content.load(db, "step6_fallback")).html + "\n\n" + html
        await db.step_save(course_id, key, title=title, html=html, photo=photo, video_note=step_video,
                           buttons=json.dumps(buttons, ensure_ascii=False), sort_order=order)
    await db.course_save(course_id, start_step=TEMPLATE_START)
    return len(TEMPLATE)


# ---------------------------------------------------------------------------
# Runtime: sending steps and the catalog
# ---------------------------------------------------------------------------


def keyboard(course_id: str, buttons: list[Button]) -> InlineKeyboardMarkup | None:
    rows = []
    for i, b in enumerate(buttons):
        if b.action == "url":
            rows.append([InlineKeyboardButton(text=b.label, url=b.target)])
        elif b.action == "goto":
            rows.append([InlineKeyboardButton(text=b.label, callback_data=f"j:{course_id}:{b.target}")])
        else:
            rows.append([InlineKeyboardButton(text=b.label, callback_data=f"ja:{course_id}:{b.action}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send_step(bot: Bot, chat_id: int, course_id: str, step: dict) -> Message | None:
    markup = keyboard(course_id, load_buttons(step["buttons"]))
    html = (step["html"] or "").strip()
    if step.get("video_note"):
        msg = await bot.send_video_note(chat_id, step["video_note"], reply_markup=None if html else markup)
        if not html:
            return msg
        return await bot.send_message(chat_id, html, reply_markup=markup)
    return await content.send_rendered(bot, chat_id, content.Post(html=html, photo=step.get("photo")), markup)


def visible_courses(courses: list[dict], is_admin: bool) -> list[dict]:
    """Students see open and waitlist courses; admins also see drafts and closed ones (marked)."""
    return [c for c in courses if c["status"] in ("open", "waitlist") or is_admin]
