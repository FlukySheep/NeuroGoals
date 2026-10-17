"""Admin panel: the AI consultant (/admin/ai) — ecosystem direction, greeting, product map, interviews."""

import json

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from . import agent_bot, ai, catalog
from .courses_admin import _allowed, _esc
from .leads import _fmt_time
from .panel import _cfg, _db, _flash, _page, _redirect, nav

PAGE_CSS = """
textarea.big{min-height:300px;font:inherit;line-height:1.5;resize:vertical}
textarea.mid{min-height:110px;font:inherit;line-height:1.5;resize:vertical}
.stage{padding:12px 16px;border-top:1px solid var(--line)}.stage:first-child{border-top:0}
.stage h4{margin:0 0 6px;font-size:14px;color:var(--muted);font-weight:600}
.prod{display:flex;justify-content:space-between;gap:10px;padding:4px 0;color:var(--ink);text-decoration:none}
.prod small{color:var(--muted)}
.chat{padding:14px 16px;border-top:1px solid var(--line)}.chat:first-child{border-top:0}
.chat .meta{font-size:13px;color:var(--muted)}
.chat summary{cursor:pointer;color:var(--muted);font-size:14px;margin-top:6px}
.line{margin:6px 0;white-space:pre-wrap;font-size:14px}.line b{color:var(--muted);font-weight:600}
.badge.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
"""


def _status_block() -> str:
    cfg = _cfg()
    state = {"off": "выключен", "admins": "включён только для администраторов", "on": "включён для всех"}
    problems = []
    if not cfg.ai_key:
        problems.append("Нет ключа: добавьте <code>ANTHROPIC_API_KEY</code> в настройки проекта Vercel.")
    if cfg.ai == "off":
        problems.append("Чтобы бот начинал с подбора, задайте <code>AI_AGENT=admins</code> (проверка) "
                        "или <code>AI_AGENT=on</code> (для всех).")
    hints = "".join(f"<p class='note' style='margin:4px 0 0'>{p}</p>" for p in problems)
    return (f'<div class="card section"><h3>Состояние</h3><p>Консультант в боте: '
            f'<b>{state.get(cfg.ai, cfg.ai)}</b> · модель <code>{_esc(cfg.ai_model)}</code></p>{hints}</div>')


async def _product_map(courses: list[dict]) -> str:
    by_stage: dict[int, list[dict]] = {}
    for c in courses:
        by_stage.setdefault(c.get("stage") or 0, []).append(c)
    blocks = []
    for stage in sorted(by_stage, key=lambda s: (s == 0, s)):
        items = []
        for c in by_stage[stage]:
            kb = (c.get("knowledge_base") or "").strip()
            badge = (f'<small>база знаний: {len(kb)} симв.</small>' if kb
                     else '<span class="badge warn">нет базы знаний</span>')
            hidden = " · скрыт (черновик)" if c["status"] == "draft" else ""
            items.append(f'<a class="prod" href="/admin/courses/{_esc(c["id"])}"><span>{_esc(catalog.type_label(c))} · '
                         f'<b>{_esc(c["title"])}</b><small>{hidden}</small></span>{badge}</a>')
        blocks.append(f'<div class="stage"><h4>{_esc(catalog.STAGES.get(stage, stage))}</h4>{"".join(items)}</div>')
    return "".join(blocks) or '<p class="note" style="padding:16px">Продуктов пока нет.</p>'


def _chat_row(chat: dict, titles: dict[str, str]) -> str:
    who = _esc(chat.get("full_name") or chat["user_id"])
    if chat.get("username"):
        who += f' <span class="meta">@{_esc(chat["username"])}</span>'
    recs = json.loads(chat.get("proposal") or "[]")
    path = " → ".join(_esc(titles.get(r["product_id"], r["product_id"])) for r in recs)
    status = {"active": "в процессе", "done": "завершён"}.get(chat["status"], chat["status"])
    lines = "".join(f'<div class="line"><b>{"ИИ" if who_ == "ai" else "Человек"}:</b> {_esc(text)}</div>'
                    for who_, text in ai.transcript(chat))
    return f"""<div class="chat"><div><a href="/admin/leads/{chat['user_id']}"><b>{who}</b></a>
<span class="meta"> · {_fmt_time(chat['updated_at'])} · ответов: {chat['answers']} · {status}</span></div>
{f'<div class="line">{_esc(chat["summary"])}</div>' if chat.get("summary") else ""}
{f'<div class="line"><b>Путь:</b> {path}</div>' if path else ""}
<details><summary>Весь разговор</summary>{lines}</details></div>"""


async def ai_page(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    await catalog.ensure_seeded(db, _cfg())
    courses = await db.courses_all()
    titles = {c["id"]: c["title"] for c in courses}
    chats = "".join(_chat_row(c, titles) for c in await db.ai_chats_recent(50))
    body = f"""<header><h1>ИИ-консультант</h1>{nav("ai")}</header>{_flash(request)}
<p class="note">После /start консультант задаёт 3–5 вопросов, подстраивая каждый следующий под ответы,
и предлагает личный путь: с чего начать, что дальше и что потом. Он знает только то, что написано здесь
и в базах знаний продуктов.</p>
{_status_block()}
<form method="post" action="/admin/ai" class="card section" data-guard>
<h3>Направление экосистемы</h3>
<p>Миссия, путь человека от первого шага к глубокой работе, роль каждого типа продукта и принципы рекомендаций.
Это общая рамка для всех продуктов — консультант опирается на неё, когда собирает путь.</p>
<textarea class="input big" name="ecosystem" maxlength="20000">{_esc(await catalog.ecosystem_text(db))}</textarea>
<label class="field"><span>Приветствие перед подбором</span>
<textarea class="input mid" name="greeting" maxlength="1000">{_esc(await agent_bot.greeting(db))}</textarea></label>
<div class="savebar"><span class="dirty">● Есть несохранённые изменения</span><button class="btn primary">Сохранить</button></div>
</form>
<h2>Карта продуктов</h2>
<div class="card">{await _product_map(courses)}</div>
<p class="note">Этап и базу знаний задают на странице продукта.</p>
<h2>Последние подборы</h2>
<div class="card">{chats or '<p class="note" style="padding:16px">Пока никто не проходил подбор.</p>'}</div>"""
    return _page("ИИ-консультант", body, f"<style>{PAGE_CSS}</style>")


async def ai_save(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    form = await request.form()
    ecosystem = str(form.get("ecosystem", "")).replace("\r\n", "\n").strip()[:20000]
    greeting = str(form.get("greeting", "")).replace("\r\n", "\n").strip()[:1000]
    if not greeting:
        return _redirect("/admin/ai", err="Приветствие не может быть пустым.")
    db = _db()
    await db.kv_set(catalog.ECOSYSTEM_KEY, ecosystem)
    await db.kv_set(agent_bot.GREETING_KEY, greeting)
    return _redirect("/admin/ai", msg="Сохранено.")


routes = [
    Route("/admin/ai", ai_page, methods=["GET"]),
    Route("/admin/ai", ai_save, methods=["POST"]),
]
