"""Card and card-list messages (HTML)."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .board import KINDS, LIST_NAMES, LISTS

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
PRIORITY = {"high": "🔴 высокий", "medium": "🟡 средний", "low": "🟢 низкий"}
MAX_LIST_LINES = 25
MAX_MESSAGE = 3900


def parse_due(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)


def fmt_due(due: datetime, now: datetime) -> str:
    text = f"{WEEKDAYS[due.weekday()]}, {due:%d.%m}"
    if due.year != now.year:
        text += f".{due:%Y}"
    if (due.hour, due.minute) != (0, 0):
        text += f" {due:%H:%M}"
    return text


def _keys_by_id(board: dict) -> tuple[dict, dict]:
    lists = {v: k for k, v in board["lists"].items()}
    labels = {v: k for k, v in board["labels"].items()}
    return lists, labels


def card_kind(card: dict, board: dict) -> str:
    _, labels = _keys_by_id(board)
    for label_id in card.get("idLabels", []):
        if labels.get(label_id) in KINDS:
            return labels[label_id]
    return "task"


def is_overdue(card: dict, board: dict, now: datetime) -> bool:
    due = parse_due(card.get("due"), now.tzinfo)
    return bool(due and due < now and not card.get("dueComplete")
                and card.get("idList") != board["lists"].get("done"))


def card_text(card: dict, board: dict, now: datetime, header: str = "") -> str:
    lists, labels = _keys_by_id(board)
    kind = KINDS[card_kind(card, board)]
    lines = [header] if header else []
    lines.append(f"{kind.emoji} <b>#{card['idShort']} · {escape(card['name'])}</b>")

    tags = [kind.name]
    for label in card.get("labels", []):
        key = labels.get(label["id"])
        if key in PRIORITY:
            tags.append(PRIORITY[key])
        elif key is None and label.get("name"):
            tags.append("#" + escape(label["name"]))
    lines.append("🏷 " + " · ".join(tags))

    if card.get("closed"):
        lines.append("🗄 <i>В архиве</i>")
    else:
        lines.append(escape(LIST_NAMES.get(lists.get(card["idList"]), "📍 другой список")))
    if members := card.get("members"):
        lines.append("👤 " + escape(", ".join(m.get("fullName") or m.get("username", "?") for m in members)))
    if due := parse_due(card.get("due"), now.tzinfo):
        mark = " ✅" if card.get("dueComplete") else (" ⚠️ просрочено" if is_overdue(card, board, now) else "")
        lines.append(f"⏰ {'когда' if kind.key == 'event' else 'срок'}: {fmt_due(due, now)}{mark}")
    badges = card.get("badges") or {}
    if badges.get("checkItems"):
        lines.append(f"☑️ {badges.get('checkItemsChecked', 0)}/{badges['checkItems']}")
    if card.get("desc"):
        desc = card["desc"].split("\n---\n")[0].strip()  # drop the "added from Telegram" footer
        if desc:
            lines.append("📄 " + escape(desc[:300] + ("…" if len(desc) > 300 else "")))
    lines.append(f'<a href="{escape(card["shortUrl"])}">Открыть в Trello</a>')
    return "\n".join(lines)


def card_line(card: dict, board: dict, now: datetime, names: dict[str, str]) -> str:
    kind = KINDS[card_kind(card, board)]
    parts = [f'{kind.emoji} <a href="{escape(card["shortUrl"])}">#{card["idShort"]}</a> {escape(card["name"])}']
    who = [names[m] for m in card.get("idMembers", []) if m in names]
    if who:
        parts.append("— " + escape(", ".join(who)))
    if due := parse_due(card.get("due"), now.tzinfo):
        parts.append(f"· {fmt_due(due, now)}" + (" ⚠️" if is_overdue(card, board, now) else ""))
    return " ".join(parts)


def cards_text(title: str, cards: list[dict], board: dict, now: datetime, names: dict[str, str]) -> str:
    if not cards:
        return f"{title}\n\nНичего нет 🎉"
    lists, _ = _keys_by_id(board)
    order = {key: i for i, (key, _, _) in enumerate(LISTS)}
    groups: dict[str, list[dict]] = {}
    for card in cards:
        groups.setdefault(lists.get(card["idList"], "other"), []).append(card)

    out = [f"{title} ({len(cards)})"]
    for key in sorted(groups, key=lambda k: order.get(k, 99)):
        group = sorted(groups[key], key=lambda c: (c.get("due") is None, c.get("due") or "", c["idShort"]))
        out.append(f"\n<b>{escape(LIST_NAMES.get(key, 'Другие'))}</b>")
        out += [card_line(c, board, now, names) for c in group[:MAX_LIST_LINES]]
        if len(group) > MAX_LIST_LINES:
            out.append(f"…и ещё {len(group) - MAX_LIST_LINES}")
    text = "\n".join(out)
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE].rsplit("\n", 1)[0] + "\n…список обрезан, откройте доску"
    return text
