"""Course catalog: admin pages behind the CATALOG_ENABLED switch."""

from test_salesbot import _login, env, web  # noqa: F401 - shared fixtures

from neurofinance import catalog
from neurofinance import texts as t


def test_catalog_hidden_when_switched_off(web):
    _login(web)
    r = web.get("/admin/courses", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin?err=")
    assert "/admin/courses" not in web.get("/admin").text  # no "Курсы" tab on live


def test_neurofinance_is_seeded_once(web, monkeypatch):
    monkeypatch.setenv("CATALOG_ENABLED", "on")
    _login(web)
    page = web.get("/admin/courses").text
    assert "НейроФинансы" in page and "Открыта продажа" in page and "групп: 2" in page
    assert "/admin/courses" in web.get("/admin").text  # tab visible

    from neurofinance.web import S
    course = web.portal.call(S.db.course_get, catalog.NEUROFINANCE)
    assert (course["price_eur"], course["price_rub"]) == (t.PRICE_EUR, t.PRICE_RUB)
    assert catalog.methods_of(course) == list(t.PAYMENT_METHODS)
    cohorts = web.portal.call(S.db.cohorts_for, catalog.NEUROFINANCE)
    assert [g["id"] for g in cohorts] == ["weekend", "weekday"]  # match users.group_key
    assert cohorts[0]["group_link"] == "https://t.me/+weekend"
    # Seeded with real sessions; the generated text matches what the live bot shows.
    assert cohorts[0]["dates"] == t.GROUPS["weekend"].dates and cohorts[1]["dates"] == t.GROUPS["weekday"].dates
    assert cohorts[0]["time_text"] == "12:00–16:00 по Москве\n11:00–15:00 по Женеве\n3–4 часа с перерывом"
    page = web.get(f"/admin/courses/{catalog.NEUROFINANCE}").text
    assert page.count('type="date"') >= 8 and 'value="2026-10-10"' in page and 'type="time"' in page

    web.post(f"/admin/courses/{catalog.NEUROFINANCE}/delete")
    assert "НейроФинансы" not in web.get("/admin/courses").text  # deletion is respected


def test_course_lifecycle(web, monkeypatch):
    monkeypatch.setenv("CATALOG_ENABLED", "on")
    _login(web)
    bad = web.post("/admin/courses/new", data={"id": "Плохой адрес", "title": "X"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]
    r = web.post("/admin/courses/new", data={"id": "neuro-love", "title": "НейроОтношения"}, follow_redirects=False)
    assert r.headers["location"].startswith("/admin/courses/neuro-love")
    dup = web.post("/admin/courses/new", data={"id": "neuro-love", "title": "Again"}, follow_redirects=False)
    assert "err=" in dup.headers["location"]

    edit = web.get("/admin/courses/neuro-love").text
    assert "Черновик" in edit and "Сохранить курс" in edit

    # An open course needs a price and a payment method.
    no_price = web.post("/admin/courses/neuro-love", data={"title": "НейроОтношения", "status": "open",
                                                           "kind": "cohort", "methods": ["paypal"]},
                        follow_redirects=False)
    assert "err=" in no_price.headers["location"]
    ok = web.post("/admin/courses/neuro-love", data={
        "title": "НейроОтношения", "status": "waitlist", "kind": "cohort", "price_eur": "180",
        "price_rub": "0", "methods": ["paypal", "twint", "nonsense"], "sort_order": "2",
        "summary": "<p><strong>Про</strong> отношения<script>x</script></p>",
    }, files={"photo": ("c.jpg", b"\xff\xd8x", "image/jpeg")}, follow_redirects=False)
    assert "msg=" in ok.headers["location"]

    from neurofinance.web import S
    course = web.portal.call(S.db.course_get, "neuro-love")
    assert course["status"] == "waitlist" and course["price_eur"] == 180 and course["price_rub"] == 0
    assert catalog.methods_of(course) == ["paypal", "twint"]
    assert course["summary"] == "<b>Про</b> отношения" and course["photo"] == "PHOTO_ID"
    assert catalog.price_line(course) == "180 €"
    assert web.get("/admin/courses/neuro-love/photo").status_code == 200

    # Cohorts are built from date/time pickers; wrong input is rejected with a clear message.
    def cohort(dates, starts, ends, **extra):
        return {"title": "Весна", "session_date": dates, "session_start": starts, "session_end": ends,
                "timezone": "Europe/Moscow", "extra_timezone": "", **extra}

    for bad in (cohort(["2027-03-01"], ["19:00"], ["18:00"]), cohort(["2027-03-01"], [""], ["18:00"]),
                cohort([""], ["19:00"], ["21:00"])):
        r = web.post("/admin/courses/neuro-love/cohorts", data=bad, follow_redirects=False)
        assert "err=" in r.headers["location"]
    assert web.portal.call(S.db.cohorts_for, "neuro-love") == []

    web.post("/admin/courses/neuro-love/cohorts",
             data=cohort(["2027-03-08", "2027-03-01", ""], ["19:00", "19:00", ""], ["21:00", "21:00", ""]))
    cohort_row = web.portal.call(S.db.cohorts_for, "neuro-love")[0]
    assert cohort_row["id"].startswith("neuro-love-") and cohort_row["title"] == "Весна"
    assert cohort_row["dates"] == "1 марта (пн) — День 1\n8 марта (пн) — День 2"  # sorted, empty row ignored
    assert cohort_row["time_text"] == "19:00–21:00 по Москве"

    web.post(f"/admin/courses/neuro-love/cohorts/{cohort_row['id']}", data={
        "action": "save", **cohort(["2027-03-01"], ["19:00"], ["21:00"], extra_timezone="Europe/Zurich",
                                   time_note="с перерывом", title="Весна 2027")})
    saved = web.portal.call(S.db.cohort_get, cohort_row["id"])
    assert saved["title"] == "Весна 2027" and saved["extra_timezone"] == "Europe/Zurich"
    assert saved["time_text"] == "19:00–21:00 по Москве\n17:00–19:00 по Женеве\nс перерывом"
    cohort = cohort_row
    # A cohort can't be edited through another course's URL.
    wrong = web.post(f"/admin/courses/{catalog.NEUROFINANCE}/cohorts/{cohort['id']}",
                     data={"action": "delete"}, follow_redirects=False)
    assert "err=" in wrong.headers["location"] and web.portal.call(S.db.cohort_get, cohort["id"])
    web.post(f"/admin/courses/neuro-love/cohorts/{cohort['id']}", data={"action": "delete"})
    assert web.portal.call(S.db.cohorts_for, "neuro-love") == []

    web.post("/admin/courses/neuro-love/delete")
    assert web.portal.call(S.db.course_get, "neuro-love") is None


def test_courses_require_login(web, monkeypatch):
    monkeypatch.setenv("CATALOG_ENABLED", "on")
    assert web.get("/admin/courses", follow_redirects=False).headers["location"] == "/admin/login"
    assert web.post("/admin/courses/new", data={"id": "x1", "title": "x"}, follow_redirects=False) \
        .headers["location"] == "/admin/login"
    assert web.get("/admin/courses/neurofinance/photo").status_code == 403


def test_seeded_catalog_gets_structured_schedule_once(web, monkeypatch):
    """Staging was seeded before schedules existed: the old text cohorts get real sessions."""
    monkeypatch.setenv("CATALOG_ENABLED", "on")
    _login(web)
    web.get("/admin/courses")  # seeds
    from neurofinance.web import S

    async def make_legacy():
        await S.db.cohort_save("weekend", sessions=None, dates="old text", time_text="old time")
        await S.db.kv_set(catalog.SCHEDULE_FLAG, "")
    web.portal.call(make_legacy)
    web.get(f"/admin/courses/{catalog.NEUROFINANCE}")
    upgraded = web.portal.call(S.db.cohort_get, "weekend")
    assert upgraded["dates"] == t.GROUPS["weekend"].dates and upgraded["sessions"]
