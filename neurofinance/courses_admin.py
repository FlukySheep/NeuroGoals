"""Admin panel: course catalog (/admin/courses). Only reachable when CATALOG_ENABLED is not off."""

import html
import secrets
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from . import catalog, content, schedule
from . import texts as t
from .panel import (
    MAX_PHOTO_BYTES, _authed, _bot, _cfg, _db, _flash, _page, _redirect, _upload_photo, file_picker, nav,
)

PAGE_CSS = """
.course{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:14px 16px;
 border-top:1px solid var(--line);color:var(--ink);text-decoration:none}
.course:first-child{border-top:0}.course:hover{background:var(--chip)}
.course .meta{font-size:13px;color:var(--muted);margin-top:2px}
.status{font-size:13px;color:var(--muted);white-space:nowrap}
#summary{min-height:120px;background:var(--card);color:var(--ink)}
.ql-toolbar.ql-snow,.ql-container.ql-snow{border-color:var(--line)!important}
.ql-toolbar.ql-snow{border-radius:10px 10px 0 0}.ql-container.ql-snow{border-radius:0 0 10px 10px;font-size:15px}
.ql-snow .ql-stroke{stroke:var(--ink)}.ql-snow .ql-fill{fill:var(--ink)}
.sessions{display:grid;gap:8px}
.session{display:grid;grid-template-columns:28px minmax(150px,1.4fr) minmax(104px,1fr) minmax(104px,1fr) 44px;gap:8px;align-items:center}
.session .n{color:var(--muted);font-size:14px;text-align:center}
.session-head{display:grid;grid-template-columns:28px minmax(150px,1.4fr) minmax(104px,1fr) minmax(104px,1fr) 44px;gap:8px;
 font-size:13px;color:var(--muted)}
@media (max-width:560px){
 .session,.session-head{grid-template-columns:1fr 1fr 44px}
 .session .n,.session-head .n{display:none}
 .session .d,.session-head .d{grid-column:1/-1}
 .session-head .d{display:none}
 .session{padding-bottom:10px;border-bottom:1px dashed var(--line)}
 .session:last-child{border-bottom:0;padding-bottom:0}
}
.preview{white-space:pre-line;background:var(--chip);border-radius:10px;padding:12px 14px;font-size:15px;color:var(--ink)}
.cohort-title{display:flex;justify-content:space-between;align-items:center;gap:8px}
details.add>summary{list-style:none;cursor:pointer}
details.add>summary::-webkit-details-marker{display:none}
"""
QUILL_HEAD = """<link href="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"></script>"""

SESSIONS_JS = """<script>
function sessionRow(d, s, e) {
  const row = document.createElement('div'); row.className = 'session';
  row.innerHTML = `<span class="n"></span>
    <input class="input d" type="date" name="session_date" value="${d}" required aria-label="Дата">
    <input class="input" type="time" name="session_start" value="${s}" required aria-label="Начало">
    <input class="input" type="time" name="session_end" value="${e}" required aria-label="Конец">
    <button type="button" class="icon-btn" title="Удалить встречу" aria-label="Удалить встречу">✕</button>`;
  return row;
}
function renumber(list) {
  list.querySelectorAll('.session').forEach((r, i) => r.querySelector('.n').textContent = i + 1);
}
document.addEventListener('click', e => {
  const add = e.target.closest('[data-add-session]');
  if (add) {
    const list = add.closest('form').querySelector('.sessions');
    const rows = list.querySelectorAll('.session');
    let d = '', s = '12:00', en = '15:00';
    if (rows.length) {  // copy the previous meeting one week later
      const last = rows[rows.length - 1];
      const ld = last.querySelector('[name=session_date]').value;
      s = last.querySelector('[name=session_start]').value || s;
      en = last.querySelector('[name=session_end]').value || en;
      if (ld) { const x = new Date(ld + 'T12:00:00'); x.setDate(x.getDate() + 7); d = x.toISOString().slice(0, 10); }
    }
    const row = sessionRow(d, s, en); list.append(row); renumber(list);
    row.querySelector('input').focus();
    add.closest('form').dispatchEvent(new Event('dirty'));
  }
  const del = e.target.closest('.session .icon-btn');
  if (del) {
    const list = del.closest('.sessions'), form = del.closest('form');
    if (list.querySelectorAll('.session').length > 1) { del.closest('.session').remove(); renumber(list); }
    else { del.closest('.session').querySelectorAll('input').forEach(i => i.value = ''); }
    form.dispatchEvent(new Event('dirty'));
  }
});
// An end time before the start time is caught before the form is sent.
document.addEventListener('submit', e => {
  for (const row of e.target.querySelectorAll('.session')) {
    const s = row.querySelector('[name=session_start]'), en = row.querySelector('[name=session_end]');
    en.setCustomValidity(s.value && en.value && en.value <= s.value ? 'Конец должен быть позже начала' : '');
    if (!en.checkValidity()) { en.reportValidity(); e.preventDefault(); return; }
  }
}, true);
</script>"""


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
        f'<option value="{_esc(k)}"{" selected" if k == current else ""}>{_esc(v)}</option>' for k, v in choices.items()
    )


def _tz_options(current: str, allow_none: bool = False) -> str:
    choices = {"": "— не показывать —"} if allow_none else {}
    choices.update({tz: label for tz, label, _ in schedule.TIMEZONES})
    return _options(choices, current)


# ---------------------------------------------------------------------------
# Course list
# ---------------------------------------------------------------------------


async def courses_page(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    await catalog.ensure_seeded(db, _cfg())
    rows = []
    for c in await db.courses_all():
        cohorts = await db.cohorts_for(c["id"])
        info = [catalog.price_line(c), catalog.KINDS.get(c["kind"], c["kind"])]
        if c["kind"] == "cohort":
            info.append(f"групп: {len(cohorts)}")
        rows.append(
            f'<a class="course" href="/admin/courses/{_esc(c["id"])}"><span><b>{_esc(c["title"])}</b>'
            f'<div class="meta">{_esc(" · ".join(info))}</div></span>'
            f'<span class="status"><span class="dot {_esc(c["status"])}"></span>'
            f'{_esc(catalog.STATUSES.get(c["status"], c["status"]))}</span></a>'
        )
    body = f"""<header><h1>Курсы</h1>{nav("courses")}</header>{_flash(request)}
<p class="note">Каталог курсов в боте. Черновики видны только здесь.</p>
<div class="card">{"".join(rows) or '<p class="note" style="padding:16px">Курсов пока нет.</p>'}</div>
<h2>Новый курс</h2>
<form method="post" action="/admin/courses/new" class="card section">
<div class="grid2">
<label class="field"><span>Название</span><input class="input" type="text" name="title" required placeholder="Например: НейроОтношения"></label>
<label class="field"><span>Адрес курса</span><input class="input" type="text" name="id" required
  pattern="[a-z0-9][a-z0-9_\\-]{{1,39}}" placeholder="neuro-relations" autocapitalize="off" spellcheck="false">
  <span class="hint">Латиница, цифры и «-». Будет в ссылке t.me/бот?start=c_адрес</span></label>
</div>
<div><button class="btn primary">Создать черновик</button></div></form>"""
    return _page("Курсы", body, f"<style>{PAGE_CSS}</style>")


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


# ---------------------------------------------------------------------------
# Course page
# ---------------------------------------------------------------------------


def _session_rows(sessions: list[schedule.Session]) -> str:
    rows = sessions or [None]
    out = []
    for i, s in enumerate(rows, start=1):
        d, st, en = (s.day.isoformat(), s.start.strftime("%H:%M"), s.end.strftime("%H:%M")) if s else ("", "12:00", "15:00")
        out.append(f"""<div class="session"><span class="n">{i}</span>
<input class="input d" type="date" name="session_date" value="{d}" required aria-label="Дата">
<input class="input" type="time" name="session_start" value="{st}" required aria-label="Начало">
<input class="input" type="time" name="session_end" value="{en}" required aria-label="Конец">
<button type="button" class="icon-btn" title="Удалить встречу" aria-label="Удалить встречу">✕</button></div>""")
    return "".join(out)


def _cohort_form(cid: str, g: dict | None) -> str:
    """Form for an existing cohort (g) or a new one (g is None)."""
    action = f"/admin/courses/{cid}/cohorts/{_esc(g['id'])}" if g else f"/admin/courses/{cid}/cohorts"
    sessions = schedule.load_sessions(g["sessions"]) if g else []
    legacy = ""
    if g and not sessions and (g.get("dates") or g.get("time_text")):
        legacy = (f'<p class="note" style="margin:0">Сейчас указано текстом — задайте встречи заново:</p>'
                  f'<div class="preview">{_esc(g.get("dates"))}\n{_esc(g.get("time_text"))}</div>')
    preview = ""
    if g and sessions:
        preview = (f'<div class="field"><span>Так увидят студенты</span>'
                   f'<div class="preview">{_esc(g["dates"])}\n\n{_esc(g["time_text"])}</div></div>')
    buttons = ('<button class="btn primary" name="action" value="save">Сохранить группу</button>'
               '<button class="btn danger" name="action" value="delete" formnovalidate '
               'onclick="return confirm(\'Удалить группу?\')">Удалить</button>') if g \
        else '<button class="btn primary">Добавить группу</button>'
    title = _esc(g["title"]) if g else ""
    return f"""<form method="post" action="{action}" class="card section" data-guard>
<div class="grid2">
<label class="field"><span>Название группы</span><input class="input" type="text" name="title" value="{title}" required
  placeholder="Группа выходного дня"></label>
<label class="field"><span>Ссылка на закрытую группу</span><input class="input" type="url" name="group_link"
  value="{_esc(g["group_link"]) if g else ""}" placeholder="https://t.me/+…"></label>
</div>
<div class="field"><span>Встречи</span>
<div class="session-head"><span class="n">№</span><span class="d">Дата</span><span>Начало</span><span>Конец</span><span></span></div>
<div class="sessions">{_session_rows(sessions)}</div>
<div><button type="button" class="btn" data-add-session>＋ Добавить встречу</button>
<p class="note" style="margin:6px 0 0">Новая встреча появится через неделю после предыдущей — дату можно поменять.</p></div>
</div>
<div class="grid2">
<label class="field"><span>Часовой пояс встреч</span><select class="input" name="timezone">
  {_tz_options((g or {}).get("timezone") or schedule.DEFAULT_TZ)}</select></label>
<label class="field"><span>Показывать время также</span><select class="input" name="extra_timezone">
  {_tz_options((g or {}).get("extra_timezone") if g else schedule.DEFAULT_EXTRA_TZ, allow_none=True)}</select></label>
</div>
<label class="field"><span>Комментарий ко времени <span class="hint">(необязательно)</span></span>
<input class="input" type="text" name="time_note" value="{_esc((g or {}).get("time_note"))}" placeholder="3–4 часа с перерывом"></label>
{legacy}{preview}
<div class="row">{buttons}</div></form>"""


async def course_page(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    db = _db()
    await catalog.upgrade_schedules(db)
    c = await db.course_get(request.path_params["course_id"])
    if c is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    cid = _esc(c["id"])
    enabled = set(catalog.methods_of(c))
    methods = "".join(
        f'<label class="toggle"><input type="checkbox" name="methods" value="{k}"{" checked" if k in enabled else ""}>'
        f'{_esc(m.label)} <small>{"/".join(x.upper() for x in m.currencies)}</small></label>'
        for k, m in t.PAYMENT_METHODS.items()
    )
    photo_url = f"/admin/courses/{cid}/photo?v={quote(c['photo'][-12:])}" if c["photo"] else None
    remove = ('<label class="toggle"><input type="checkbox" name="remove_photo" value="1">Убрать обложку</label>'
              if c["photo"] else "")
    cohorts = "".join(_cohort_form(cid, g) for g in await db.cohorts_for(c["id"]))
    n_steps = len(await db.steps_for(c["id"]))
    journey_block = f"""<a class="card section" href="/admin/courses/{cid}/journey" style="text-decoration:none;color:var(--ink)">
<h3>Путь в боте →</h3><p>{f"Шагов: {n_steps}. Схема, тексты и кнопки, которые видит студент." if n_steps else
"Пока нет — бот покажет карточку курса. Создайте путь или скопируйте путь «НейроФинансов»."}</p></a>"""
    cohort_block = f"""<h2>Группы (потоки)</h2>
{cohorts or '<p class="note">Групп пока нет — добавьте первую.</p>'}
<details class="add"{"" if cohorts else " open"}><summary class="btn">＋ Новая группа</summary>
<div style="margin-top:12px">{_cohort_form(cid, None)}</div></details>"""

    body = f"""<header><h1>{_esc(c["title"])}</h1><a class="btn" href="/admin/courses">← Курсы</a></header>
{_flash(request)}
{journey_block}
<form method="post" enctype="multipart/form-data" id="course" data-guard>
<div class="card section"><h3>Основное</h3>
<div class="grid2">
<label class="field"><span>Название</span><input class="input" type="text" name="title" value="{_esc(c["title"])}" required></label>
<label class="field"><span>Статус</span><select class="input" name="status">{_options(catalog.STATUSES, c["status"])}</select></label>
<label class="field"><span>Формат</span><select class="input" name="kind">{_options(catalog.KINDS, c["kind"])}</select></label>
<label class="field"><span>Порядок в каталоге</span><input class="input" type="number" name="sort_order" value="{c["sort_order"]}" min="0" inputmode="numeric"></label>
</div>
<div class="field"><span>Короткое описание <span class="hint">— видно в каталоге, до 900 символов</span></span>
<div id="summary">{content.telegram_to_editor(c["summary"] or "")}</div></div>
<input type="hidden" name="summary" id="summary_html">
</div>
<div class="card section"><h3>Цена и оплата</h3><p>0 — эту валюту не принимать.</p>
<div class="grid2">
<label class="field"><span>Цена в евро</span><span class="affix"><input class="input" type="number" name="price_eur" value="{c["price_eur"]}" min="0" inputmode="numeric"><b>€</b></span></label>
<label class="field"><span>Цена в рублях</span><span class="affix"><input class="input" type="number" name="price_rub" value="{c["price_rub"]}" min="0" step="100" inputmode="numeric"><b>₽</b></span></label>
</div>
<div class="field"><span>Способы оплаты</span><div class="toggles">{methods}</div></div>
</div>
<div class="card section"><h3>Обложка и доступ</h3>
<div class="row" style="align-items:flex-start">{file_picker("photo", "Выбрать обложку", photo_url)}{remove}</div>
<label class="field"><span>Ссылка на группу участников <span class="hint">— для курсов без дат</span></span>
<input class="input" type="url" name="group_link" value="{_esc(c["group_link"])}" placeholder="https://t.me/+…"></label>
<p class="note" style="margin:0">Ссылка на курс в боте: <code>t.me/&lt;бот&gt;?start=c_{cid}</code></p>
</div>
<div class="savebar"><span class="dirty">● Есть несохранённые изменения</span>
<button class="btn primary">Сохранить курс</button></div>
</form>
{cohort_block if c["kind"] == "cohort" else ""}
<h2>Удаление</h2>
<form method="post" action="/admin/courses/{cid}/delete" class="card section">
<p class="note" style="margin:0">Курс и его группы будут удалены насовсем. Обычно достаточно статуса «Набор закрыт».</p>
<div><button class="btn danger" onclick="return confirm('Удалить курс «{_esc(c["title"])}» насовсем?')">Удалить курс</button></div>
</form>
<script>
const q = new Quill('#summary', {{theme: 'snow', modules: {{toolbar: [['bold','italic','underline','link'],['clean']]}}}});
q.on('text-change', (d, o, source) => {{ if (source === 'user') document.getElementById('course').dispatchEvent(new Event('dirty')); }});
document.getElementById('course').addEventListener('submit', () => {{
  document.getElementById('summary_html').value = q.root.innerHTML; }});
</script>{SESSIONS_JS}"""
    return _page(c["title"], body, QUILL_HEAD + f"<style>{PAGE_CSS}</style>")


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


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------


def _cohort_fields(form) -> dict:
    """Validated cohort fields from the form (raises ScheduleError)."""
    title = str(form.get("title", "")).strip()
    if not title:
        raise schedule.ScheduleError("Укажите название группы.")
    sessions = schedule.parse_sessions(
        form.getlist("session_date"), form.getlist("session_start"), form.getlist("session_end"))
    if not sessions:
        raise schedule.ScheduleError("Добавьте хотя бы одну встречу с датой и временем.")
    zones = {tz for tz, _, _ in schedule.TIMEZONES}
    tz = str(form.get("timezone", "")) if form.get("timezone") in zones else schedule.DEFAULT_TZ
    extra = str(form.get("extra_timezone", ""))
    extra = extra if extra in zones and extra != tz else ""
    return {"title": title, "group_link": str(form.get("group_link", "")).strip(),
            **catalog.schedule_fields(sessions, tz, extra, str(form.get("time_note", "")).strip()[:200])}


async def cohort_add(request: Request) -> Response:
    if (denied := _allowed(request)) is not None:
        return denied
    course_id = request.path_params["course_id"]
    db = _db()
    here = f"/admin/courses/{course_id}"
    if await db.course_get(course_id) is None:
        return _redirect("/admin/courses", err="Курс не найден.")
    try:
        fields = _cohort_fields(await request.form())
    except schedule.ScheduleError as exc:
        return _redirect(here, err=str(exc))
    await db.cohort_save(f"{course_id}-{secrets.token_hex(3)}", course_id=course_id,
                         sort_order=len(await db.cohorts_for(course_id)), **fields)
    return _redirect(here, msg="Группа добавлена.")


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
    try:
        fields = _cohort_fields(form)
    except schedule.ScheduleError as exc:
        return _redirect(here, err=f"{g['title']}: {exc}")
    await db.cohort_save(cohort_id, **fields)
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
