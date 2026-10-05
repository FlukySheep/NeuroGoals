"""Admin panel (/admin): edit bot messages in a WYSIWYG editor and attach photos.

Login: ADMIN_PASSWORD. Photos are uploaded to Telegram (sent to the admin chat
and deleted right away) and stored as file_ids, so no file storage is needed.
"""

import hashlib
import hmac
import html
import logging
import os
import time
from collections import OrderedDict
from urllib.parse import quote

from aiogram import Bot
from aiogram.types import BufferedInputFile
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from . import content
from . import texts as t
from .config import Config
from .db import Database

log = logging.getLogger(__name__)

COOKIE = "nf_admin"
SESSION_DAYS = 30
MAX_PHOTO_BYTES = 4 * 1024 * 1024  # Vercel request body limit is 4.5 MB

# Filled in by web.py so the panel shares the bot, DB and config.
deps: dict = {}


def _cfg() -> Config:
    return deps["config"]()


def _db() -> Database:
    return deps["db"]()


def _bot() -> Bot:
    return deps["bot"]()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _password() -> str:
    return os.getenv("ADMIN_PASSWORD", "")


def _sign(value: str) -> str:
    key = hashlib.sha256((_password() + "|" + os.getenv("WEBHOOK_SECRET", "")).encode()).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def _session_cookie() -> str:
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{_sign(expires)}"


def _authed(request: Request) -> bool:
    if not _password():
        return False
    value = request.cookies.get(COOKIE, "")
    expires, _, sig = value.partition(".")
    return expires.isdigit() and int(expires) > time.time() and hmac.compare_digest(sig, _sign(expires))


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f4ef;--card:#fff;--ink:#1d1b18;--muted:#6f6a62;--line:#e4dfd5;--accent:#2d5a3d;--accent-ink:#fff;--warn:#9a3b1c;--chip:#eef2ec}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#211f1c;--ink:#ece8e1;--muted:#a19b91;--line:#36322c;--accent:#7fb48f;--accent-ink:#10140f;--warn:#e48a6a;--chip:#2a3129}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:16px}
header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin:8px 0 20px}
h1{font-size:22px;margin:0}h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:28px 0 8px}
a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px}
.list a{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 16px;text-decoration:none;color:var(--ink);border-top:1px solid var(--line)}
.list a:first-child{border-top:0}.list a:hover{background:var(--chip)}
.badges{display:flex;gap:6px;flex-shrink:0}.badge{font-size:12px;padding:2px 8px;border-radius:99px;background:var(--chip);color:var(--muted)}
.badge.on{background:var(--accent);color:var(--accent-ink)}
.btn{appearance:none;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:10px 16px;border-radius:10px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.btn.danger{color:var(--warn)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.flash{padding:12px 16px;border-radius:10px;background:var(--chip);margin-bottom:16px}
.flash.err{color:var(--warn)}
.note{color:var(--muted);font-size:14px;margin:4px 0 12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.chip{border:1px dashed var(--accent);color:var(--accent);background:transparent;border-radius:8px;padding:3px 8px;font:13px ui-monospace,Menlo,monospace;cursor:pointer}
#editor{min-height:280px;font-size:16px;background:var(--card);color:var(--ink)}
.ql-toolbar.ql-snow,.ql-container.ql-snow{border-color:var(--line)!important}
.ql-toolbar.ql-snow{border-radius:12px 12px 0 0;background:var(--card)}.ql-container.ql-snow{border-radius:0 0 12px 12px}
.ql-snow .ql-stroke{stroke:var(--ink)}.ql-snow .ql-fill{fill:var(--ink)}.ql-snow .ql-picker{color:var(--ink)}
.ql-editor blockquote{border-left:3px solid var(--accent)!important;color:var(--muted)}
.counter{font-size:13px;color:var(--muted);text-align:right;margin-top:6px}.counter.over{color:var(--warn)}
.photo{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;padding:16px}
.photo img{max-width:220px;max-height:220px;border-radius:10px;border:1px solid var(--line)}
input[type=password]{width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit}
.sticky{position:sticky;bottom:0;background:linear-gradient(transparent,var(--bg) 30%);padding:20px 0 12px}
"""


def _page(title: str, body: str, head: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style>{head}</head>
<body><div class="wrap">{body}</div></body></html>"""
    )


def nav(active: str) -> str:
    links = [("messages", "/admin", "Сообщения"), ("leads", "/admin/leads", "Лиды")]
    items = "".join(
        f'<a class="btn{" primary" if key == active else ""}" href="{href}">{title}</a>' for key, href, title in links
    )
    return f'<div class="row">{items}<a class="btn" href="/admin/logout">Выйти</a></div>'


def _flash(request: Request) -> str:
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    if err:
        return f'<div class="flash err">{html.escape(err)}</div>'
    if msg:
        return f'<div class="flash">{html.escape(msg)}</div>'
    return ""


def _redirect(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    query = f"?msg={quote(msg)}" if msg else (f"?err={quote(err)}" if err else "")
    return RedirectResponse(path + query, status_code=303)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def login_page(request: Request) -> Response:
    if not _password():
        return _page("Админка", "<h1>Админка не настроена</h1><p class='note'>Добавьте переменную "
                     "<b>ADMIN_PASSWORD</b> в Vercel (Settings → Environment Variables) и сделайте Redeploy.</p>")
    if _authed(request):
        return RedirectResponse("/admin", status_code=303)
    return _page("Вход", f"""
<header><h1>НейроФинансы · вход</h1></header>{_flash(request)}
<form method="post" action="/admin/login" class="card" style="padding:16px">
<p><input type="password" name="password" placeholder="Пароль" autofocus required></p>
<button class="btn primary" type="submit">Войти</button></form>""")


async def login(request: Request) -> Response:
    form = await request.form()
    if not _password() or not hmac.compare_digest(str(form.get("password", "")), _password()):
        return _redirect("/admin/login", err="Неверный пароль")
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(COOKIE, _session_cookie(), max_age=SESSION_DAYS * 86400,
                        httponly=True, secure=True, samesite="strict")
    return response


async def logout(request: Request) -> Response:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


async def index(request: Request) -> Response:
    if not _authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    rows = {r["key"]: r for r in await _db().content_all()}
    sections: OrderedDict[str, list[str]] = OrderedDict()
    for d in content.CATALOG.values():
        row = rows.get(d.key)
        badges = []
        if row and row["html"] is not None:
            badges.append('<span class="badge on">изменено</span>')
        if row and row["photo"]:
            badges.append('<span class="badge on">фото</span>')
        if d.key.startswith("faq_") and not ((row and row["html"]) or d.default):
            badges.append('<span class="badge">скрыт</span>')
        sections.setdefault(d.section, []).append(
            f'<a href="/admin/edit/{d.key}"><span>{html.escape(d.title)}</span>'
            f'<span class="badges">{"".join(badges)}</span></a>'
        )
    body = "".join(
        f"<h2>{html.escape(name)}</h2><div class='card list'>{''.join(items)}</div>" for name, items in sections.items()
    )
    return _page("Сообщения бота", f"""
<header><h1>Сообщения бота</h1>{nav("messages")}</header>
{_flash(request)}
<p class="note">Нажмите на сообщение, чтобы изменить текст или добавить картинку.
Изменения сразу появляются в боте.</p>{body}""")


def _sample_vars() -> dict[str, str]:
    group = next(iter(t.GROUPS.values()))
    return {
        "group": group.title, "dates": group.dates, "time": t.TIME_TEXT,
        "amount": t.CURRENCY_AMOUNT["eur"], "manager": _cfg().manager_username,
        "group_access": "Сейчас вы можете перейти в закрытую группу участников.",
    }


async def edit_page(request: Request) -> Response:
    if not _authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    key = request.path_params["key"]
    d = content.CATALOG.get(key)
    if d is None:
        return _redirect("/admin", err="Сообщение не найдено")
    row = await _db().content_get(key)
    text = row["html"] if row and row["html"] is not None else d.default
    photo = row["photo"] if row else None
    changed = bool(row and row["html"] is not None)

    chips = "".join(
        f'<button type="button" class="chip" data-var="{{{v}}}" title="{html.escape(content.VARIABLE_HELP.get(v, ""))}">'
        f"{{{v}}}</button>" for v in d.variables
    )
    vars_block = (
        f'<p class="note">Подставляется автоматически (нажмите, чтобы вставить):</p><div class="chips">{chips}</div>'
        if d.variables else ""
    )
    fallback = ""
    if not photo and d.fallback_slot:
        fallback = "<p class='note'>Сейчас используется картинка, загруженная через бота (/media), если она есть.</p>"
    photo_block = (
        f'<img src="/admin/photo/{key}?v={quote(photo[-12:])}" alt="">'
        f'<label class="row"><input type="checkbox" name="remove_photo" value="1"> Убрать картинку</label>'
        if photo else "<span class='note'>Картинки нет.</span>"
    )
    reset = (
        '<button class="btn danger" name="action" value="reset" '
        'onclick="return confirm(\'Вернуть исходный текст и убрать картинку?\')">Сбросить</button>'
        if changed or photo else ""
    )
    head = """<link href="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"></script>"""
    body = f"""
<header><h1>{html.escape(d.title)}</h1><a class="btn" href="/admin">← Назад</a></header>
{_flash(request)}
{f'<p class="note">{html.escape(d.note)}</p>' if d.note else ''}
<form method="post" enctype="multipart/form-data" id="form">
{vars_block}
<div id="editor">{content.telegram_to_editor(text)}</div>
<div class="counter" id="counter"></div>
<input type="hidden" name="html" id="html">
<h2>Картинка</h2>
<div class="card photo">{photo_block}
<div><input type="file" name="photo" accept="image/jpeg,image/png,image/webp">
<p class="note">JPG/PNG до 4 МБ. Если текст длиннее 1024 символов, картинка придёт отдельным сообщением над текстом.</p>
{fallback}</div></div>
<div class="sticky row">
<button class="btn primary" name="action" value="save">Сохранить</button>
<button class="btn" name="action" value="test">Сохранить и прислать мне</button>
{reset}
</div></form>
<script>
const quill = new Quill('#editor', {{theme: 'snow', modules: {{toolbar: [
  ['bold', 'italic', 'underline', 'strike'], ['link', 'blockquote', 'code'], ['clean']]}}}});
const counter = document.getElementById('counter');
function count() {{
  const n = quill.getText().replace(/\\n$/, '').length;
  const hasPhoto = {str(bool(photo)).lower()} || document.querySelector('input[name=photo]').files.length > 0;
  counter.textContent = n + ' / 4096 символов' + (hasPhoto && n > 1024 ? ' · картинка уйдёт отдельным сообщением' : '');
  counter.classList.toggle('over', n > 4096);
}}
quill.on('text-change', count); document.querySelector('input[name=photo]').addEventListener('change', count); count();
document.querySelectorAll('.chip').forEach(b => b.addEventListener('click', () => {{
  const r = quill.getSelection(true); quill.insertText(r ? r.index : quill.getLength() - 1, b.dataset.var); }}));
document.getElementById('form').addEventListener('submit', () => {{
  document.getElementById('html').value = quill.root.innerHTML; }});
</script>"""
    return _page(d.title, body, head)


async def _upload_photo(data: bytes, filename: str) -> str:
    config = _cfg()
    target = config.admin_chat_id or next(iter(config.admin_ids), None)
    if not target:
        raise ValueError("Чтобы загружать картинки, сначала укажите ADMIN_CHAT_ID в Vercel.")
    bot = _bot()
    msg = await bot.send_photo(target, BufferedInputFile(data, filename=filename or "photo.jpg"))
    try:
        await bot.delete_message(target, msg.message_id)
    except Exception:
        pass
    return msg.photo[-1].file_id


async def edit_save(request: Request) -> Response:
    if not _authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    key = request.path_params["key"]
    d = content.CATALOG.get(key)
    if d is None:
        return _redirect("/admin", err="Сообщение не найдено")
    db = _db()
    form = await request.form()
    action = form.get("action", "save")
    here = f"/admin/edit/{key}"

    if action == "reset":
        await db.content_delete(key)
        return _redirect(here, msg="Исходный текст восстановлен.")

    text = content.editor_to_telegram(str(form.get("html", "")))
    if content.visible_length(text) > content.TEXT_LIMIT:
        return _redirect(here, err=f"Слишком длинный текст: {content.visible_length(text)} символов (максимум 4096).")

    row = await db.content_get(key)
    photo = row["photo"] if row else None
    if form.get("remove_photo"):
        photo = None
    upload = form.get("photo")
    if upload is not None and getattr(upload, "filename", ""):
        data = await upload.read()
        if len(data) > MAX_PHOTO_BYTES:
            return _redirect(here, err="Картинка больше 4 МБ — уменьшите её.")
        if data:
            try:
                photo = await _upload_photo(data, upload.filename)
            except Exception as exc:
                log.exception("Photo upload failed")
                return _redirect(here, err=f"Не удалось загрузить картинку: {exc}")

    if not text.strip() and not photo and not key.startswith("faq_"):
        return _redirect(here, err="Сообщение не может быть пустым: нужен текст или картинка.")

    stored_html = None if text == content.normalized_default(key) else text
    if stored_html is None and photo is None:
        await db.content_delete(key)
    else:
        await db.content_set(key, stored_html, photo)

    if action == "test":
        config = _cfg()
        if not config.admin_chat_id:
            return _redirect(here, err="Сохранено, но превью некуда отправить: укажите ADMIN_CHAT_ID.")
        try:
            await _bot().send_message(config.admin_chat_id, f"👁 Превью: <b>{html.escape(d.title)}</b>")
            await content.send(_bot(), config.admin_chat_id, db, key, None, config, track=False, **_sample_vars())
        except Exception as exc:
            log.exception("Preview failed")
            return _redirect(here, err=f"Сохранено, но Telegram не принял сообщение: {exc}")
        return _redirect(here, msg="Сохранено. Превью отправлено вам в Telegram.")
    return _redirect(here, msg="Сохранено. Бот уже использует новый текст.")


async def photo(request: Request) -> Response:
    if not _authed(request):
        return Response(status_code=403)
    row = await _db().content_get(request.path_params["key"])
    if not row or not row["photo"]:
        return Response(status_code=404)
    bot = _bot()
    file = await bot.get_file(row["photo"])
    data = await bot.download_file(file.file_path)
    return Response(data.read(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


routes = [
    Route("/admin", index),
    Route("/admin/login", login_page, methods=["GET"]),
    Route("/admin/login", login, methods=["POST"]),
    Route("/admin/logout", logout),
    Route("/admin/edit/{key}", edit_page, methods=["GET"]),
    Route("/admin/edit/{key}", edit_save, methods=["POST"]),
    Route("/admin/photo/{key}", photo),
]
