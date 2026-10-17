"""Admin panel: journey designer for a course (/admin/courses/<id>/journey)."""

import html
import re
from urllib.parse import quote

from aiogram.types import BufferedInputFile
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from . import content, journey
from .courses_admin import QUILL_HEAD, _allowed, _esc
from .panel import MAX_PHOTO_BYTES, _bot, _cfg, _db, _flash, _page, _redirect, _upload_photo, file_picker

MAX_VIDEO_BYTES = 4 * 1024 * 1024

PAGE_CSS = """
.map{overflow:auto;padding:12px;-webkit-overflow-scrolling:touch;position:relative}
.map svg{height:auto}
.map.zoomed svg{max-width:none!important;width:auto}
.map-tools{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:0 0 8px}
.step{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:14px 16px;
 border-top:1px solid var(--line);color:var(--ink);text-decoration:none}
.step:first-child{border-top:0}.step:hover{background:var(--chip)}
.step .meta{font-size:13px;color:var(--muted);margin-top:2px}
.badge.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
#editor{min-height:200px;background:var(--card);color:var(--ink)}
.ql-toolbar.ql-snow,.ql-container.ql-snow{border-color:var(--line)!important}
.ql-toolbar.ql-snow{border-radius:10px 10px 0 0}.ql-container.ql-snow{border-radius:0 0 10px 10px;font-size:15px}
.ql-snow .ql-stroke{stroke:var(--ink)}.ql-snow .ql-fill{fill:var(--ink)}
.btnrows{display:grid;gap:10px}
.btnrow{display:grid;grid-template-columns:minmax(160px,1.3fr) minmax(170px,1fr) minmax(160px,1fr) auto;gap:8px;align-items:center;
 padding:10px;border:1px solid var(--line);border-radius:12px}
.btnrow .tools{display:flex;gap:6px}
.btnrow .icon-btn{width:40px;height:40px}
@media (max-width:640px){.btnrow{grid-template-columns:1fr}.btnrow .tools{justify-content:flex-end}}
.tg{background:#e7ebe3;border-radius:14px;padding:12px;max-width:420px}
@media (prefers-color-scheme:dark){.tg{background:#1c2420}}
.tg .bubble{background:var(--card);border-radius:12px;padding:10px 12px;white-space:pre-wrap;font-size:15px}
.tg .kb{display:grid;gap:4px;margin-top:4px}
.tg .kb span{display:block;text-align:center;background:color-mix(in srgb,var(--accent) 70%,transparent);color:#fff;
 border-radius:8px;padding:8px;font-size:14px}
"""

MERMAID_HEAD = """<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs';
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
mermaid.initialize({startOnLoad: true, securityLevel: 'loose', theme: dark ? 'dark' : 'neutral',
  flowchart: {htmlLabels: true, curve: 'basis', useMaxWidth: true}});
</script>"""


def _m(text: str, limit: int = 38) -> str:
    """Text safe inside a Mermaid quoted label."""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return html.escape(text, quote=False).replace('"', "#quot;")


def reachable(steps: list[dict], start: str | None) -> set[str]:
    links = {s["key"]: [b.target for b in journey.load_buttons(s["buttons"]) if b.action == "goto"] for s in steps}
    seen, todo = set(), [start] if start else []
    while todo:
        key = todo.pop()
        if key in seen or key not in links:
            continue
        seen.add(key)
        todo.extend(links[key])
    return seen


def mermaid_map(course_id: str, steps: list[dict], start: str | None) -> str:
    lines = ["flowchart TD"]
    used_actions = set()
    ok = reachable(steps, start)
    for s in steps:
        key = s["key"]
        lines.append(f'  {key}["{_m(s["title"])}"]')
        lines.append(f'  click {key} href "/admin/courses/{course_id}/journey/{key}"')
        if key == start:
            lines.append(f"  class {key} start")
        elif key not in ok:
            lines.append(f"  class {key} orphan")
        for b in journey.load_buttons(s["buttons"]):
            label = _m(b.label, 24)
            if b.action == "goto":
                lines.append(f'  {key} -->|"{label}"| {b.target}')
            else:
                node = f"act_{b.action}"
                used_actions.add(b.action)
                lines.append(f'  {key} -.->|"{label}"| {node}')
    for action in sorted(used_actions):
        name, icon = journey.ACTIONS[action]
        lines.append(f'  act_{action}(["{icon} {_m(name)}"])')
        lines.append(f"  class act_{action} action")
    lines += [
        "  classDef start stroke:#2f9e5b,stroke-width:3px",
        "  classDef orphan stroke-dasharray:5 4,opacity:.7",
        "  classDef action fill:transparent,stroke-dasharray:3 3",
    ]
    return "\n".join(lines)


def _text_preview(step: dict, limit: int = 90) -> str:
    plain = " ".join(html.unescape(re.sub(r"<[^>]+>", "", step["html"] or "")).split())
    return plain[:limit] + ("…" if len(plain) > limit else "")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


async def journey_page(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    c = await db.course_get(request.path_params["course_id"])
    if c is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    cid = _esc(c["id"])
    steps = await db.steps_for(c["id"])
    start = c.get("start_step") if c.get("start_step") in {s["key"] for s in steps} else None
    ok = reachable(steps, start)

    rows = []
    for s in steps:
        buttons = journey.load_buttons(s["buttons"])
        badges = []
        if s["key"] == start:
            badges.append('<span class="badge on">старт</span>')
        elif s["key"] not in ok:
            badges.append('<span class="badge warn">недоступен</span>')
        if s.get("photo"):
            badges.append('<span class="badge">фото</span>')
        if s.get("video_note"):
            badges.append('<span class="badge">кружочек</span>')
        meta = f"кнопок: {len(buttons)}" + (" · " + _esc(_text_preview(s)) if s["html"] else "")
        rows.append(f'<a class="step" href="/admin/courses/{cid}/journey/{_esc(s["key"])}"><span><b>{_esc(s["title"])}</b>'
                    f'<div class="meta">{meta}</div></span><span class="badges">{"".join(badges)}</span></a>')

    if steps:
        start_options = "".join(
            f'<option value="{_esc(s["key"])}"{" selected" if s["key"] == start else ""}>{_esc(s["title"])}</option>'
            for s in steps)
        map_block = f"""<div class="map-tools"><span class="note" style="margin:0">Нажмите на шаг на схеме, чтобы его изменить.
Пунктир — шаг, до которого не ведёт ни одна кнопка.</span>
<button type="button" class="btn" onclick="const m=document.querySelector('.map');m.classList.toggle('zoomed');
this.textContent=m.classList.contains('zoomed')?'Целиком':'🔍 Крупнее'">🔍 Крупнее</button></div>
<div class="card map"><pre class="mermaid">{_esc(mermaid_map(c["id"], steps, start))}</pre></div>"""
        settings = f"""<form method="post" action="/admin/courses/{cid}/journey/start" class="card section">
<label class="field"><span>Первый шаг — с него начинается курс</span>
<select class="input" name="start" onchange="this.form.submit()">{start_options}</select></label>
<p class="note" style="margin:0">Проверить в боте: откройте <code>t.me/&lt;бот&gt;?start=c_{cid}</code></p></form>"""
        template = ""
    else:
        map_block = '<p class="note">Шагов пока нет.</p>'
        settings = ""
        template = f"""<form method="post" action="/admin/courses/{cid}/journey/template" class="card section">
<h3>Начать с шаблона</h3><p>Скопировать путь «НейроФинансов»: все тексты, фото, кружочек и кнопки. Потом всё можно изменить.</p>
<div><button class="btn primary">Скопировать путь «НейроФинансов»</button></div></form>"""

    body = f"""<header><h1>Путь: {_esc(c["title"])}</h1><a class="btn" href="/admin/courses/{cid}">← Курс</a></header>
{_flash(request)}
<p class="note">Путь — это сообщения, которые бот показывает по шагам. Каждая кнопка ведёт на другой шаг
или запускает действие: выбор группы и оплату, лист ожидания, вопрос менеджеру…</p>
{template}{map_block}{settings}
<h2>Шаги ({len(steps)})</h2>
<div class="card">{"".join(rows) or '<p class="note" style="padding:16px">Пусто.</p>'}</div>
<form method="post" action="/admin/courses/{cid}/journey/new" class="card section" style="margin-top:14px">
<div class="grid2"><label class="field"><span>Новый шаг</span>
<input class="input" type="text" name="title" required maxlength="80" placeholder="Например: Отзывы участников"></label></div>
<div><button class="btn">＋ Добавить шаг</button></div></form>"""
    return _page(f"Путь: {c['title']}", body, MERMAID_HEAD + f"<style>{PAGE_CSS}</style>")


async def journey_template(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    course_id = request.path_params["course_id"]
    if await db.course_get(course_id) is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    if await db.steps_for(course_id):
        return _redirect(f"/admin/courses/{course_id}/journey", err="У курса уже есть шаги — шаблон не применён.")
    n = await journey.apply_template(db, course_id)
    return _redirect(f"/admin/courses/{course_id}/journey", msg=f"Скопировано шагов: {n}.")


async def journey_new_step(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    course_id = request.path_params["course_id"]
    c = await db.course_get(course_id)
    if c is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    title = str((await request.form()).get("title", "")).strip()[:80]
    if not title:
        return _redirect(f"/admin/courses/{course_id}/journey", err="Укажите название шага.")
    steps = await db.steps_for(course_id)
    key = journey.new_key({s["key"] for s in steps})
    await db.step_save(course_id, key, title=title, html="", buttons="[]", sort_order=len(steps))
    if not c.get("start_step"):
        await db.course_save(course_id, start_step=key)
    return _redirect(f"/admin/courses/{course_id}/journey/{key}", msg="Шаг создан — добавьте текст и кнопки.")


async def journey_set_start(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    course_id = request.path_params["course_id"]
    key = str((await request.form()).get("start", ""))
    if await db.step_get(course_id, key) is None:
        return _redirect(f"/admin/courses/{course_id}/journey", err="Шаг не найден.")
    await db.course_save(course_id, start_step=key)
    return _redirect(f"/admin/courses/{course_id}/journey", msg="Первый шаг изменён.")


# ---------------------------------------------------------------------------
# Step editor
# ---------------------------------------------------------------------------

STEP_JS = """<script>
const actionsWithTarget = {goto: 'target', url: 'url'};
function syncRow(row) {
  const a = row.querySelector('[name=b_action]').value;
  row.querySelector('[name=b_target]').hidden = a !== 'goto';
  row.querySelector('[name=b_url]').hidden = a !== 'url';
  row.querySelector('.noarg').hidden = a === 'goto' || a === 'url';
}
function rows() { return document.querySelectorAll('.btnrow'); }
document.querySelectorAll('.btnrow').forEach(syncRow);
document.addEventListener('change', e => { if (e.target.name === 'b_action') syncRow(e.target.closest('.btnrow')); });
document.addEventListener('click', e => {
  const t = e.target.closest('[data-btn]'); if (!t) return;
  const row = t.closest('.btnrow'), list = document.querySelector('.btnrows');
  const form = document.getElementById('step');
  if (t.dataset.btn === 'add') {
    if (rows().length >= %(max)d) { alert('Не больше %(max)d кнопок'); return; }
    const tpl = document.getElementById('btn-template').content.firstElementChild.cloneNode(true);
    list.append(tpl); syncRow(tpl); tpl.querySelector('input').focus();
  }
  if (t.dataset.btn === 'del') row.remove();
  if (t.dataset.btn === 'up' && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
  if (t.dataset.btn === 'down' && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
  form.dispatchEvent(new Event('dirty'));
});
</script>""" % {"max": journey.MAX_BUTTONS}


def _button_row(b: journey.Button | None, steps: list[dict], current: str) -> str:
    b = b or journey.Button("", "goto", "")
    actions = "".join(f'<option value="{k}"{" selected" if k == b.action else ""}>{icon} {_esc(name)}</option>'
                      for k, (name, icon) in journey.ACTIONS.items())
    targets = '<option value="">— выберите шаг —</option>' + "".join(
        f'<option value="{_esc(s["key"])}"{" selected" if s["key"] == b.target and b.action == "goto" else ""}>'
        f'{_esc(s["title"])}{" (этот шаг)" if s["key"] == current else ""}</option>' for s in steps)
    url = _esc(b.target) if b.action == "url" else ""
    return f"""<div class="btnrow">
<input class="input" type="text" name="b_label" value="{_esc(b.label)}" maxlength="{journey.MAX_LABEL}" placeholder="Текст кнопки" aria-label="Текст кнопки">
<select class="input" name="b_action" aria-label="Действие">{actions}</select>
<span><select class="input" name="b_target" aria-label="Куда ведёт">{targets}</select>
<input class="input" type="url" name="b_url" value="{url}" placeholder="https://…" aria-label="Ссылка">
<span class="noarg note" style="margin:0">—</span></span>
<span class="tools"><button type="button" class="icon-btn" data-btn="up" title="Выше" aria-label="Выше">↑</button>
<button type="button" class="icon-btn" data-btn="down" title="Ниже" aria-label="Ниже">↓</button>
<button type="button" class="icon-btn" data-btn="del" title="Удалить кнопку" aria-label="Удалить кнопку">✕</button></span>
</div>"""


def _tg_preview(step: dict) -> str:
    buttons = journey.load_buttons(step["buttons"])
    media = "📷 фото · " if step.get("photo") else ("⏺ кружочек · " if step.get("video_note") else "")
    text = html.unescape(re.sub(r"<[^>]+>", "", step["html"] or "")).strip()
    kb = "".join(f"<span>{_esc(b.label)}</span>" for b in buttons)
    return (f'<div class="tg"><div class="bubble">{_esc(media)}{_esc(text[:600]) or "…"}'
            f'{"…" if len(text) > 600 else ""}</div><div class="kb">{kb}</div></div>')


async def step_page(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    course_id, key = request.path_params["course_id"], request.path_params["key"]
    c = await db.course_get(course_id)
    s = await db.step_get(course_id, key)
    if c is None or s is None:
        return _redirect(f"/admin/courses/{course_id}/journey", err="Шаг не найден.")
    cid, skey = _esc(course_id), _esc(key)
    steps = await db.steps_for(course_id)
    buttons = journey.load_buttons(s["buttons"])
    rows = "".join(_button_row(b, steps, key) for b in buttons)
    photo_url = f"/admin/courses/{cid}/journey/{skey}/photo?v={quote(s['photo'][-12:])}" if s.get("photo") else None
    media_state = ("Сейчас: фото" if s.get("photo") else "Сейчас: кружочек" if s.get("video_note") else "Сейчас: без медиа")
    remove = ('<label class="toggle"><input type="checkbox" name="remove_media" value="1">Убрать фото/кружочек</label>'
              if s.get("photo") or s.get("video_note") else "")
    is_start = c.get("start_step") == key
    body = f"""<header><h1>{_esc(s["title"])}</h1><a class="btn" href="/admin/courses/{cid}/journey">← Путь</a></header>
{_flash(request)}
<form method="post" enctype="multipart/form-data" id="step" data-guard>
<div class="card section"><h3>Сообщение</h3>
<label class="field"><span>Название шага <span class="hint">— только для админки</span></span>
<input class="input" type="text" name="title" value="{_esc(s["title"])}" required maxlength="80"></label>
<div id="editor">{content.telegram_to_editor(s["html"] or "")}</div>
<div class="counter" id="counter"></div>
<input type="hidden" name="html" id="html">
</div>
<div class="card section"><h3>Фото или кружочек</h3><p>{media_state}. Фото до 4 МБ; кружочек — квадратное видео MP4 до 60 сек и 4 МБ.</p>
<div class="row" style="align-items:flex-start">{file_picker("photo", "Выбрать фото", photo_url)}
<label class="file"><input type="file" name="video_note" accept="video/mp4"><span class="btn">⏺ Загрузить кружочек</span>
<span class="file-name">Файл не выбран</span></label>{remove}</div>
</div>
<div class="card section"><h3>Кнопки</h3><p>До {journey.MAX_BUTTONS} кнопок. Каждая ведёт на шаг или запускает действие.</p>
<div class="btnrows">{rows}</div>
<div><button type="button" class="btn" data-btn="add">＋ Добавить кнопку</button></div>
</div>
<div class="card section"><h3>Как это выглядит</h3>{_tg_preview(s)}
<p class="note" style="margin:0">Предпросмотр после сохранения. «Сохранить и прислать мне» отправит шаг в ваш Telegram с рабочими кнопками.</p></div>
<div class="savebar"><span class="dirty">● Есть несохранённые изменения</span>
<span class="row"><button class="btn" name="action" value="test">Сохранить и прислать мне</button>
<button class="btn primary" name="action" value="save">Сохранить</button></span></div>
</form>
<template id="btn-template">{_button_row(None, steps, key)}</template>
<h2>Удаление</h2>
<form method="post" class="card section">
<p class="note" style="margin:0">{"Это первый шаг курса — сначала выберите другой первый шаг." if is_start else
"Шаг можно удалить, только если на него не ведёт ни одна кнопка."}</p>
<div><button class="btn danger" name="action" value="delete" {"disabled" if is_start else ""}
 onclick="return confirm('Удалить шаг «{_esc(s["title"])}»?')">Удалить шаг</button></div></form>
<script>
const quill = new Quill('#editor', {{theme: 'snow', modules: {{toolbar: [
  ['bold', 'italic', 'underline', 'strike'], ['link', 'blockquote', 'code'], ['clean']]}}}});
const counter = document.getElementById('counter'), form = document.getElementById('step');
function count() {{ const n = quill.getText().replace(/\\n$/, '').length;
  counter.textContent = n + ' / 4096 символов'; counter.classList.toggle('over', n > 4096); }}
quill.on('text-change', (d, o, src) => {{ count(); if (src === 'user') form.dispatchEvent(new Event('dirty')); }}); count();
form.addEventListener('submit', () => {{ document.getElementById('html').value = quill.root.innerHTML; }});
</script>{STEP_JS}"""
    return _page(s["title"], body, QUILL_HEAD + f"<style>{PAGE_CSS}</style>")


async def _upload_video_note(data: bytes, filename: str) -> str:
    config = _cfg()
    target = config.admin_chat_id or next(iter(config.admin_ids), None)
    if not target:
        raise ValueError("Чтобы загружать видео, сначала укажите ADMIN_CHAT_ID.")
    bot = _bot()
    msg = await bot.send_video_note(target, BufferedInputFile(data, filename=filename or "note.mp4"))
    try:
        await bot.delete_message(target, msg.message_id)
    except Exception:
        pass
    if not msg.video_note:
        raise ValueError("Telegram не принял видео как кружочек (нужно квадратное MP4).")
    return msg.video_note.file_id


async def step_save(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    course_id, key = request.path_params["course_id"], request.path_params["key"]
    c = await db.course_get(course_id)
    s = await db.step_get(course_id, key)
    overview = f"/admin/courses/{course_id}/journey"
    if c is None or s is None:
        return _redirect(overview, err="Шаг не найден.")
    here = f"{overview}/{key}"
    form = await request.form()
    action = form.get("action", "save")
    steps = await db.steps_for(course_id)

    if action == "delete":
        if c.get("start_step") == key:
            return _redirect(here, err="Это первый шаг курса — сначала выберите другой первый шаг.")
        referrers = [x["title"] for x in steps if x["key"] != key and any(
            b.action == "goto" and b.target == key for b in journey.load_buttons(x["buttons"]))]
        if referrers:
            return _redirect(here, err="На этот шаг ведут кнопки из шагов: " + ", ".join(referrers)
                             + ". Сначала измените их.")
        await db.step_delete(course_id, key)
        return _redirect(overview, msg="Шаг удалён.")

    title = str(form.get("title", "")).strip()[:80]
    if not title:
        return _redirect(here, err="Название шага не может быть пустым.")
    text = content.editor_to_telegram(str(form.get("html", "")))
    if content.visible_length(text) > content.TEXT_LIMIT:
        return _redirect(here, err="Текст длиннее 4096 символов.")
    try:
        buttons = journey.parse_buttons(form.getlist("b_label"), form.getlist("b_action"),
                                        form.getlist("b_target"), form.getlist("b_url"),
                                        {x["key"] for x in steps})
    except journey.JourneyError as exc:
        return _redirect(here, err=str(exc))

    photo, video_note = s.get("photo"), s.get("video_note")
    if form.get("remove_media"):
        photo = video_note = None
    try:
        upload = form.get("photo")
        if upload is not None and getattr(upload, "filename", ""):
            data = await upload.read()
            if len(data) > MAX_PHOTO_BYTES:
                return _redirect(here, err="Фото больше 4 МБ.")
            if data:
                photo, video_note = await _upload_photo(data, upload.filename), None
        upload = form.get("video_note")
        if upload is not None and getattr(upload, "filename", ""):
            data = await upload.read()
            if len(data) > MAX_VIDEO_BYTES:
                return _redirect(here, err="Видео больше 4 МБ.")
            if data:
                video_note, photo = await _upload_video_note(data, upload.filename), None
    except Exception as exc:
        return _redirect(here, err=f"Не удалось загрузить файл: {exc}")

    if not text.strip() and not photo and not video_note:
        return _redirect(here, err="Шаг не может быть пустым: нужен текст, фото или кружочек.")
    await db.step_save(course_id, key, title=title, html=text, photo=photo, video_note=video_note,
                       buttons=journey.dump_buttons(buttons))

    if action == "test":
        config = _cfg()
        if not config.admin_chat_id:
            return _redirect(here, err="Сохранено, но превью некуда отправить: укажите ADMIN_CHAT_ID.")
        try:
            await _bot().send_message(config.admin_chat_id, f"👁 Превью шага: <b>{html.escape(title)}</b>")
            await journey.send_step(_bot(), config.admin_chat_id, course_id, await db.step_get(course_id, key))
        except Exception as exc:
            return _redirect(here, err=f"Сохранено, но Telegram не принял сообщение: {exc}")
        return _redirect(here, msg="Сохранено. Шаг отправлен вам в Telegram.")
    return _redirect(here, msg="Шаг сохранён.")


async def step_photo(request: Request) -> Response:
    from .panel import _authed
    if not _authed(request):
        return Response(status_code=403)
    s = await _db().step_get(request.path_params["course_id"], request.path_params["key"])
    if not s or not s.get("photo"):
        return Response(status_code=404)
    bot = _bot()
    file = await bot.get_file(s["photo"])
    data = await bot.download_file(file.file_path)
    return Response(data.read(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


routes = [
    Route("/admin/courses/{course_id}/journey", journey_page, methods=["GET"]),
    Route("/admin/courses/{course_id}/journey/template", journey_template, methods=["POST"]),
    Route("/admin/courses/{course_id}/journey/new", journey_new_step, methods=["POST"]),
    Route("/admin/courses/{course_id}/journey/start", journey_set_start, methods=["POST"]),
    Route("/admin/courses/{course_id}/journey/{key}", step_page, methods=["GET"]),
    Route("/admin/courses/{course_id}/journey/{key}", step_save, methods=["POST"]),
    Route("/admin/courses/{course_id}/journey/{key}/photo", step_photo, methods=["GET"]),
]
