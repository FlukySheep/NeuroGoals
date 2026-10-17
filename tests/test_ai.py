"""AI consultant (interview → personal path) and AI journey drafts. Claude is replaced by a script."""

import json

import pytest
from aiogram import methods
from test_salesbot import ADMIN_ID, STUDENT, Harness, _login, env, web  # noqa: F401

from neurofinance import ai, catalog

NF = catalog.NEUROFINANCE


class FakeClaude:
    """Stands in for ai.complete: records each request and returns scripted replies."""

    def __init__(self, replies, harness=None):
        self.replies = list(replies)
        self.calls = []
        self.harness = harness

    async def __call__(self, config, system, messages, schema, **kwargs):
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages)), "schema": schema,
                           "tg_calls": len(self.harness.tg.calls) if self.harness else None, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def kinds(self, n):
        return self.calls[n]["schema"]["properties"]["kind"]["enum"]


def question(text, *options):
    return {"kind": "question", "message": text, "options": list(options), "recommendations": [], "summary": ""}


def proposal(text, *recs, summary="Хочет перестать бояться денег"):
    return {"kind": "proposal", "message": text, "options": [], "summary": summary,
            "recommendations": [{"product_id": p, "role": r, "why": "потому что"} for p, r in recs]}


@pytest.fixture
def ha(env, monkeypatch):
    monkeypatch.setenv("AI_AGENT", "on")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    harness = Harness(env["DATABASE_URL"])
    harness.run(catalog.ensure_seeded(harness.db, harness.config))
    harness.run(harness.db.course_save("book", title="Книга о деньгах", status="open", product_type="book",
                                       stage=1, kind="open", price_eur=20, knowledge_base="Книга для начала пути."))
    yield harness
    harness.close()


def last(h, chat_id=42):
    """What the person sees last: a new message, or the "thinking…" message edited into the answer."""
    shown = (methods.SendMessage, methods.SendPhoto, methods.EditMessageText)
    return [m for m in h.tg.calls if isinstance(m, shown) and m.chat_id == chat_id][-1]


def text(m) -> str:
    return getattr(m, "text", None) or getattr(m, "caption", None) or ""


def _cb(m):
    return [b.callback_data or b.url for row in m.reply_markup.inline_keyboard for b in row]


def test_interview_adapts_and_ends_with_a_path(ha, monkeypatch):
    fake = FakeClaude([
        question("Что хотите изменить?", "Доход", "Тревогу из-за денег"),
        question("Что уже пробовали?", "Книги", "Ничего"),
        question("Как удобнее учиться?", "В группе", "Сам"),
        proposal("Начните с книги, затем курс.", ("book", "start"), (NF, "next"), ("ghost", "later")),
    ], ha)
    monkeypatch.setattr(ai, "complete", fake)

    ha.say(STUDENT, "/start")
    assert _cb(last(ha)) == ["ai:go", "ai:catalog"]
    assert "ИИ-консультант" in text(last(ha))

    ha.press(STUDENT, "ai:go")
    # Feedback comes first: "thinking…" is sent before the model is called, then becomes the question.
    placeholder = next(m for m in ha.tg.calls if isinstance(m, methods.SendMessage) and "Готовлю" in m.text)
    assert ha.tg.calls.index(placeholder) < fake.calls[0]["tg_calls"]
    assert any(isinstance(m, methods.SendChatAction) for m in ha.tg.calls)
    assert isinstance(last(ha), methods.EditMessageText)
    assert "Что хотите изменить?" in text(last(ha))
    assert _cb(last(ha)) == ["ai:o:0", "ai:o:1"]
    # The model knows the ecosystem and every product's knowledge base.
    system = fake.calls[0]["system"]
    assert "Направление экосистемы" in system and "Книга для начала пути." in system and "финансовом сценарии" in system
    # The interview runs on the fast chat model with minimal reasoning.
    assert (fake.calls[0]["model"], fake.calls[0]["effort"]) == ("gpt-5-mini", "minimal")
    assert fake.calls[0]["schema"]["properties"]["recommendations"]["items"]["properties"]["product_id"]["enum"] \
        == [NF, "book"]

    ha.press(STUDENT, "ai:o:1")  # a button answer
    assert fake.calls[1]["messages"][-1] == {"role": "user", "content": "Тревогу из-за денег"}
    ha.say(STUDENT, "Читала пару книг, не помогло")  # a free-text answer
    assert fake.calls[2]["messages"][-1]["content"] == "Читала пару книг, не помогло"
    # Fewer than 3 answers: only questions are allowed; from the 3rd answer a proposal may come.
    assert [fake.kinds(i) for i in range(3)] == [["question"]] * 3
    ha.tg.clear()
    ha.say(STUDENT, "В группе")
    assert fake.kinds(3) == ["question", "proposal"]

    final = last(ha)
    assert isinstance(final, methods.EditMessageText)
    assert "Начните с книги" in final.text
    assert _cb(final) == ["jc:book", f"jc:{NF}", "ai:manager", "ai:go"]  # unknown product dropped
    note = next(m for m in ha.tg.sent(ADMIN_ID) if "ИИ-подбор" in m.text)
    assert "Хочет перестать бояться денег" in note.text and "Книга о деньгах" in note.text

    chat = ha.run(ha.db.ai_chat_current(42))
    assert chat["status"] == "done" and chat["answers"] == 3
    assert [r["product_id"] for r in json.loads(chat["proposal"])] == ["book", NF]
    # Replayed history alternates user/assistant, ending with the assistant's proposal.
    roles = [m["role"] for m in json.loads(chat["messages"])]
    assert roles == ["user", "assistant"] * 4

    # After the proposal, free text is a question to the team again, not an interview answer.
    ha.say(STUDENT, "спасибо!")
    assert len(fake.calls) == 4
    # The recommended product opens its card.
    ha.press(STUDENT, "jc:book")
    assert "Книга о деньгах" in text(last(ha))


def test_fifth_answer_forces_the_proposal(ha, monkeypatch):
    fake = FakeClaude([question(f"Вопрос {i}", "да", "нет") for i in range(5)] + [proposal("Вот путь", (NF, "start"))])
    monkeypatch.setattr(ai, "complete", fake)
    ha.say(STUDENT, "/start")
    ha.press(STUDENT, "ai:go")
    for _ in range(5):
        ha.press(STUDENT, "ai:o:0")
    assert fake.kinds(5) == ["proposal"]
    assert "последний вопрос" in fake.calls[5]["messages"][-1]["content"]
    assert "Вот путь" in text(last(ha))


def test_ai_failure_keeps_the_answer_retryable(ha, monkeypatch):
    fake = FakeClaude([question("Что хотите изменить?", "Доход"), ai.AIError("API 529"),
                       question("Что уже пробовали?", "Ничего")])
    monkeypatch.setattr(ai, "complete", fake)
    ha.say(STUDENT, "/start")
    ha.press(STUDENT, "ai:go")
    ha.say(STUDENT, "Хочу больше зарабатывать")
    assert "Не получилось ответить" in text(last(ha))
    assert ha.run(ha.db.ai_chat_current(42))["answers"] == 0  # nothing lost
    ha.say(STUDENT, "Хочу больше зарабатывать")
    assert "Что уже пробовали?" in text(last(ha))
    assert ha.run(ha.db.ai_chat_current(42))["answers"] == 1


def test_busy_interview_and_daily_limit(ha, monkeypatch):
    monkeypatch.setattr(ai, "complete", FakeClaude([question("Q", "a")] * 10))
    ha.say(STUDENT, "/start")
    ha.press(STUDENT, "ai:go")
    chat = ha.run(ha.db.ai_chat_current(42))
    assert ha.run(ha.db.ai_chat_claim(chat["id"]))  # another call is in progress
    ha.say(STUDENT, "ответ")
    assert "ещё думаю" in text(last(ha))
    for _ in range(ai.DAILY_INTERVIEWS - 1):
        ha.press(STUDENT, "ai:go")
    ha.press(STUDENT, "ai:go")
    assert "несколько раз" in text(last(ha))


def test_switch_and_key_required(env, monkeypatch):
    monkeypatch.setenv("AI_AGENT", "on")  # no API key: classic flow
    harness = Harness(env["DATABASE_URL"])
    try:
        harness.say(STUDENT, "/start")
        assert _cb(harness.tg.sent(42)[-1]) == ["go:step2", "go:step5", "go:choose"]
    finally:
        harness.close()
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("AI_AGENT", "admins")  # only admins get the consultant
    harness = Harness(env["DATABASE_URL"])
    try:
        harness.say(STUDENT, "/start")
        assert _cb(harness.tg.sent(42)[-1]) == ["go:step2", "go:step5", "go:choose"]
        harness.say(STUDENT, f"/start c_{NF}")  # deep links still open the product directly
        assert "go:step2" in _cb(harness.tg.sent(42)[-1])
    finally:
        harness.close()


# -- real request shape ----------------------------------------------------------------------


def test_openai_request_and_refusal(monkeypatch, env):
    import asyncio

    import openai

    from neurofinance.config import load_config

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    seen = {}
    reply = {"refusal": None, "finish_reason": "stop"}

    class Message:
        def __init__(self):
            self.content = '{"kind": "question"}'
            self.refusal = reply["refusal"]

    class Choice:
        def __init__(self):
            self.message = Message()
            self.finish_reason = reply["finish_reason"]

    class Response:
        choices = None

    class Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.chat = self
            self.completions = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def create(self, **kwargs):
            seen["request"] = kwargs
            r = Response()
            r.choices = [Choice()]
            return r

    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    config = load_config()
    assert (config.ai_provider, config.ai_model, config.ai_chat_model, config.ai_key) == (
        "openai", "gpt-5", "gpt-5-mini", True)
    schema = {"type": "object"}
    result = asyncio.run(ai.complete(config, "SYS", [{"role": "user", "content": "hi"}], schema,
                                     effort="low", max_tokens=100, timeout=5))
    assert result == {"kind": "question"}
    req = seen["request"]
    assert req["model"] == "gpt-5" and req["reasoning_effort"] == "low" and req["max_completion_tokens"] == 100
    assert req["prompt_cache_key"] == "neurofinance-chat"
    asyncio.run(ai.complete(config, "S", [], schema, effort="minimal", max_tokens=10, timeout=5, model="o4-mini"))
    assert seen["request"]["model"] == "o4-mini" and seen["request"]["reasoning_effort"] == "low"
    assert req["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    assert req["response_format"]["json_schema"] == {"name": "reply", "strict": True, "schema": schema}

    monkeypatch.setenv("AI_MODEL", "gpt-4.1")  # non-reasoning models get no reasoning_effort
    asyncio.run(ai.complete(load_config(), "S", [], schema, effort="low", max_tokens=10, timeout=5))
    assert "reasoning_effort" not in seen["request"]

    reply["refusal"] = "I can't help with that"
    with pytest.raises(ai.AIError):
        asyncio.run(ai.complete(config, "S", [], schema, effort="low", max_tokens=10, timeout=5))


def test_claude_request_and_refusal(monkeypatch, env):
    import anthropic

    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    from neurofinance.config import load_config

    seen = {}

    class Block:
        type = "text"
        text = '{"kind": "question"}'

    class Final:
        stop_reason = "end_turn"
        content = [Block()]

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_final_message(self):
            return Final()

    class Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.beta = self
            self.messages = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, **kwargs):
            seen["request"] = kwargs
            return Stream()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", Client)
    import asyncio
    config = load_config()
    result = asyncio.run(ai.complete(config, "SYS", [{"role": "user", "content": "hi"}], {"type": "object"},
                                     effort="low", max_tokens=100, timeout=5))
    assert result == {"kind": "question"}
    req = seen["request"]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["output_config"]["effort"] == "low" and req["output_config"]["format"]["type"] == "json_schema"
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}

    Final.stop_reason = "refusal"
    with pytest.raises(ai.AIError):
        asyncio.run(ai.complete(config, "SYS", [], {}, effort="low", max_tokens=100, timeout=5))


# -- journey drafts ----------------------------------------------------------------------------


def test_clean_journey_fixes_links_actions_and_html():
    raw = {"start": "Intro", "steps": [
        {"key": "Intro", "title": "Начало", "text": "<b>Привет</b> <script>x</script> <i>ой",
         "buttons": [{"label": "Дальше", "action": "goto", "target": "q1"},
                     {"label": "Никуда", "action": "goto", "target": "missing"},
                     {"label": "Купить", "action": "enroll", "target": ""},
                     {"label": "Сайт", "action": "url", "target": "javascript:alert(1)"}]},
        {"key": "q1", "title": "Вопрос", "text": "?", "buttons": [{"label": "Назад", "action": "goto", "target": "Intro"}]},
    ]}
    steps, start = ai.clean_journey(raw, {"status": "waitlist"})
    assert start == "intro" and [s["key"] for s in steps] == ["intro", "q1"]
    assert steps[0]["html"] == "<b>Привет</b> &lt;script&gt;x&lt;/script&gt; ой"
    assert json.loads(steps[0]["buttons"]) == [
        {"label": "Дальше", "action": "goto", "target": "q1"},
        {"label": "Купить", "action": "waitlist", "target": ""},  # matches the product status
    ]
    assert json.loads(steps[1]["buttons"])[0]["target"] == "intro"


def test_admin_knowledge_base_ecosystem_and_ai_journey(web, monkeypatch):
    monkeypatch.setenv("CATALOG_ENABLED", "on")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    _login(web)
    from neurofinance.web import S
    page = web.get("/admin/courses").text
    assert "Продукты" in page and "Групповой онлайн-курс" in page

    r = web.post("/admin/courses/new", data={"id": "game", "title": "Игра «Деньги»", "product_type": "game"},
                 follow_redirects=False)
    assert "msg=" in r.headers["location"]
    game = web.portal.call(S.db.course_get, "game")
    assert game["product_type"] == "game" and game["kind"] == "open"
    assert "нет базы знаний" in web.get("/admin/courses").text
    assert "Сначала заполните" in web.get("/admin/courses/game/journey").text

    form = {"title": "Игра «Деньги»", "status": "waitlist", "kind": "open", "price_eur": "40", "price_rub": "0",
            "summary": "<p>Игра</p>", "product_type": "game", "stage": "1",
            "knowledge_base": "Настольная онлайн-игра про деньги.", "url": "https://example.com/game"}
    assert "msg=" in web.post("/admin/courses/game", data=form, follow_redirects=False).headers["location"]
    game = web.portal.call(S.db.course_get, "game")
    assert (game["stage"], game["knowledge_base"], game["url"]) == (1, "Настольная онлайн-игра про деньги.",
                                                                   "https://example.com/game")
    bad = dict(form, url="http://insecure")
    assert "err=" in web.post("/admin/courses/game", data=bad, follow_redirects=False).headers["location"]

    # Ecosystem page: direction, greeting, product map by stage.
    page = web.get("/admin/ai").text
    assert "Направление экосистемы" in page and "1 · Знакомство" in page and "Игра «Деньги»" in page
    web.post("/admin/ai", data={"ecosystem": "Наша миссия", "greeting": "Привет!"})
    assert web.portal.call(S.db.kv_get, catalog.ECOSYSTEM_KEY) == "Наша миссия"

    # AI journey draft from the knowledge base.
    fake = FakeClaude([{"start": "hello", "steps": [
        {"key": "hello", "title": "Привет", "text": "Это игра", "buttons": [
            {"label": "Записаться", "action": "enroll", "target": ""},
            {"label": "Дальше", "action": "goto", "target": "more"}]},
        {"key": "more", "title": "Подробнее", "text": "Правила", "buttons": []},
    ]}])
    monkeypatch.setattr(ai, "complete", fake)
    r = web.post("/admin/courses/game/journey/ai", data={"wishes": "покороче"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert "Наша миссия" in fake.calls[0]["system"] and "НейроФинансы" in fake.calls[0]["system"]
    assert "Настольная онлайн-игра" in fake.calls[0]["messages"][0]["content"]
    assert "покороче" in fake.calls[0]["messages"][0]["content"]
    steps = web.portal.call(S.db.steps_for, "game")
    assert [s["key"] for s in steps] == ["hello", "more"]
    assert json.loads(steps[0]["buttons"])[0]["action"] == "waitlist"
    assert web.portal.call(S.db.course_get, "game")["start_step"] == "hello"
    # Existing steps are only replaced on purpose.
    r = web.post("/admin/courses/game/journey/ai", data={}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_interview_shows_in_admin(web, monkeypatch):
    monkeypatch.setenv("AI_AGENT", "admins")
    _login(web)
    from neurofinance.web import S
    web.get("/admin/ai")  # seed
    chat_id = web.portal.call(S.db.ai_chat_new, 42)
    web.portal.call(S.db.upsert_user, 42, "anna", "Анна")
    messages = [{"role": "user", "content": ai.FIRST_TURN},
                {"role": "assistant", "content": json.dumps(question("Что важно?"), ensure_ascii=False)},
                {"role": "user", "content": "Спокойствие"}]
    web.portal.call(lambda: S.db.ai_chat_save(chat_id, messages=json.dumps(messages, ensure_ascii=False),
                                              answers=1, summary="Ищет спокойствия"))
    page = web.get("/admin/ai").text
    assert "Анна" in page and "Ищет спокойствия" in page and "Спокойствие" in page and "Что важно?" in page
    assert ai.FIRST_TURN not in page
