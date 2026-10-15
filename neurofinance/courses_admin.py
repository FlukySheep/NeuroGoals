"""Admin panel: course catalog (/admin/courses). Only reachable when CATALOG_ENABLED is not off."""

import html
import secrets
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from . import catalog, content
from . import texts as t
from .panel import MAX_PHOTO_BYTES, _authed, _bot, _cfg, _db, _flash, _page, _redirect, _upload_photo, nav

FORM_CSS = """
.form{padding:16px;display:grid;gap:14px}
.form label{display:grid;gap:6px;font-size:14px;color:var(--muted)}
.form input[type=text],.form input[type=number],.form select,.form textarea{width:100%;padding:10px 12px;border-radius:9px;
 border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:15px}
.form textarea{min-height:96px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.checks{display:flex;flex-wrap:wrap;gap:8px 16px;color:var(--ink)}
.checks label{display:flex;align-items:center;gap:6px;color:var(--ink);font-size:15px}
.course{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:14px 16px;
 border-top:1px solid var(--line);color:var(--ink);text-decoration:none}
.course:first-child{border-top:0}.course:hover{background:var(--chip)}
.course .meta{font-size:13px;color:var(--muted)}
.cohort{border-top:1px solid var(--line)}.cohort:first-child{border-top:0}
#summary{min-height:120px;background:var(--card);color:var(--ink)}
.ql-toolbar.ql-snow,.ql-container.ql-snow{border-color:var(--line)!important}
.ql-snow .ql-stroke{stroke:var(--ink)}.ql-snow .ql-fill{fill:var(--ink)}
"""
QUILL_HEAD = """<link href="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"></script>"""


def _allowed(request: Request) -> Response | None:
    """Login required, and the catalog must be switched on for this deployment."""
    if not _authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    if _cfg().catalog == "off":
        return _redirect("/admin", err="Каталог курсов выключен (CATALOG_ENABLED=off).")
    return None


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _int(value, default: int = 0) -> int:
    try:
        return max(0, int(str(value).strip() or default))
    except ValueError:
        return default


def _options(choices: dict[str, str], current: str) -> str:
    return "".join(
        f'<option value="{k}"{" selected" if k == current else ""}>{_esc(v)}</option>' for k, v in choices.items()
    )


async def courses_page(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    await catalog.ensure_seeded(db, _cfg())
    rows = []
    for c in await db.courses_all():
        cohorts = await db.cohorts_for(c["id"])
        badge = "on" if c["status"] == "open" else ""
        info = [catalog.price_line(c), catalog.KINDS.get(c["kind"], c["kind"])]
        if c["kind"] == "cohort":
            info.append(f"групп: {len(cohorts)}")
        rows.append(
            f'<a class="course" href="/admin/courses/{_esc(c["id"])}"><span><b>{_esc(c["title"])}</b>'
            f'<div class="meta">{_esc(" · ".join(info))}</div></span>'
            f'<span class="badge {badge}">{_esc(catalog.STATUSES.get(c["status"], c["status"]))}</span></a>'
        )
    body = f"""<header><h1>Курсы</h1>{nav("courses")}</header>{_flash(request)}
<p class="note">Каталог курсов в боте. Черновики видны только здесь.</p>
<div class="card">{"".join(rows) or '<p class="note" style="padding:16px">Курсов пока нет.</p>'}</div>
<h2>Новый курс</h2>
<form method="post" action="/admin/courses/new" class="card form">
<div class="grid2">
<label>Название<input type="text" name="title" required placeholder="Например: НейроОтношения"></label>
<label>Адрес (латиница, для ссылки t.me/…?start=)<input type="text" name="id" required pattern="[a-z0-9][a-z0-9_-]{{1,39}}" placeholder="neuro-relations"></label>
</div>
<div><button class="btn primary">Создать черновик</button></div></form>"""
    return _page("Курсы", body, f"<style>{FORM_CSS}</style>")


async def course_create(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    form = await request.form()
    course_id = str(form.get("id", "")).strip().lower()
    title = str(form.get("title", "")).strip()
    if not catalog.SLUG_RE.match(course_id):
        return _redirect("/admin/courses", err="Адрес: 2–40 символов, латиница, цифры, «-» и «_».")
    if not title:
        return _redirect("/admin/courses", err="Укажите название.")
    db = _db()
    if await db.course_get(course_id):
        return _redirect("/admin/courses", err="Курс с таким адресом уже есть.")
    await db.course_save(course_id, title=title, status="draft", kind="cohort",
                         methods=",".join(t.PAYMENT_METHODS), sort_order=len(await db.courses_all()))
    return _redirect(f"/admin/courses/{course_id}", msg="Черновик создан. Заполните описание, цены и группы.")


async def course_page(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    c = await db.course_get(request.path_params["course_id"])
    if c is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    cid = _esc(c["id"])
    enabled = set(catalog.methods_of(c))
    methods = "".join(
        f'<label><input type="checkbox" name="methods" value="{k}"{" checked" if k in enabled else ""}> '
        f'{_esc(m.label)} <span class="note" style="margin:0">({"/".join(x.upper() for x in m.currencies)})</span></label>'
        for k, m in t.PAYMENT_METHODS.items()
    )
    photo = (
        f'<img src="/admin/courses/{cid}/photo?v={quote(c["photo"][-12:])}" alt="" '
        f'style="max-width:200px;border-radius:10px;border:1px solid var(--line)">'
        f'<label class="checks"><label><input type="checkbox" name="remove_photo" value="1"> Убрать картинку</label></label>'
        if c["photo"] else '<span class="note">Картинки нет.</span>'
    )
    cohorts = ""
    for g in await db.cohorts_for(c["id"]):
        gid = _esc(g["id"])
        cohorts += f"""<form method="post" action="/admin/courses/{cid}/cohorts/{gid}" class="form cohort">
<div class="grid2"><label>Название группы<input type="text" name="title" value="{_esc(g["title"])}" required></label>
<label>Время (можно в две строки)<textarea name="time_text" rows="2" style="min-height:0">{_esc(g["time_text"])}</textarea></label></div>
<label>Даты (каждый день с новой строки)<textarea name="dates">{_esc(g["dates"])}</textarea></label>
<div class="grid2"><label>Ссылка на закрытую группу<input type="text" name="group_link" value="{_esc(g["group_link"])}" placeholder="https://t.me/+…"></label>
<label>Порядок<input type="number" name="sort_order" value="{g["sort_order"]}" min="0"></label></div>
<div class="row"><button class="btn" name="action" value="save">Сохранить группу</button>
<button class="btn danger" name="action" value="delete" onclick="return confirm('Удалить группу?')">Удалить</button>
<span class="note" style="margin:0">id: {gid}</span></div></form>"""
    cohort_block = f"""<h2>Группы (потоки)</h2>
<div class="card">{cohorts or '<p class="note" style="padding:16px">Групп пока нет.</p>'}</div>
<form method="post" action="/admin/courses/{cid}/cohorts" class="card form" style="margin-top:12px">
<b>Добавить группу</b>
<div class="grid2"><label>Название<input type="text" name="title" required placeholder="Группа выходного дня"></label>
<label>Время<textarea name="time_text" rows="2" style="min-height:0" placeholder="12:00–15:00 по Москве"></textarea></label></div>
<label>Даты<textarea name="dates" placeholder="10 октября (сб) — День 1"></textarea></label>
<label>Ссылка на закрытую группу<input type="text" name="group_link" placeholder="https://t.me/+…"></label>
<div><button class="btn primary">Добавить группу</button></div></form>"""

    body = f"""<header><h1>{_esc(c["title"])}</h1><a class="btn" href="/admin/courses">← Курсы</a></header>
{_flash(request)}
<form method="post" enctype="multipart/form-data" class="card form" id="course">
<div class="grid2">
<label>Название<input type="text" name="title" value="{_esc(c["title"])}" required></label>
<label>Статус<select name="status">{_options(catalog.STATUSES, c["status"])}</select></label>
<label>Формат<select name="kind">{_options(catalog.KINDS, c["kind"])}</select></label>
<label>Порядок в каталоге<input type="number" name="sort_order" value="{c["sort_order"]}" min="0"></label>
</div>
<label>Короткое описание (в каталоге)<div id="summary">{content.telegram_to_editor(c["summary"] or "")}</div></label>
<input type="hidden" name="summary" id="summary_html">
<div class="grid2">
<label>Цена, € (0 — не принимать евро)<input type="number" name="price_eur" value="{c["price_eur"]}" min="0"></label>
<label>Цена, ₽ (0 — не принимать рубли)<input type="number" name="price_rub" value="{c["price_rub"]}" min="0"></label>
</div>
<label>Способы оплаты</label><div class="checks">{methods}</div>
<label>Ссылка на группу участников (для курсов без дат)<input type="text" name="group_link" value="{_esc(c["group_link"])}" placeholder="https://t.me/+…"></label>
<label>Обложка курса</label><div class="row" style="align-items:flex-start">{photo}
<input type="file" name="photo" accept="image/jpeg,image/png,image/webp"></div>
<p class="note" style="margin:0">Ссылка на курс в боте: <code>t.me/&lt;бот&gt;?start=c_{cid}</code></p>
<div class="row"><button class="btn primary">Сохранить курс</button></div>
</form>
{cohort_block if c["kind"] == "cohort" else ""}
<h2>Удаление</h2>
<form method="post" action="/admin/courses/{cid}/delete" class="card form">
<p class="note" style="margin:0">Курс и его группы будут удалены. Лучше сначала поставить статус «Набор закрыт».</p>
<div><button class="btn danger" onclick="return confirm('Удалить курс «{_esc(c["title"])}» насовсем?')">Удалить курс</button></div>
</form>
<script>
const q = new Quill('#summary', {{theme: 'snow', modules: {{toolbar: [['bold','italic','underline','link'],['clean']]}}}});
document.getElementById('course').addEventListener('submit', () => {{
  document.getElementById('summary_html').value = q.root.innerHTML; }});
</script>"""
    return _page(c["title"], body, QUILL_HEAD + f"<style>{FORM_CSS}</style>")


async def course_save(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    course_id = request.path_params["course_id"]
    c = await db.course_get(course_id)
    if c is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    form = await request.form()
    here = f"/admin/courses/{course_id}"
    title = str(form.get("title", "")).strip()
    if not title:
        return _redirect(here, err="Название не может быть пустым.")
    status = str(form.get("status", "draft"))
    kind = str(form.get("kind", "cohort"))
    methods = [m for m in form.getlist("methods") if m in t.PAYMENT_METHODS]
    price_eur, price_rub = _int(form.get("price_eur")), _int(form.get("price_rub"))
    if status in ("open", "waitlist") and not (price_eur or price_rub):
        return _redirect(here, err="Для открытого курса укажите хотя бы одну цену.")
    if status == "open" and not methods:
        return _redirect(here, err="Для открытого курса выберите хотя бы один способ оплаты.")
    summary = content.editor_to_telegram(str(form.get("summary", "")))
    if content.visible_length(summary) > 900:
        return _redirect(here, err="Короткое описание длиннее 900 символов.")

    photo = c["photo"]
    if form.get("remove_photo"):
        photo = None
    upload = form.get("photo")
    if upload is not None and getattr(upload, "filename", ""):
        data = await upload.read()
        if len(data) > MAX_PHOTO_BYTES:
            return _redirect(here, err="Картинка больше 4 МБ.")
        if data:
            try:
                photo = await _upload_photo(data, upload.filename)
            except Exception as exc:
                return _redirect(here, err=f"Не удалось загрузить картинку: {exc}")

    await db.course_save(
        course_id, title=title,
        status=status if status in catalog.STATUSES else "draft",
        kind=kind if kind in catalog.KINDS else "cohort",
        summary=summary, photo=photo, price_eur=price_eur, price_rub=price_rub,
        methods=",".join(methods), group_link=str(form.get("group_link", "")).strip(),
        sort_order=_int(form.get("sort_order")),
    )
    return _redirect(here, msg="Курс сохранён.")


async def cohort_add(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    course_id = request.path_params["course_id"]
    db = _db()
    if await db.course_get(course_id) is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    form = await request.form()
    title = str(form.get("title", "")).strip()
    if not title:
        return _redirect(f"/admin/courses/{course_id}", err="Укажите название группы.")
    await db.cohort_save(
        f"{course_id}-{secrets.token_hex(3)}", course_id=course_id, title=title,
        dates=str(form.get("dates", "")).strip(), time_text=str(form.get("time_text", "")).strip(),
        group_link=str(form.get("group_link", "")).strip(), sort_order=len(await db.cohorts_for(course_id)),
    )
    return _redirect(f"/admin/courses/{course_id}", msg="Группа добавлена.")


async def cohort_save(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    course_id, cohort_id = request.path_params["course_id"], request.path_params["cohort_id"]
    db = _db()
    g = await db.cohort_get(cohort_id)
    here = f"/admin/courses/{course_id}"
    if g is None or g["course_id"] != course_id:
        return _redirect(here, err="Группа не найдена.")
    form = await request.form()
    if form.get("action") == "delete":
        await db.cohort_delete(cohort_id)
        return _redirect(here, msg="Группа удалена.")
    title = str(form.get("title", "")).strip()
    if not title:
        return _redirect(here, err="Название группы не может быть пустым.")
    await db.cohort_save(
        cohort_id, title=title, dates=str(form.get("dates", "")).strip(),
        time_text=str(form.get("time_text", "")).strip(), group_link=str(form.get("group_link", "")).strip(),
        sort_order=_int(form.get("sort_order")),
    )
    return _redirect(here, msg="Группа сохранена.")


async def course_delete(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    await _db().course_delete(request.path_params["course_id"])
    return _redirect("/admin/courses", msg="Курс удалён.")


async def course_photo(request: Request) -> Response:
    if not _authed(request):
        return Response(status_code=403)
    c = await _db().course_get(request.path_params["course_id"])
    if not c or not c["photo"]:
        return Response(status_code=404)
    bot = _bot()
    file = await bot.get_file(c["photo"])
    data = await bot.download_file(file.file_path)
    return Response(data.read(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


routes = [
    Route("/admin/courses", courses_page, methods=["GET"]),
    Route("/admin/courses/new", course_create, methods=["POST"]),
    Route("/admin/courses/{course_id}", course_page, methods=["GET"]),
    Route("/admin/courses/{course_id}", course_save, methods=["POST"]),
    Route("/admin/courses/{course_id}/cohorts", cohort_add, methods=["POST"]),
    Route("/admin/courses/{course_id}/cohorts/{cohort_id}", cohort_save, methods=["POST"]),
    Route("/admin/courses/{course_id}/delete", course_delete, methods=["POST"]),
    Route("/admin/courses/{course_id}/photo", course_photo, methods=["GET"]),
]
