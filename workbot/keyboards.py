"""Inline buttons under card messages. callback_data: "w:<action>:<card shortLink>[:<arg>]"."""

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

from .board import LISTS

DUE_CHOICES = [("today", "Сегодня"), ("tomorrow", "Завтра"), ("fri", "Пятница"), ("week", "+ неделя"),
               ("none", "Убрать срок")]


def _kb(rows: list[list[B]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back(card: str) -> list[B]:
    return [B(text="← Назад", callback_data=f"w:bk:{card}")]


def card(card: str, closed: bool = False) -> InlineKeyboardMarkup:
    if closed:
        return _kb([[B(text="♻️ Вернуть из архива", callback_data=f"w:re:{card}")]])
    return _kb([
        [B(text="✅ Готово", callback_data=f"w:dn:{card}"), B(text="🔄 Статус", callback_data=f"w:st:{card}")],
        [B(text="🙋 Беру", callback_data=f"w:me:{card}"), B(text="👥 Назначить", callback_data=f"w:as:{card}"),
         B(text="⏰ Срок", callback_data=f"w:du:{card}")],
        [B(text="🗄 В архив", callback_data=f"w:ar:{card}")],
    ])


def status(card: str, current_key: str | None) -> InlineKeyboardMarkup:
    buttons = [B(text=("• " if key == current_key else "") + name, callback_data=f"w:mv:{card}:{key}")
               for key, name, _ in LISTS]
    return _kb([buttons[i:i + 2] for i in range(0, len(buttons), 2)] + [_back(card)])


def assign(card: str, members: list[dict], assigned: set[str]) -> InlineKeyboardMarkup:
    buttons = [B(text=("✅ " if m["trello_id"] in assigned else "") + m["full_name"],
                 callback_data=f"w:am:{card}:{m['tg_user_id']}") for m in members]
    return _kb([buttons[i:i + 2] for i in range(0, len(buttons), 2)] + [_back(card)])


def due(card: str) -> InlineKeyboardMarkup:
    buttons = [B(text=label, callback_data=f"w:dd:{card}:{key}") for key, label in DUE_CHOICES]
    return _kb([buttons[:3], buttons[3:], _back(card)])


def archive(card: str) -> InlineKeyboardMarkup:
    return _kb([
        [B(text="🗄 Да, в архив", callback_data=f"w:ay:{card}")],
        [B(text="❌ Удалить навсегда (админ)", callback_data=f"w:rm:{card}")],
        _back(card),
    ])


def delete_confirm(card: str) -> InlineKeyboardMarkup:
    return _kb([[B(text="❌ Точно удалить навсегда", callback_data=f"w:ry:{card}")], _back(card)])
