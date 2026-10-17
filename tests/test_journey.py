"""Journey designer (admin) and journeys in the bot (catalog switched on)."""

import json

import pytest
from aiogram import methods
from test_salesbot import ADMIN_ID, STUDENT, ZOE, Harness, _login, env, web  # noqa: F401

from neurofinance import catalog, journey
from neurofinance import texts as t

NF = catalog.NEUROFINANCE


@pytest.fixture
def hc(env, monkeypatch):
    """Bot harness with the catalog switched on for everyone."""
    monkeypatch.setenv("CATALOG_ENABLED", "on")
    harness = Harness(env["DATABASE_URL"])
    yield harness
    harness.close()


def _callbacks(m) -> list[str]:
    return [b.callback_data or b.url for row in m.reply_markup.inline_keyboard for b in row]


# -- bot side -------------------------------------------------------------------------


def test_course_without_journey_shows_card(hc):
    hc.say(STUDENT, "/start")
    card = hc.tg.sent(42)[-1]
    assert "НейроФинансы" in hc.tg.texts(42)[-1] and "230 €" in hc.tg.texts(42)[-1]
    assert f"ja:{NF}:enroll" in _callbacks(card)


def test_template_journey_runs_in_bot(hc):
    hc.run(catalog.ensure_seeded(hc.db, hc.config))
    n = hc.run(journey.apply_template(hc.db, NF))
    assert n == len(journey.TEMPLATE)
    hc.say(STUDENT, "/start")
    first = hc.tg.sent(42)[-1]
    assert "НЕЙРОФИНАНСЫ" in hc.tg.texts(42)[-1]
    assert _callbacks(first) == [f"j:{NF}:step2", f"j:{NF}:step5", f"ja:{NF}:enroll"]

    hc.press(STUDENT, f"j:{NF}:step2")
    assert [b for b in _callbacks(hc.tg.sent(42)[-1])] == [f"j:{NF}:br_{k}" for k in t.BRANCHES]
    hc.press(STUDENT, f"j:{NF}:br_unstable")
    assert "Можно объективно иметь деньги" in hc.tg.texts(42)[-1]

    # "Choose a group" on НейроФинансы enters the existing payment flow.
    hc.press(STUDENT, f"ja:{NF}:enroll")
    assert "Выберите группу" in hc.tg.texts(42)[-1]
    hc.press(STUDENT, "grp:weekend")
    assert "Вы выбрали" in hc.tg.texts(42)[-1]


def test_catalog_drafts_deeplinks_and_waitlist(hc):
    hc.run(catalog.ensure_seeded(hc.db, hc.config))
    hc.run(hc.db.course_save("love", title="НейроОтношения", status="waitlist", price_eur=180))
    hc.run(hc.db.course_save("secret", title="Секретный курс", status="draft"))

    hc.say(STUDENT, "/start")
    menu = hc.tg.sent(42)[-1]
    assert _callbacks(menu) == [f"jc:{NF}", "jc:love"]  # drafts are hidden from students
    hc.tg.clear()
    hc.say(ZOE, "/start", chat_id=ADMIN_ID)
    assert "jc:secret" in _callbacks(hc.tg.sent(ADMIN_ID)[-1])  # …but admins see them

    hc.say(STUDENT, "/start c_love")
    card = hc.tg.sent(42)[-1]
    assert "НейроОтношения" in hc.tg.texts(42)[-1] and "ja:love:waitlist" in _callbacks(card)
    hc.say(STUDENT, "/start c_secret")  # deep link to a draft → catalog instead
    assert "jc:secret" not in _callbacks(hc.tg.sent(42)[-1])

    hc.tg.clear()
    hc.press(STUDENT, "ja:love:waitlist")
    hc.press(STUDENT, "ja:love:waitlist")
    assert [w["user_id"] for w in hc.run(hc.db.waitlist_for("love"))] == [42]
    assert sum("Лист ожидания" in (m.text or "") for m in hc.tg.sent(ADMIN_ID)) == 1  # notified once
    assert "листе ожидания" in hc.tg.texts(42)[-1]

    hc.press(STUDENT, "jc:secret")  # tapping a draft as a student is refused
    assert any(isinstance(m, methods.AnswerCallbackQuery) and m.show_alert for m in hc.tg.calls)


def test_catalog_off_keeps_classic_flow(env):
    harness = Harness(env["DATABASE_URL"])
    try:
        harness.run(catalog.ensure_seeded(harness.db, harness.config))
        harness.run(journey.apply_template(harness.db, NF))
        harness.say(STUDENT, "/start")
        assert _callbacks(harness.tg.sent(42)[-1]) == ["go:step2", "go:step5", "go:choose"]
    finally:
        harness.close()


# -- admin designer ---------------------------------------------------------------------


def test_designer_template_edit_and_guards(web, monkeypatch):
    monkeypatch.setenv("CATALOG_ENABLED", "on")
    _login(web)
    web.get("/admin/courses")  # seed
    from neurofinance.web import S
    page = web.get(f"/admin/courses/{NF}/journey").text
    assert "Скопировать путь" in page

    web.post(f"/admin/courses/{NF}/journey/template")
    page = web.get(f"/admin/courses/{NF}/journey").text
    assert 'class="mermaid"' in page and "step1 --&gt;|" in page and "act_enroll" in page
    assert page.count('class="step"') == len(journey.TEMPLATE)
    again = web.post(f"/admin/courses/{NF}/journey/template", follow_redirects=False)
    assert "err=" in again.headers["location"]  # never overwrites existing steps

    editor = web.get(f"/admin/courses/{NF}/journey/step3").text
    assert 'name="b_label"' in editor and "Как это выглядит" in editor

    def save(key, labels, actions, targets, urls, **extra):
        return web.post(f"/admin/courses/{NF}/journey/{key}", data={
            "title": "Шаг", "html": "<p>Текст</p>", "action": "save", "b_label": labels, "b_action": actions,
            "b_target": targets, "b_url": urls, **extra}, follow_redirects=False)

    assert "err=" in save("step3", ["Дальше"], ["goto"], ["nope"], [""]).headers["location"]
    assert "err=" in save("step3", ["Сайт"], ["url"], [""], ["javascript:alert(1)"]).headers["location"]
    assert "err=" in save("step3", ["x"] * 9, ["faq"] * 9, [""] * 9, [""] * 9).headers["location"]
    r = save("step3", ["Дальше", "", "Сайт", "Вопрос"], ["goto", "goto", "url", "ask"],
             ["step4", "", "", "step1"], ["", "", "https://example.com", ""])
    assert "msg=" in r.headers["location"]
    step = web.portal.call(S.db.step_get, NF, "step3")
    assert json.loads(step["buttons"]) == [
        {"label": "Дальше", "action": "goto", "target": "step4"},
        {"label": "Сайт", "action": "url", "target": "https://example.com"},
        {"label": "Вопрос", "action": "ask", "target": ""},
    ]

    # Deleting: not the start step, not a step other buttons lead to.
    assert "err=" in web.post(f"/admin/courses/{NF}/journey/step1", data={"action": "delete"},
                              follow_redirects=False).headers["location"]
    assert "err=" in web.post(f"/admin/courses/{NF}/journey/step4", data={"action": "delete"},
                              follow_redirects=False).headers["location"]

    # A new step starts unreachable until a button points to it; then it can be deleted when unused.
    r = web.post(f"/admin/courses/{NF}/journey/new", data={"title": "Отзывы"}, follow_redirects=False)
    key = r.headers["location"].split("/journey/")[1].split("?")[0]
    assert "недоступен" in web.get(f"/admin/courses/{NF}/journey").text
    assert "err=" in save(key, [], [], [], [], html="<p><br></p>").headers["location"]  # empty step refused
    web.post(f"/admin/courses/{NF}/journey/{key}", data={"action": "delete"})
    assert web.portal.call(S.db.step_get, NF, key) is None

    # Preview goes to the manager's chat with working buttons.
    r = save("step4", ["Программа"], ["goto"], ["step5"], [""], action="test")
    assert "msg=" in r.headers["location"]
    preview = [m for m in web.tg.calls if isinstance(m, methods.SendMessage) and m.chat_id == ADMIN_ID][-1]
    assert preview.reply_markup.inline_keyboard[0][0].callback_data == f"j:{NF}:step5"

    web.post(f"/admin/courses/{NF}/journey/start", data={"start": "step5"})
    assert web.portal.call(S.db.course_get, NF)["start_step"] == "step5"


def test_designer_hidden_when_catalog_off(web):
    _login(web)
    r = web.get(f"/admin/courses/{NF}/journey", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin?err=")
