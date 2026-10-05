"""Lead database in the admin panel: /admin/leads (list, filters, CSV export, notes)."""

import csv
import html
import io
import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from . import content
from . import texts as t
from .panel import _authed, _bot, _cfg, _db, _flash, _page, _redirect, nav

TZ = ZoneInfo("Europe/Zurich")
LIST_LIMIT = 300

STATUS_LABELS = {
    "new": "Смотрит",
    "chose_group": "Выбрал группу",
    "pending": "Ждёт проверки оплаты",
    "paid": "Оплатил",
}
# Leads created before stage tracking: infer the stage from the status.
STATUS_STAGE = {"chose_group": 8, "pending": 10, "paid": 11}

LEADS_CSS = """
.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;padding:12px}
.filters select,.filters input{width:100%;padding:9px 10px;border-radius:9px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:15px}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;min-width:110px}
.stat b{display:block;font-size:22px}.stat span{font-size:13px;color:var(--muted)}
.lead{display:block;padding:12px 16px;border-top:1px solid var(--line);color:var(--ink);text-decoration:none}
.lead:first-child{border-top:0}.lead:hover{background:var(--chip)}
.lead .top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.lead .meta{font-size:13px;color:var(--muted);margin-top:2px}
.badge.paid{background:var(--accent);color:var(--accent-ink)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;padding:16px;margin:0}
dt{color:var(--muted)}dd{margin:0}
textarea{width:100%;min-height:120px;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit}
code.link{display:block;padding:10px;border-radius:9px;background:var(--chip);word-break:break-all;font-size:14px;user-select:all}
"""


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def stage(lead: dict) -> int:
    return max(lead.get("furthest_step") or 0, STATUS_STAGE.get(lead["status"], 0), 1)


def situation_label(key: str | None) -> str:
    return t.BRANCHES[key][0] if key in t.BRANCHES else "—"


def group_label(key: str | None) -> str:
    return t.GROUPS[key].title if key in t.GROUPS else "—"


def contact_url(lead: dict) -> str:
    return f"https://t.me/{lead['username']}" if lead.get("username") else f"tg://user?id={lead['user_id']}"


def _filters(request: Request) -> dict[str, str]:
    q = request.query_params
    return {k: q.get(k, "").strip() for k in ("status", "group", "situation", "source", "q")}


def _staff() -> set[int]:
    config = _cfg()
    return set(config.admin_ids) | ({config.admin_chat_id} if config.admin_chat_id else set())


async def _query(f: dict[str, str], limit: int | None) -> list[dict]:
    """Leads matching the filters; the manager's own account is not a lead."""
    staff = _staff()
    rows = await _db().leads(f["status"], f["group"], f["situation"], f["source"], f["q"],
                             limit + len(staff) if limit else None)
    rows = [r for r in rows if r["user_id"] not in staff]
    return rows[:limit] if limit else rows


def _select(name: str, label: str, options: list[tuple[str, str]], current: str) -> str:
    opts = [f'<option value="">{html.escape(label)}: все</option>']
    for value, title in options:
        sel = " selected" if value == current else ""
        opts.append(f'<option value="{html.escape(value)}"{sel}>{html.escape(title)}</option>')
    return f'<select name="{name}" onchange="this.form.submit()">{"".join(opts)}</select>'


async def leads_page(request: Request) -> Response:
    if not _authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    f = _filters(request)
    db = _db()
    rows = await _query(f, LIST_LIMIT + 1)
    everyone = await _query({k: "" for k in f}, None)
    sources = await db.distinct_sources()

    total = len(everyone)
    by_status = {s: sum(1 for r in everyone if r["status"] == s) for s in STATUS_LABELS}
    stats = (
        f'<div class="stat"><b>{total}</b><span>всего</span></div>'
        + "".join(
            f'<div class="stat"><b>{by_status[s]}</b><span>{html.escape(label)}</span></div>'
            for s, label in STATUS_LABELS.items()
        )
    )

    filters = f"""<form method="get" class="card filters">
{_select("status", "Статус", list(STATUS_LABELS.items()), f["status"])}
{_select("group", "Группа", [(k, g.title) for k, g in t.GROUPS.items()] + [("-", "без группы")], f["group"])}
{_select("situation", "Ситуация", [(k, v[0]) for k, v in t.BRANCHES.items()] + [("-", "не отвечал")], f["situation"])}
{_select("source", "Источник", [(s, s) for s in sources] + [("-", "без источника")], f["source"])}
<input type="search" name="q" value="{html.escape(f["q"])}" placeholder="Имя, @ник или заметка">
</form>"""

    items = []
    for r in rows[:LIST_LIMIT]:
        name = html.escape(r["full_name"] or "Без имени")
        username = f" · @{html.escape(r['username'])}" if r["username"] else ""
        badges = [f'<span class="badge{" paid" if r["status"] == "paid" else ""}">'
                  f'{STATUS_LABELS.get(r["status"], r["status"])}</span>']
        if r["group_key"]:
            badges.append(f'<span class="badge">{html.escape(group_label(r["group_key"]))}</span>')
        if r["blocked"]:
            badges.append('<span class="badge">заблокировал бота</span>')
        meta = [content.STAGES.get(stage(r), ""), f"источник: {r['source'] or '—'}"]
        if r["situation"]:
            meta.append(situation_label(r["situation"]))
        meta.append(f"был(а): {_fmt_time(r['last_seen'] or r['created_at'])}")
        note = f'<div class="meta">📝 {html.escape((r["notes"] or "")[:120])}</div>' if r["notes"] else ""
        items.append(
            f'<a class="lead" href="/admin/leads/{r["user_id"]}">'
            f'<div class="top"><span><b>{name}</b>{username}</span><span class="badges">{"".join(badges)}</span></div>'
            f'<div class="meta">{html.escape(" · ".join(meta))}</div>{note}</a>'
        )
    more = (
        f'<p class="note">Показаны последние {LIST_LIMIT}. Все — в выгрузке CSV.</p>' if len(rows) > LIST_LIMIT else ""
    )
    export = "/admin/leads.csv?" + urlencode({k: v for k, v in f.items() if v})
    found = f"Найдено: {min(len(rows), LIST_LIMIT)}{'+' if len(rows) > LIST_LIMIT else ''}"

    try:
        bot_username = (await _bot().me()).username
    except Exception:
        bot_username = "neurogoals_bot"
    link_tool = f"""<h2>Ссылки с источником</h2>
<div class="card" style="padding:16px">
<p class="note" style="margin-top:0">Используйте разные ссылки в разных местах — и в списке будет видно,
откуда пришёл человек. Только латиница, цифры, «_» и «-».</p>
<input id="src" placeholder="например: instagram, webinar, stories_oct" style="width:100%;padding:10px;border-radius:9px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit">
<p><code class="link" id="srclink">https://t.me/{bot_username}?start=…</code></p>
</div>
<script>
const src=document.getElementById('src'), out=document.getElementById('srclink');
src.addEventListener('input',()=>{{const v=src.value.trim().toLowerCase().replace(/[^a-z0-9_-]/g,'').slice(0,64);
src.value=v; out.textContent='https://t.me/{bot_username}?start='+(v||'…');}});
</script>"""

    body = f"""<header><h1>Лиды</h1>{nav("leads")}</header>{_flash(request)}
<div class="stats">{stats}</div>{filters}
<div class="row" style="justify-content:space-between;margin:12px 0">
<span class="note" style="margin:0">{found}</span>
<a class="btn" href="{html.escape(export)}">⬇ Скачать CSV</a></div>
<div class="card">{"".join(items) or '<p class="note" style="padding:16px">Никого не найдено.</p>'}</div>
{more}{link_tool}"""
    return _page("Лиды", body, f"<style>{LEADS_CSS}</style>")


async def lead_page(request: Request) -> Response:
    if not _authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    user_id = int(request.path_params["user_id"])
    r = await _db().get_user(user_id)
    if r is None:
        return _redirect("/admin/leads", err="Лид не найден")
    rows = [
        ("Telegram", f'<a href="{html.escape(contact_url(r))}">'
                     f'{"@" + html.escape(r["username"]) if r["username"] else "открыть чат"}</a>'),
        ("ID", f"<code>{r['user_id']}</code>"),
        ("Статус", STATUS_LABELS.get(r["status"], r["status"])),
        ("Дошёл до", content.STAGES.get(stage(r), "—")),
        ("Ситуация", situation_label(r["situation"])),
        ("Группа", group_label(r["group_key"])),
        ("Валюта", t.CURRENCY_AMOUNT.get(r["currency"] or "", "—")),
        ("Способ оплаты", t.PAYMENT_METHODS[r["method"]].label if r["method"] in t.PAYMENT_METHODS else "—"),
        ("Источник", html.escape(r["source"] or "—")),
        ("Впервые", _fmt_time(r["created_at"])),
        ("Последний раз", _fmt_time(r["last_seen"])),
        ("Напоминание", "отправлено" if r["reminder_sent"] else "нет"),
        ("Бот", "заблокирован пользователем" if r["blocked"] else "активен"),
    ]
    details = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    body = f"""<header><h1>{html.escape(r["full_name"] or "Без имени")}</h1>
<a class="btn" href="/admin/leads">← Назад</a></header>{_flash(request)}
<div class="card"><dl>{details}</dl></div>
<h2>Заметки</h2>
<form method="post"><textarea name="notes" placeholder="Например: спрашивала про рассрочку">{html.escape(r["notes"] or "")}</textarea>
<p><button class="btn primary">Сохранить заметку</button></p></form>"""
    return _page(r["full_name"] or "Лид", body, f"<style>{LEADS_CSS}</style>")


async def lead_save(request: Request) -> Response:
    if not _authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    user_id = int(request.path_params["user_id"])
    form = await request.form()
    await _db().set_notes(user_id, str(form.get("notes", ""))[:5000])
    return _redirect(f"/admin/leads/{user_id}", msg="Заметка сохранена.")


CSV_FIELDS = [
    ("ID", lambda r: r["user_id"]),
    ("Имя", lambda r: r["full_name"] or ""),
    ("Username", lambda r: f"@{r['username']}" if r["username"] else ""),
    ("Ссылка", contact_url),
    ("Статус", lambda r: STATUS_LABELS.get(r["status"], r["status"])),
    ("Дошёл до", lambda r: content.STAGES.get(stage(r), "")),
    ("Ситуация", lambda r: situation_label(r["situation"]) if r["situation"] else ""),
    ("Группа", lambda r: group_label(r["group_key"]) if r["group_key"] else ""),
    ("Валюта", lambda r: (r["currency"] or "").upper()),
    ("Способ оплаты", lambda r: t.PAYMENT_METHODS[r["method"]].label if r["method"] in t.PAYMENT_METHODS else ""),
    ("Источник", lambda r: r["source"] or ""),
    ("Впервые", lambda r: _fmt_time(r["created_at"])),
    ("Последний раз", lambda r: _fmt_time(r["last_seen"])),
    ("Заблокировал бота", lambda r: "да" if r["blocked"] else ""),
    ("Заметки", lambda r: r["notes"] or ""),
]


async def leads_csv(request: Request) -> Response:
    if not _authed(request):
        return Response(status_code=403)
    rows = await _query(_filters(request), None)
    buf = io.StringIO()
    # ";" + BOM: opens correctly in Excel with Russian/Swiss settings; Google Sheets detects it too.
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([name for name, _ in CSV_FIELDS])
    for r in rows:
        writer.writerow([_cell(fn(r)) if name in FREE_TEXT else fn(r) for name, fn in CSV_FIELDS])
    filename = f"leads-{datetime.now(TZ):%Y-%m-%d}.csv"
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


FREE_TEXT = {"Имя", "Заметки"}


def _cell(value) -> str:
    text = str(value)
    # Neutralise spreadsheet formulas in user-controlled text (names, notes).
    return "'" + text if re.match(r"^[=+\-@]", text) else text


routes = [
    Route("/admin/leads", leads_page),
    Route("/admin/leads.csv", leads_csv),
    Route("/admin/leads/{user_id:int}", lead_page, methods=["GET"]),
    Route("/admin/leads/{user_id:int}", lead_save, methods=["POST"]),
]
