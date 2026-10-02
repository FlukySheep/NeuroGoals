import time

import aiosqlite

# status: new -> chose_group -> pending (receipt sent) -> paid
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    full_name       TEXT,
    created_at      REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new',
    group_key       TEXT,
    currency        TEXT,
    method          TEXT,
    group_chosen_at REAL,
    reminder_sent   INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS relay (
    admin_msg_id INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _exec(self, sql: str, params: tuple = ()) -> None:
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def upsert_user(self, user_id: int, username: str | None, full_name: str) -> None:
        await self._exec(
            "INSERT INTO users (user_id, username, full_name, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
            "full_name=excluded.full_name, blocked=0",
            (user_id, username, full_name, time.time()),
        )

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone()

    async def set_group(self, user_id: int, group_key: str) -> None:
        # Paid users may re-open the flow; never downgrade their status.
        await self._exec(
            "UPDATE users SET group_key=?, "
            "status=CASE WHEN status IN ('paid', 'pending') THEN status ELSE 'chose_group' END, "
            "group_chosen_at=COALESCE(group_chosen_at, ?) WHERE user_id=?",
            (group_key, time.time(), user_id),
        )

    async def set_currency(self, user_id: int, currency: str) -> None:
        await self._exec("UPDATE users SET currency=? WHERE user_id=?", (currency, user_id))

    async def set_method(self, user_id: int, method: str) -> None:
        await self._exec("UPDATE users SET method=? WHERE user_id=?", (method, user_id))

    async def set_status(self, user_id: int, status: str) -> None:
        await self._exec("UPDATE users SET status=? WHERE user_id=?", (status, user_id))

    async def mark_blocked(self, user_id: int) -> None:
        await self._exec("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))

    async def due_reminders(self, delay_hours: float) -> list[int]:
        threshold = time.time() - delay_hours * 3600
        async with self.conn.execute(
            "SELECT user_id FROM users WHERE status='chose_group' AND reminder_sent=0 "
            "AND blocked=0 AND group_chosen_at IS NOT NULL AND group_chosen_at<=?",
            (threshold,),
        ) as cur:
            return [row["user_id"] for row in await cur.fetchall()]

    async def mark_reminded(self, user_id: int) -> None:
        await self._exec("UPDATE users SET reminder_sent=1 WHERE user_id=?", (user_id,))

    async def unpaid_users(self) -> list[int]:
        async with self.conn.execute(
            "SELECT user_id FROM users WHERE status NOT IN ('paid', 'pending') AND blocked=0"
        ) as cur:
            return [row["user_id"] for row in await cur.fetchall()]

    async def stats(self) -> dict[str, int]:
        async with self.conn.execute(
            "SELECT status, group_key, COUNT(*) AS n FROM users GROUP BY status, group_key"
        ) as cur:
            rows = await cur.fetchall()
        return {f"{r['status']}:{r['group_key'] or '-'}": r["n"] for r in rows}

    async def save_relay(self, admin_msg_id: int, user_id: int) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO relay (admin_msg_id, user_id) VALUES (?, ?)",
            (admin_msg_id, user_id),
        )

    async def relay_user(self, admin_msg_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT user_id FROM relay WHERE admin_msg_id=?", (admin_msg_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["user_id"] if row else None
