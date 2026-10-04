"""Board layout created by /setup, and the card types the commands create."""

from dataclasses import dataclass

from .db import WorkDatabase
from .trello import Trello

BOARD_NAME = "NeuroGoals · Работа"

# key, list name, words accepted by /move and /list
LISTS = [
    ("inbox", "📥 Входящие", ("inbox", "входящие", "вход", "идеи")),
    ("todo", "📋 К выполнению", ("todo", "сделать", "задачи", "выполнить")),
    ("progress", "🔄 В работе", ("progress", "doing", "работа", "работе")),
    ("review", "👀 На проверке", ("review", "проверка", "проверке", "проверить")),
    ("done", "✅ Готово", ("done", "готово", "сделано", "выполнено")),
    ("events", "📅 События", ("events", "события", "событие", "календарь")),
    ("notes", "📝 Заметки и требования", ("notes", "заметки", "требования", "заметка")),
]
LIST_NAMES = {key: name for key, name, _ in LISTS}

# key, label name, Trello colour
LABELS = [
    ("task", "Задача", "blue"),
    ("event", "Событие", "purple"),
    ("req", "Требование", "sky"),
    ("note", "Заметка", "lime"),
    ("bug", "Баг", "orange"),
    ("idea", "Идея", "pink"),
    ("high", "🔴 Высокий приоритет", "red"),
    ("medium", "🟡 Средний приоритет", "yellow"),
    ("low", "🟢 Низкий приоритет", "green"),
]


@dataclass(frozen=True)
class Kind:
    key: str
    list_key: str
    emoji: str
    name: str            # shown in replies ("Задача создана")
    default_hour: int    # due time when only a date is given
    checklist: str       # checklist name for "- item" lines
    reminder: int | None = None  # Trello due reminder, minutes before


KINDS = {
    "task": Kind("task", "todo", "📋", "Задача", 18, "Чек-лист"),
    "event": Kind("event", "events", "📅", "Событие", 10, "Подготовка", reminder=60),
    "req": Kind("req", "notes", "📐", "Требование", 18, "Критерии приёмки"),
    "note": Kind("note", "notes", "📝", "Заметка", 18, "Пункты"),
    "idea": Kind("idea", "inbox", "💡", "Идея", 18, "Пункты"),
    "bug": Kind("bug", "todo", "🐞", "Баг", 18, "Шаги"),
}


def list_key(word: str) -> str | None:
    word = word.strip().lower()
    for key, name, aliases in LISTS:
        if word == key or word in aliases or word == name.split(" ", 1)[1].lower():
            return key
    # "в работе", "на проверке", "к выполнению" — two words
    for key, name, _ in LISTS:
        if len(word) >= 3 and word in name.lower():
            return key
    return None


async def create_board(trello: Trello, db: WorkDatabase, workspace: str = "") -> dict:
    created = await trello.create_board(BOARD_NAME, workspace)
    board = {"id": created["id"], "url": created.get("shortUrl") or created.get("url"),
             "name": created["name"], "lists": {}, "labels": {}, "tags": {}}
    for pos, (key, name, _) in enumerate(LISTS, start=1):
        board["lists"][key] = (await trello.create_list(board["id"], name, pos * 1000))["id"]
    for key, name, color in LABELS:
        board["labels"][key] = (await trello.create_label(board["id"], name, color))["id"]
    await db.board_set(board)
    return board


async def tag_label(trello: Trello, db: WorkDatabase, board: dict, tag: str) -> str:
    """Label id for a #tag, creating the label on first use."""
    key = tag.lower()
    if key not in board["tags"]:
        board["tags"][key] = (await trello.create_label(board["id"], tag, None))["id"]
        await db.board_set(board)
    return board["tags"][key]
