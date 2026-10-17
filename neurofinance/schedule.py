"""Structured cohort schedules: sessions as real dates/times in a time zone.

The admin picks dates and times with native pickers; the student-facing text
("10 октября (сб) — День 1", "12:00–16:00 по Москве / 11:00–15:00 по Женеве")
is generated from them, including daylight-saving changes between the zones.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

# (IANA zone, label in the admin, phrase in the bot)
TIMEZONES = [
    ("Europe/Moscow", "Москва", "по Москве"),
    ("Europe/Zurich", "Женева / Цюрих", "по Женеве"),
    ("Europe/Berlin", "Берлин / Париж", "по Берлину"),
    ("Europe/London", "Лондон", "по Лондону"),
    ("Europe/Kyiv", "Киев", "по Киеву"),
    ("Asia/Tbilisi", "Тбилиси", "по Тбилиси"),
    ("Asia/Dubai", "Дубай", "по Дубаю"),
    ("Asia/Almaty", "Алматы", "по Алматы"),
    ("America/New_York", "Нью-Йорк", "по Нью-Йорку"),
]
TZ_PHRASE = {tz: phrase for tz, _, phrase in TIMEZONES}
DEFAULT_TZ = "Europe/Moscow"
DEFAULT_EXTRA_TZ = "Europe/Zurich"

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Session:
    day: date
    start: time
    end: time

    def as_dict(self) -> dict:
        return {"date": self.day.isoformat(), "start": self.start.strftime("%H:%M"), "end": self.end.strftime("%H:%M")}


def parse_sessions(dates: list[str], starts: list[str], ends: list[str]) -> list[Session]:
    """Rows from the form; fully empty rows are ignored, partial rows are an error."""
    sessions = []
    for n, (d, s, e) in enumerate(zip(dates, starts, ends), start=1):
        d, s, e = d.strip(), s.strip(), e.strip()
        if not (d or s or e):
            continue
        if not (d and s and e):
            raise ScheduleError(f"Встреча {n}: укажите дату, начало и конец.")
        try:
            session = Session(date.fromisoformat(d), time.fromisoformat(s), time.fromisoformat(e))
        except ValueError:
            raise ScheduleError(f"Встреча {n}: неверная дата или время.") from None
        if session.end <= session.start:
            raise ScheduleError(f"Встреча {n}: конец должен быть позже начала.")
        sessions.append(session)
    sessions.sort(key=lambda x: (x.day, x.start))
    return sessions


def load_sessions(raw: str | None) -> list[Session]:
    if not raw:
        return []
    try:
        return [Session(date.fromisoformat(x["date"]), time.fromisoformat(x["start"]), time.fromisoformat(x["end"]))
                for x in json.loads(raw)]
    except (ValueError, KeyError, TypeError):
        return []


def dump_sessions(sessions: list[Session]) -> str:
    return json.dumps([s.as_dict() for s in sessions])


def _day_label(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} ({WEEKDAYS[d.weekday()]})"


def _span(s: Session, tz: str, target: str | None) -> str:
    start = datetime.combine(s.day, s.start, ZoneInfo(tz))
    end = datetime.combine(s.day, s.end, ZoneInfo(tz))
    if target and target != tz:
        start, end = start.astimezone(ZoneInfo(target)), end.astimezone(ZoneInfo(target))
    return f"{start:%H:%M}–{end:%H:%M}"


def render(sessions: list[Session], tz: str, extra_tz: str | None = None, note: str = "") -> tuple[str, str]:
    """(dates_text, time_text) in the same style the bot has always used."""
    if not sessions:
        return "", note.strip()
    zones = [tz] + ([extra_tz] if extra_tz and extra_tz != tz else [])
    spans = [[_span(s, tz, z) for z in zones] for s in sessions]
    same_time = all(sp == spans[0] for sp in spans)
    lines = []
    for i, (s, sp) in enumerate(zip(sessions, spans), start=1):
        when = "" if same_time else f", {' / '.join(sp)}"
        lines.append(f"{_day_label(s.day)}{when} — День {i}")
    dates_text = "\n".join(lines)
    if same_time:
        time_lines = [f"{span} {TZ_PHRASE.get(z, z)}" for span, z in zip(spans[0], zones)]
    else:
        time_lines = ["Время " + " / ".join(TZ_PHRASE.get(z, z) for z in zones)]
    if note.strip():
        time_lines.append(note.strip())
    return dates_text, "\n".join(time_lines)
