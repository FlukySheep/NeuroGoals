from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, ExceptionTypeFilter
from aiogram.types import CallbackQuery, ErrorEvent, Message, ReactionTypeEmoji, User

from . import keyboards as kb
from . import render
from . import texts as t
from .board import KINDS, LIST_NAMES, LISTS, create_board, list_key, tag_label
from .config import WorkConfig
from .db import WorkDatabase
from .parse import MENTION_RE, card_ref, find_due, parse
from .trello import Trello, TrelloError

router = Router()

CLEAR_DUE = {"убрать", "нет", "без", "без срока", "none", "-", "clear"}


# -- helpers ------------------------------------------------------------------


async def is_admin(bot: Bot, config: WorkConfig, user_id: int) -> bool:
    if user_id in config.admin_ids:
        return True
    for chat_id in config.chat_ids:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramBadRequest:
            continue
        if member.status in ("creator", "administrator"):
            return True
    return False


async def need_board(message: Message, db: WorkDatabase, trello: Trello) -> dict | None:
    if not trello.configured:
        await message.reply(t.NO_TRELLO)
        return None
    board = await db.board_get()
    if not board:
        await message.reply(t.NO_BOARD)
    return board


def message_link(message: Message) -> str | None:
    chat = message.chat
    if chat.username:
        return f"https://t.me/{chat.username}/{message.message_id}"
    if str(chat.id).startswith("-100"):
        return f"https://t.me/c/{str(chat.id)[4:]}/{message.message_id}"
    return None


def full_text(message: Message) -> tuple[str, list]:
    return message.text or message.caption or "", message.entities or message.caption_entities or []


def cut_text_mentions(message: Message, args: str) -> tuple[str, list[User]]:
    """Mentions of users without a @username arrive as text_mention entities."""
    text, entities = full_text(message)
    users = []
    for entity in entities:
        if entity.type == "text_mention" and entity.user:
            users.append(entity.user)
            args = args.replace(entity.extract_from(text), " ", 1)
    return args, users


async def resolve_people(db: WorkDatabase, author: User, usernames: list[str],
                         users: list[User]) -> tuple[list[dict], list[str]]:
    found, missing = [], []
    for name in usernames:
        member = await (db.member_by_tg(author.id) if name == "me" else db.member_by_username(name))
        if member:
            found.append(member)
        else:
            missing.append("вы" if name == "me" else "@" + name)
    for user in users:
        if member := await db.member_by_tg(user.id):
            found.append(member)
        else:
            missing.append(user.full_name)
    unique = list({m["trello_id"]: m for m in found}.values())
    return unique, missing


def now_in(config: WorkConfig) -> datetime:
    return datetime.now(config.tz)


async def show_card(message: Message, card: dict, board: dict, db: WorkDatabase, config: WorkConfig,
                    header: str = "") -> Message:
    sent = await message.reply(
        render.card_text(card, board, now_in(config), header),
        reply_markup=kb.card(card["shortLink"], card.get("closed", False)),
    )
    await db.msg_card_set(sent.chat.id, sent.message_id, card["shortLink"])
    return sent


async def target_card(message: Message, command: CommandObject, db: WorkDatabase,
                      trello: Trello) -> tuple[dict, str, str] | None:
    """(board, card id, rest of the arguments) from "/cmd 42 rest" or a reply to a card message."""
    board = await need_board(message, db, trello)
    if not board:
        return None
    number, rest = card_ref(command.args)
    if number is not None:
        try:
            return board, (await trello.board_card(board["id"], number))["id"], rest
        except TrelloError as exc:
            if exc.status in (400, 404):
                await message.reply(t.CARD_NOT_FOUND.format(n=number))
                return None
            raise
    reply = message.reply_to_message
    if reply and (card := await db.msg_card(message.chat.id, reply.message_id)):
        return board, card, rest
    await message.reply(t.NEED_CARD.format(cmd=command.command))
    return None


def done_fields(board: dict, key: str) -> dict:
    return {"idList": board["lists"][key], "dueComplete": key == "done"}


# -- general --------------------------------------------------------------------


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(t.START_PRIVATE if message.chat.type == "private" else t.HELP)


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(t.HELP)


@router.message(Command("chatid"))
async def chat_id(message: Message) -> None:
    await message.reply(t.CHAT_ID.format(chat_id=message.chat.id))


@router.message(Command("setup"))
async def setup(message: Message, bot: Bot, db: WorkDatabase, config: WorkConfig, trello: Trello) -> None:
    if not await is_admin(bot, config, message.from_user.id):
        await message.reply(t.ADMINS_ONLY)
        return
    if not trello.configured:
        await message.reply(t.NO_TRELLO)
        return
    if board := await db.board_get():
        await message.reply(t.SETUP_EXISTS.format(url=board["url"], name=escape(board["name"])))
        return
    board = await create_board(trello, db, config.trello_workspace)
    # Members linked before the board existed get access now.
    for member in await db.members():
        try:
            await trello.board_add_member(board["id"], member["trello_id"])
        except TrelloError:
            pass
    await message.reply(t.SETUP_DONE.format(url=board["url"], name=escape(board["name"])))


@router.message(Command("board"))
async def board_link(message: Message, db: WorkDatabase, trello: Trello) -> None:
    if board := await need_board(message, db, trello):
        await message.reply(t.BOARD.format(url=board["url"], name=escape(board["name"])))


# -- people -------------------------------------------------------------------------


@router.message(Command("link"))
async def link(message: Message, command: CommandObject, bot: Bot, db: WorkDatabase,
               config: WorkConfig, trello: Trello) -> None:
    if not trello.configured:
        await message.reply(t.NO_TRELLO)
        return
    arg = (command.args or "").strip().split()
    if not arg:
        await message.reply(t.LINK_USAGE)
        return
    arg = arg[0]
    board = await db.board_get()

    if "@" in arg[1:] and "." in arg:  # e-mail: invite only, link later by username
        if not board:
            await message.reply(t.NO_BOARD)
            return
        await trello.board_invite_email(board["id"], arg)
        await message.reply(t.INVITED.format(email=escape(arg)))
        return

    # An admin may link someone else by replying to their message.
    user = message.from_user
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot and reply.from_user.id != user.id:
        if not await is_admin(bot, config, user.id):
            await message.reply(t.ADMINS_ONLY)
            return
        user = reply.from_user

    username = arg.lstrip("@")
    try:
        member = await trello.member(username)
    except TrelloError as exc:
        if exc.status in (400, 404):
            await message.reply(t.TRELLO_USER_NOT_FOUND.format(username=escape(username)))
            return
        raise
    if board:
        await trello.board_add_member(board["id"], member["id"])
    await db.member_link(user.id, user.username, user.full_name, member["id"], member["username"])
    template = t.LINKED if board else t.LINKED_NO_BOARD
    await message.reply(template.format(
        tg=escape(user.full_name), username=escape(member["username"]), name=escape(member.get("fullName") or ""),
    ))


@router.message(Command("unlink"))
async def unlink(message: Message, db: WorkDatabase) -> None:
    await message.reply(t.UNLINKED if await db.member_unlink(message.from_user.id) else t.NOTHING_TO_UNLINK)


@router.message(Command("members"))
async def members(message: Message, db: WorkDatabase) -> None:
    rows = await db.members()
    if not rows:
        await message.reply(t.MEMBERS_EMPTY)
        return
    lines = [t.MEMBERS_TITLE] + [
        f"• {escape(m['full_name'])}" + (f" (@{escape(m['tg_username'])})" if m["tg_username"] else "")
        + f" → Trello @{escape(m['trello_username'])}" for m in rows
    ]
    await message.reply("\n".join(lines))


# -- create -----------------------------------------------------------------------------


@router.message(Command(*KINDS, ignore_case=True))
async def create(message: Message, command: CommandObject, bot: Bot, db: WorkDatabase,
                 config: WorkConfig, trello: Trello) -> None:
    board = await need_board(message, db, trello)
    if not board:
        return
    kind = KINDS[command.command.lower()]
    args, mentioned_users = cut_text_mentions(message, command.args or "")

    # Replying with /task to someone's message turns that message into the card.
    source = message.reply_to_message
    if source and source.from_user and source.from_user.id == bot.id:
        source = None
    quoted = (source.text or source.caption or "").strip() if source else ""
    if not args.strip() and quoted:
        args, quoted = quoted, ""
    now = now_in(config)
    parsed = parse(args, now, kind.default_hour)
    if not parsed.title:
        await message.reply(t.NEED_TITLE.format(cmd=kind.key))
        return

    people, missing = await resolve_people(db, message.from_user, parsed.mentions, mentioned_users)
    labels = [board["labels"][kind.key]]
    if parsed.priority:
        labels.append(board["labels"][parsed.priority])
    for tag in parsed.tags:
        labels.append(await tag_label(trello, db, board, tag))

    body = "\n".join(parsed.description)
    if quoted:
        body = (body + "\n\n" if body else "") + "\n".join("> " + line for line in quoted.splitlines())
    footer = f"Добавил(а) {message.from_user.full_name} из Telegram"
    if link := message_link(message):
        footer += f" · [сообщение]({link})"

    fields = {
        "idList": board["lists"][kind.list_key],
        "name": parsed.title,
        "desc": f"{body}\n\n---\n{footer}" if body else f"\n---\n{footer}",
        "idLabels": ",".join(labels),
    }
    if people:
        fields["idMembers"] = ",".join(m["trello_id"] for m in people)
    if parsed.due:
        fields["due"] = parsed.due.isoformat()
        if kind.reminder:
            fields["dueReminder"] = kind.reminder
    created = await trello.create_card(**fields)
    if parsed.checklist:
        await trello.add_checklist(created["id"], kind.checklist, parsed.checklist)

    card = await trello.card(created["id"])
    await show_card(message, card, board, db, config, t.CREATED)
    await db.msg_card_set(message.chat.id, message.message_id, card["shortLink"])
    if source:
        await db.msg_card_set(source.chat.id, source.message_id, card["shortLink"])
    if missing:
        await message.answer(t.NOT_LINKED.format(names=escape(", ".join(missing))))


# -- change ------------------------------------------------------------------------------


@router.message(Command("card"))
async def card_cmd(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
                   trello: Trello) -> None:
    if found := await target_card(message, command, db, trello):
        board, card_id, _ = found
        await show_card(message, await trello.card(card_id), board, db, config)


@router.message(Command("done"))
async def done(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
               trello: Trello) -> None:
    if found := await target_card(message, command, db, trello):
        board, card_id, _ = found
        card = await trello.update_card(card_id, **done_fields(board, "done"))
        await show_card(message, await trello.card(card["id"]), board, db, config, "✅ <b>Готово!</b>")


@router.message(Command("move"))
async def move(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
               trello: Trello) -> None:
    if not (found := await target_card(message, command, db, trello)):
        return
    board, card_id, rest = found
    key = list_key(rest) if rest else None
    if not key:
        await message.reply(t.MOVE_USAGE.format(lists=", ".join(escape(n) for _, n, _ in LISTS)))
        return
    await trello.update_card(card_id, **done_fields(board, key))
    await show_card(message, await trello.card(card_id), board, db, config, f"➡️ {escape(LIST_NAMES[key])}")


@router.message(Command("assign"))
async def assign(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
                 trello: Trello) -> None:
    if not (found := await target_card(message, command, db, trello)):
        return
    board, card_id, rest = found
    rest, users = cut_text_mentions(message, rest)
    names = [("me" if m.group(1).lower() in ("я", "me") else m.group(1).lower()) for m in MENTION_RE.finditer(rest)]
    if not names and not users:
        await message.reply(t.ASSIGN_USAGE)
        return
    people, missing = await resolve_people(db, message.from_user, names, users)
    for person in people:
        await trello.add_card_member(card_id, person["trello_id"])
    if people:
        await show_card(message, await trello.card(card_id), board, db, config, "👤 <b>Назначено</b>")
    if missing:
        await message.answer(t.NOT_LINKED.format(names=escape(", ".join(missing))))


@router.message(Command("due"))
async def due(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
              trello: Trello) -> None:
    if not (found := await target_card(message, command, db, trello)):
        return
    board, card_id, rest = found
    if rest.lower() in CLEAR_DUE:
        await trello.update_card(card_id, due=None)
        header = "⏰ Срок убран"
    else:
        card = await trello.card(card_id)
        when, _ = find_due(rest, now_in(config), KINDS[render.card_kind(card, board)].default_hour)
        if not when:
            await message.reply(t.DUE_USAGE)
            return
        await trello.update_card(card_id, due=when.isoformat(), dueComplete=False)
        header = "⏰ Срок изменён"
    await show_card(message, await trello.card(card_id), board, db, config, header)


@router.message(Command("edit"))
async def edit(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
               trello: Trello) -> None:
    if not (found := await target_card(message, command, db, trello)):
        return
    board, card_id, rest = found
    title = rest.strip().splitlines()[0].strip() if rest.strip() else ""
    if not title:
        await message.reply(t.EDIT_USAGE)
        return
    await trello.update_card(card_id, name=title)
    await show_card(message, await trello.card(card_id), board, db, config, "✏️ Переименовано")


async def add_comment(trello: Trello, card_id: str, author: User, text: str) -> None:
    await trello.comment(card_id, f"**{author.full_name}** (Telegram):\n{text}")


@router.message(Command("comment"))
async def comment(message: Message, command: CommandObject, db: WorkDatabase, trello: Trello) -> None:
    if not (found := await target_card(message, command, db, trello)):
        return
    _, card_id, rest = found
    if not rest:
        await message.reply(t.COMMENT_USAGE)
        return
    await add_comment(trello, card_id, message.from_user, rest)
    card = await trello.card(card_id)
    await message.reply(t.COMMENTED.format(n=card["idShort"]))


@router.message(Command("delete", "archive"))
async def delete(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
                 trello: Trello) -> None:
    if not (found := await target_card(message, command, db, trello)):
        return
    board, card_id, _ = found
    card = await trello.card(card_id)
    sent = await message.reply(
        render.card_text(card, board, now_in(config), t.DELETE_ASK), reply_markup=kb.archive(card["shortLink"]),
    )
    await db.msg_card_set(sent.chat.id, sent.message_id, card["shortLink"])


@router.message(Command("restore"))
async def restore(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
                  trello: Trello) -> None:
    if not (found := await target_card(message, command, db, trello)):
        return
    board, card_id, _ = found
    await trello.update_card(card_id, closed=False)
    await show_card(message, await trello.card(card_id), board, db, config, "♻️ Возвращено из архива")


# -- lists ------------------------------------------------------------------------------------


async def show_list(message: Message, arg: str, db: WorkDatabase, config: WorkConfig, trello: Trello) -> None:
    board = await need_board(message, db, trello)
    if not board:
        return
    arg = arg.strip()
    word = arg.lower()
    now = now_in(config)
    done_list = board["lists"]["done"]
    active = lambda c: c["idList"] != done_list  # noqa: E731

    if not word:
        title = t.LIST_TITLES[None]
        hidden = {done_list, board["lists"]["notes"]}
        keep = lambda c: c["idList"] not in hidden  # noqa: E731
    elif word in ("все", "all"):
        title, keep = t.LIST_TITLES["all"], (lambda c: True)
    elif word in ("мои", "mine", "my", "я"):
        me = await db.member_by_tg(message.from_user.id)
        if not me:
            await message.reply(t.SELF_NOT_LINKED)
            return
        title, keep = t.LIST_TITLES["mine"], (lambda c: me["trello_id"] in c["idMembers"] and active(c))
    elif word.startswith("@"):
        person = await db.member_by_username(word)
        if not person:
            await message.reply(t.NOT_LINKED.format(names=escape(arg)))
            return
        title = f"👤 Задачи {escape(person['full_name'])}"
        keep = lambda c: person["trello_id"] in c["idMembers"] and active(c)  # noqa: E731
    elif word in ("просрочено", "просроченные", "overdue"):
        title, keep = t.LIST_TITLES["overdue"], (lambda c: render.is_overdue(c, board, now))
    elif word in ("сегодня", "today"):
        today = now.date()
        title = t.LIST_TITLES["today"]
        keep = lambda c: active(c) and (d := render.parse_due(c.get("due"), config.tz)) and d.date() == today  # noqa: E731
    elif word in ("неделя", "неделю", "week"):
        horizon = now + timedelta(days=7)
        title = t.LIST_TITLES["week"]
        keep = lambda c: active(c) and (d := render.parse_due(c.get("due"), config.tz)) and d <= horizon  # noqa: E731
    elif key := list_key(word):
        title = escape(LIST_NAMES[key])
        keep = lambda c: c["idList"] == board["lists"][key]  # noqa: E731
    else:
        await message.reply(t.LIST_USAGE.format(arg=escape(arg)))
        return
    cards = [c for c in await trello.board_cards(board["id"]) if keep(c)]
    names = {m["id"]: m.get("fullName") or m.get("username") for m in await trello.board_members(board["id"])}
    await message.reply(render.cards_text(f"<b>{title}</b>", cards, board, now, names))


@router.message(Command("list"))
async def list_cmd(message: Message, command: CommandObject, db: WorkDatabase, config: WorkConfig,
                   trello: Trello) -> None:
    await show_list(message, command.args or "", db, config, trello)


@router.message(Command("mytasks", "my"))
async def my_tasks(message: Message, db: WorkDatabase, config: WorkConfig, trello: Trello) -> None:
    await show_list(message, "мои", db, config, trello)


@router.message(Command("overdue"))
async def overdue(message: Message, db: WorkDatabase, config: WorkConfig, trello: Trello) -> None:
    await show_list(message, "просрочено", db, config, trello)


# -- replies to card messages become comments ------------------------------------------------


@router.message(F.reply_to_message, F.text, ~F.text.startswith("/"))
async def reply_comment(message: Message, bot: Bot, db: WorkDatabase, trello: Trello) -> None:
    reply = message.reply_to_message
    if not (reply.from_user and reply.from_user.id == bot.id and trello.configured):
        return
    card_id = await db.msg_card(message.chat.id, reply.message_id)
    if not card_id:
        return
    await add_comment(trello, card_id, message.from_user, message.text)
    try:
        await message.react([ReactionTypeEmoji(emoji="👍")])
    except TelegramBadRequest:
        await message.reply("💬 Добавлено в комментарии Trello")


# -- buttons -------------------------------------------------------------------------------------


async def _refresh(cb: CallbackQuery, trello: Trello, board: dict, config: WorkConfig, short: str,
                   markup=None, header: str = "") -> dict:
    card = await trello.card(short)
    try:
        await cb.message.edit_text(
            render.card_text(card, board, now_in(config), header),
            reply_markup=markup or kb.card(short, card.get("closed", False)),
        )
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
    return card


async def _markup(cb: CallbackQuery, markup) -> None:
    try:
        await cb.message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


@router.callback_query(F.data.startswith("w:"))
async def on_button(cb: CallbackQuery, bot: Bot, db: WorkDatabase, config: WorkConfig, trello: Trello) -> None:
    _, action, short, *rest = cb.data.split(":")
    arg = rest[0] if rest else ""
    board = await db.board_get()
    if not board or not trello.configured or not cb.message:
        await cb.answer(t.NO_BOARD, show_alert=True)
        return
    note = ""

    if action == "bk":
        await _refresh(cb, trello, board, config, short)
    elif action == "st":
        card = await trello.card(short)
        current = {v: k for k, v in board["lists"].items()}.get(card["idList"])
        await _markup(cb, kb.status(short, current))
    elif action in ("mv", "dn"):
        key = "done" if action == "dn" else arg
        await trello.update_card(short, **done_fields(board, key))
        await _refresh(cb, trello, board, config, short)
        note = LIST_NAMES[key]
    elif action == "me":
        me = await db.member_by_tg(cb.from_user.id)
        if not me:
            await cb.answer(t.SELF_NOT_LINKED, show_alert=True)
            return
        card = await trello.card(short)
        if me["trello_id"] in card["idMembers"]:
            await trello.remove_card_member(short, me["trello_id"])
            note = "Вы сняты с карточки"
        else:
            await trello.add_card_member(short, me["trello_id"])
            note = "Карточка ваша 💪"
        await _refresh(cb, trello, board, config, short)
    elif action in ("as", "am"):
        people = await db.members()
        if not people:
            await cb.answer(t.MEMBERS_EMPTY, show_alert=True)
            return
        card = await trello.card(short)
        assigned = set(card["idMembers"])
        if action == "am":
            person = await db.member_by_tg(int(arg))
            if person and person["trello_id"] in assigned:
                await trello.remove_card_member(short, person["trello_id"])
                assigned.discard(person["trello_id"])
            elif person:
                await trello.add_card_member(short, person["trello_id"])
                assigned.add(person["trello_id"])
            await _refresh(cb, trello, board, config, short, kb.assign(short, people, assigned))
        else:
            await _markup(cb, kb.assign(short, people, assigned))
    elif action == "du":
        await _markup(cb, kb.due(short))
    elif action == "dd":
        if arg == "none":
            await trello.update_card(short, due=None)
        else:
            card = await trello.card(short)
            now = now_in(config)
            hour = KINDS[render.card_kind(card, board)].default_hour
            if arg == "week":
                current = render.parse_due(card.get("due"), config.tz)
                when = (current or find_due("сегодня", now, hour)[0]) + timedelta(days=7)
            else:
                when = find_due({"today": "сегодня", "tomorrow": "завтра", "fri": "пятница"}[arg], now, hour)[0]
            await trello.update_card(short, due=when.isoformat(), dueComplete=False)
        await _refresh(cb, trello, board, config, short)
    elif action == "ar":
        await _markup(cb, kb.archive(short))
    elif action == "ay":
        card = await trello.update_card(short, closed=True)
        await _refresh(cb, trello, board, config, short)
        note = t.ARCHIVED.format(n=card["idShort"])
    elif action == "re":
        await trello.update_card(short, closed=False)
        await _refresh(cb, trello, board, config, short)
        note = "Возвращено"
    elif action in ("rm", "ry"):
        if not await is_admin(bot, config, cb.from_user.id):
            await cb.answer(t.ADMINS_ONLY, show_alert=True)
            return
        if action == "rm":
            await _markup(cb, kb.delete_confirm(short))
        else:
            card = await trello.card(short)
            await trello.delete_card(short)
            await cb.message.edit_text(t.DELETED.format(n=card["idShort"], name=escape(card["name"])))
    await cb.answer(note)


# -- errors ----------------------------------------------------------------------------------------


@router.errors(ExceptionTypeFilter(TrelloError))
async def trello_error(event: ErrorEvent) -> None:
    text = t.TRELLO_ERROR.format(error=escape(str(event.exception)))
    update = event.update
    if update.message:
        await update.message.reply(text)
    elif update.callback_query:
        await update.callback_query.answer(text[:200], show_alert=True)
