from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from . import texts as t


def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Rows of (label, callback_data); callback_data starting with http is a URL button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, url=data)
                if data.startswith("http")
                else InlineKeyboardButton(text=label, callback_data=data)
                for label, data in row
            ]
            for row in rows
        ]
    )


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in t.MENU_LAYOUT],
        resize_keyboard=True,
        is_persistent=True,
    )


step1 = _kb(
    [("Понять, подойдёт ли мне", "go:step2")],
    [("Посмотреть программу", "go:step5")],
    [("Выбрать группу", "go:choose")],
)

step2 = _kb(*[[(label, f"br:{key}")] for key, (label, _) in t.BRANCHES.items()])

branch = _kb([("А что такое финансовый сценарий?", "go:step3")])
step3 = _kb([("Что с этим можно сделать?", "go:step4")])
step4 = _kb([("Посмотреть программу четырёх дней", "go:step5")])

step5 = _kb(
    [("Это мне подходит", "go:step6")],
    [("Узнать о формате", "go:step7")],
    [("Пока сомневаюсь", "go:doubt")],
)

doubt = _kb(
    [("Посмотреть формат", "go:step7")],
    [("Частые вопросы", "go:faq")],
    [("Задать вопрос", "go:ask")],
)

step6 = _kb([("Формат, даты и стоимость", "go:step7")])

choose_group = _kb(
    [("Выбираю выходные", "grp:weekend")],
    [("Выбираю будни", "grp:weekday")],
    [("Задать вопрос", "go:ask")],
)

step8 = _kb(
    [("Оплатить 230 €", "cur:eur")],
    [("Оплатить 23 000 ₽", "cur:rub")],
    [("Вернуться к расписанию", "go:choose")],
)

step9 = _kb([("ПЕРЕЙТИ К ОПЛАТЕ", "go:methods")], [("← Назад", "go:step8")])


def payment_methods(currency: str) -> InlineKeyboardMarkup:
    rows = [
        [(m.label, f"pay:{m.key}")] for m in t.PAYMENT_METHODS.values() if currency in m.currencies
    ]
    rows.append([("Связаться с менеджером", "manager")])
    rows.append([("← Сменить валюту", "go:step8")])
    return _kb(*rows)


def payment_method(method: t.PaymentMethod) -> InlineKeyboardMarkup:
    rows = []
    if method.url:
        rows.append([(method.url_label or method.label, method.url)])
    rows.append([("✅ Я оплатил(а)", "paid")])
    rows.append([("← Другие способы оплаты", "go:methods")])
    rows.append([("Связаться с менеджером", "manager")])
    return _kb(*rows)


cancel_input = _kb([("Отмена", "cancel")])


def manager_link(url: str) -> InlineKeyboardMarkup:
    return _kb([("Написать менеджеру", url)])


def step10(link: str | None) -> InlineKeyboardMarkup | None:
    return _kb([("ПЕРЕЙТИ В ГРУППУ", link)]) if link else None


def faq_list() -> InlineKeyboardMarkup:
    return _kb(*[[(q, f"faq:{i}")] for i, (q, a) in enumerate(t.FAQ) if a])


faq_answer = _kb(
    [("← Все вопросы", "go:faq")],
    [("Записаться", "go:choose"), ("Задать вопрос", "go:ask")],
)

price = _kb([("Записаться", "go:choose")], [("Формат и даты", "go:step7")])

reminder = _kb(
    [("Выбрать группу", "go:choose")],
    [("Задать вопрос", "go:ask")],
    [("Вернуться к программе", "go:step5")],
)

closing = _kb([("ВЫБРАТЬ ГРУППУ", "go:choose")], [("ЗАДАТЬ ВОПРОС", "go:ask")])

closing_confirm = _kb([("Отправить всем", "adm:closing:yes"), ("Отмена", "adm:closing:no")])


def admin_payment(user_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [("✅ Подтвердить оплату", f"adm:ok:{user_id}")],
        [("❌ Платёж не найден", f"adm:no:{user_id}")],
    )
