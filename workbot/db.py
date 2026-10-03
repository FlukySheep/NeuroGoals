"""Work bot tables, on top of the shared Postgres/SQLite layer of the sales bot."""

import json

from neurofinance.db import SCHEMA, Database

WORK_SCHEMA = [
    # Telegram user <-> Trello member
    """CREATE TABLE IF NOT EXISTS work_members (
        tg_user_id      BIGINT PRIMARY KEY,
        tg_username     TEXT,
        full_name       TEXT,
        trello_id       TEXT NOT NULL,
        trello_username TEXT
    )""",
    # Telegram message (bot's card preview or the source message) -> Trello card
    """CREATE TABLE IF NOT EXISTS work_msgs (
        chat_id BIGINT NOT NULL,
        msg_id  BIGINT NOT NULL,
        card    TEXT NOT NULL,
        PRIMARY KEY (chat_id, msg_id)
    )""",
]

BOARD_KEY = "work:board"


class WorkDatabase(Database):
    schema = SCHEMA + WORK_SCHEMA

    # -- board settings (ids of the board, its lists and labels) -------------

    async def board_get(self) -> dict | None:
        raw = await self.kv_get(BOARD_KEY)
        return json.loads(raw) if raw else None

    async def board_set(self, board: dict) -> None:
        await self.kv_set(BOARD_KEY, json.dumps(board, ensure_ascii=False))

    # -- members ----------------------------------------------------------------

    async def member_link(self, tg_user_id: int, tg_username: str | None, full_name: str,
                          trello_id: str, trello_username: str) -> None:
        await self.execute(
            "INSERT INTO work_members (tg_user_id, tg_username, full_name, trello_id, trello_username) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tg_user_id) DO UPDATE SET tg_username=excluded.tg_username, "
            "full_name=excluded.full_name, trello_id=excluded.trello_id, trello_username=excluded.trello_username",
            tg_user_id, (tg_username or "").lower() or None, full_name, trello_id, trello_username,
        )

    async def member_unlink(self, tg_user_id: int) -> bool:
        return bool(await self.execute("DELETE FROM work_members WHERE tg_user_id=?", tg_user_id))

    async def member_by_tg(self, tg_user_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM work_members WHERE tg_user_id=?", tg_user_id)

    async def member_by_username(self, username: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM work_members WHERE tg_username=?", username.lstrip("@").lower()
        )

    async def members(self) -> list[dict]:
        return await self.fetch("SELECT * FROM work_members ORDER BY full_name")

    async def touch_member(self, tg_user_id: int, tg_username: str | None, full_name: str) -> None:
        """Keeps the Telegram username current, so @mentions keep resolving after a rename."""
        await self.execute(
            "UPDATE work_members SET tg_username=?, full_name=? WHERE tg_user_id=?",
            (tg_username or "").lower() or None, full_name, tg_user_id,
        )

    # -- message -> card ----------------------------------------------------------

    async def msg_card_set(self, chat_id: int, msg_id: int, card: str) -> None:
        await self.execute(
            "INSERT INTO work_msgs (chat_id, msg_id, card) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, msg_id) DO UPDATE SET card=excluded.card",
            chat_id, msg_id, card,
        )

    async def msg_card(self, chat_id: int, msg_id: int) -> str | None:
        row = await self.fetchone("SELECT card FROM work_msgs WHERE chat_id=? AND msg_id=?", chat_id, msg_id)
        return row["card"] if row else None
