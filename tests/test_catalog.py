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
    # Saving a cohort from the form keeps the two-line time (Moscow / Geneva) intact.
    web.post(f"/admin/courses/{catalog.NEUROFINANCE}/cohorts/weekend",
             data={"action": "save", "title": cohorts[0]["title"], "dates": cohorts[0]["dates"],
                   "time_text": t.TIME_TEXT, "group_link": cohorts[0]["group_link"], "sort_order": "0"})
    assert web.portal.call(S.db.cohort_get, "weekend")["time_text"] == t.TIME_TEXT
    assert f'<textarea name="time_text" rows="2" style="min-height:0">{t.TIME_TEXT}</textarea>' in \
        web.get(f"/admin/courses/{catalog.NEUROFINANCE}").text

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

    web.post("/admin/courses/neuro-love/cohorts", data={"title": "Весна", "dates": "1 марта", "time_text": "19:00"})
    cohort = web.portal.call(S.db.cohorts_for, "neuro-love")[0]
    assert cohort["id"].startswith("neuro-love-") and cohort["title"] == "Весна"
    web.post(f"/admin/courses/neuro-love/cohorts/{cohort['id']}",
             data={"action": "save", "title": "Весна 2027", "dates": "1 марта\n3 марта", "sort_order": "0"})
    assert web.portal.call(S.db.cohort_get, cohort["id"])["dates"] == "1 марта\n3 марта"
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
