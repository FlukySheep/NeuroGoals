"""Parses command text such as

    /task Обновить лендинг @anna !срочно до пятницы 18:00 #маркетинг
    - проверить цены
    - заменить фото

First line: the title plus tokens (@people, !priority, #tags, a due date).
Following lines starting with "-", "•", "*" or "1." become checklist items, other lines the description.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

PRIORITY_WORDS = {
    "!!": "high", "!high": "high", "!h": "high", "!высокий": "high", "!срочно": "high", "!важно": "high",
    "!medium": "medium", "!m": "medium", "!средний": "medium",
    "!low": "low", "!l": "low", "!низкий": "low",
}

WEEKDAYS = [
    r"понедельник[а]?|пн|monday|mon",
    r"вторник[а]?|вт|tuesday|tue",
    r"сред[уаы]|ср|wednesday|wed",
    r"четверг[а]?|чт|thursday|thu",
    r"пятниц[уаы]|пт|friday|fri",
    r"суббот[уаы]|сб|saturday|sat",
    r"воскресень[ея]|вс|sunday|sun",
]
_WEEKDAY = "|".join(f"(?P<wd{i}>{w})" for i, w in enumerate(WEEKDAYS))
_TIME = r"(?P<h>\d{1,2}):(?P<mi>\d{2})"
_PREFIX = r"(?:(?:due|срок):\s*|(?:до|к|в|во|на|by|on)\s+)?"

DATE_RE = re.compile(
    r"(?<!\S)" + _PREFIX + r"(?:"
    r"(?P<word>сегодня|завтра|послезавтра|today|tomorrow)"
    r"|(?P<d>\d{1,2})\.(?P<mo>\d{2})(?:\.(?P<y>\d{4}|\d{2}))?"
    r"|через\s+(?P<n>\d+\s*)?(?P<unit>день|дня|дней|час|часа|часов|недел[юиь]|неделя)"
    r"|" + _WEEKDAY +
    r")(?:\s+(?:в\s+)?" + _TIME + r")?(?![\w.:])",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"(?<!\S)(?:(?:в|к|до|at)\s+)?" + _TIME + r"(?![\w.:])", re.IGNORECASE)
MENTION_RE = re.compile(r"(?<!\S)@([A-Za-z0-9_]{3,32}|я|me)(?![\w])", re.IGNORECASE)
PRIORITY_RE = re.compile(r"(?<!\S)(" + "|".join(re.escape(k) for k in PRIORITY_WORDS) + r")(?!\S)", re.IGNORECASE)
TAG_RE = re.compile(r"(?<!\S)#([^\W\d][\w-]*)", re.UNICODE)
CHECK_RE = re.compile(r"^\s*(?:[-•*–]|\d+[.)]|\[\s?\])\s+(.+)$")


@dataclass
class Parsed:
    title: str
    description: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)   # lowercase usernames; "me" = the author
    priority: str | None = None                          # high / medium / low
    tags: list[str] = field(default_factory=list)
    due: datetime | None = None


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" ,;:–—-").strip()


def _time_of(m: re.Match) -> time | None:
    if m.group("h") is None:
        return None
    h, mi = int(m.group("h")), int(m.group("mi"))
    return time(h, mi) if h < 24 and mi < 60 else None


def _date_of(m: re.Match, now: datetime) -> tuple[datetime | None, bool]:
    """(date at midnight or exact moment, whether it is an exact moment)."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if word := m.group("word"):
        days = {"сегодня": 0, "today": 0, "завтра": 1, "tomorrow": 1, "послезавтра": 2}[word.lower()]
        return today + timedelta(days=days), False
    if m.group("d"):
        d, mo = int(m.group("d")), int(m.group("mo"))
        y = m.group("y")
        year = (2000 + int(y) if len(y) == 2 else int(y)) if y else now.year
        try:
            date = today.replace(year=year, month=mo, day=d)
        except ValueError:
            return None, False
        if not y and date < today:
            date = date.replace(year=year + 1)
        return date, False
    if unit := m.group("unit"):
        n = int(m.group("n") or 1)
        unit = unit.lower()
        if unit.startswith("час"):
            return now.replace(second=0, microsecond=0) + timedelta(hours=n), True
        if unit.startswith("недел"):
            return today + timedelta(weeks=n), False
        return today + timedelta(days=n), False
    for i in range(7):
        if m.group(f"wd{i}"):
            return today + timedelta(days=(i - today.weekday()) % 7), False
    return None, False


def find_due(text: str, now: datetime, default_hour: int = 18) -> tuple[datetime | None, str]:
    """Finds the first date/time in text. Returns (due, text without it)."""
    m = DATE_RE.search(text)
    if m:
        date, exact = _date_of(m, now)
        if date is not None:
            if not exact:
                at = _time_of(m)
                hm = at or time(default_hour)
                date = date.replace(hour=hm.hour, minute=hm.minute)
                if at is None and date < now and (m.group("word") or "").lower() in ("сегодня", "today"):
                    date = date.replace(hour=23, minute=59)  # "сегодня" said after the default hour
                # "в пятницу" said on Friday after the deadline hour means next Friday.
                if date < now and m.group("word") is None and not m.group("d"):
                    date += timedelta(days=7)
            return date, text[:m.start()] + " " + text[m.end():]
    m = TIME_RE.search(text)
    if m and (at := _time_of(m)):
        date = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        if date < now:
            date += timedelta(days=1)
        return date, text[:m.start()] + " " + text[m.end():]
    return None, text


def parse(text: str, now: datetime, default_hour: int = 18) -> Parsed:
    lines = [ln.rstrip() for ln in (text or "").strip().splitlines()]
    head = lines[0] if lines else ""
    result = Parsed(title="")

    for body_line in lines[1:]:
        if m := CHECK_RE.match(body_line):
            result.checklist.append(m.group(1).strip())
        elif body_line.strip() or result.description:
            result.description.append(body_line)
    while result.description and not result.description[-1].strip():
        result.description.pop()

    def take_mention(m: re.Match) -> str:
        name = m.group(1).lower()
        name = "me" if name in ("я", "me") else name
        if name not in result.mentions:
            result.mentions.append(name)
        return " "

    def take_priority(m: re.Match) -> str:
        result.priority = PRIORITY_WORDS[m.group(1).lower()]
        return " "

    def take_tag(m: re.Match) -> str:
        if m.group(1) not in result.tags:
            result.tags.append(m.group(1))
        return " "

    head = MENTION_RE.sub(take_mention, head)
    head = PRIORITY_RE.sub(take_priority, head)
    head = TAG_RE.sub(take_tag, head)
    result.due, head = find_due(head, now, default_hour)
    result.title = _clean(head)
    return result


CARD_REF_RE = re.compile(r"^\s*#?(\d{1,6})(?!\S)\s*(.*)$", re.DOTALL)


def card_ref(args: str | None) -> tuple[int | None, str]:
    """'42 rest of text' / '#42 rest' -> (42, 'rest of text'); otherwise (None, args)."""
    m = CARD_REF_RE.match(args or "")
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, (args or "").strip()
