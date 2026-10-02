"""Storage: Postgres (production / Vercel) or SQLite (local polling).

The connection is (re)created per event loop, so the same object works in a
long-running polling process and in serverless invocations.
"""

import asyncio
import json
import re
import time
from typing import Any

# status: new -> chose_group -> pending (receipt sent) -> paid
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        user_id         BIGINT PRIMARY KEY,
        username        TEXT,
        full_name       TEXT,
        created_at      DOUBLE PRECISION NOT NULL,
        status          TEXT NOT NULL DEFAULT 'new',
        group_key       TEXT,
        currency        TEXT,
        method          TEXT,
        group_chosen_at DOUBLE PRECISION,
        reminder_sent   INTEGER NOT NULL DEFAULT 0,
        closing_sent    INTEGER NOT NULL DEFAULT 0,
        blocked         INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS relay (
        admin_msg_id BIGINT PRIMARY KEY,
        user_id      BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS kv (
        k TEXT PRIMARY KEY,
        v TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS fsm (
        k     TEXT PRIMARY KEY,
        state TEXT,
        data  TEXT
    )""",
]


def _to_pg(sql: str) -> str:
    counter = iter(range(1, 1000))
    return re.sub(r"\?", lambda _: f"${next(counter)}", sql)


class Database:
    def __init__(self, url: str):
        self.url = url
        self.is_pg = url.startswith(("postgres://", "postgresql://"))
        self._handle: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- low level ----------------------------------------------------------

    async def _get(self) -> Any:
        loop = asyncio.get_running_loop()
        if self._handle is not None and self._loop is loop:
            return self._handle
        if self.is_pg:
            import asyncpg

            if self._handle is not None:
                # Pool from a previous event loop: drop its connections (best effort).
                try:
                    self._handle.terminate()
                except Exception:
                    pass
            # statement_cache_size=0 keeps it compatible with pgbouncer/Neon pooled URLs.
            handle = await asyncpg.create_pool(
                self.url, min_size=0, max_size=5, statement_cache_size=0,
                max_inactive_connection_lifetime=60,
            )
            for stmt in SCHEMA:
                await handle.execute(stmt)
        else:
            import aiosqlite

            handle = await aiosqlite.connect(self.url.removeprefix("sqlite:///"))
            handle.row_factory = aiosqlite.Row
            for stmt in SCHEMA:
                await handle.execute(stmt)
            await handle.commit()
        self._handle, self._loop = handle, loop
        return handle

    async def execute(self, sql: str, *args: Any) -> int:
        """Run a statement, return the number of affected rows."""
        handle = await self._get()
        if self.is_pg:
            status = await handle.execute(_to_pg(sql), *args)
            tail = status.rsplit(" ", 1)[-1]
            return int(tail) if tail.isdigit() else 0
        cur = await handle.execute(sql, args)
        await handle.commit()
        return cur.rowcount

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        handle = await self._get()
        if self.is_pg:
            return [dict(r) for r in await handle.fetch(_to_pg(sql), *args)]
        async with handle.execute(sql, args) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def fetchone(self, sql: str, *args: Any) -> dict | None:
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def connect(self) -> None:
        await self._get()

    async def close(self) -> None:
        if self._handle is not None:
            await self._handle.close()
            self._handle = self._loop = None

    # -- users --------------------------------------------------------------

    async def upsert_user(self, user_id: int, username: str | None, full_name: str) -> None:
        await self.execute(
            "INSERT INTO users (user_id, username, full_name, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
            "full_name=excluded.full_name, blocked=0",
            user_id, username, full_name, time.time(),
        )

    async def get_user(self, user_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM users WHERE user_id=?", user_id)

    async def set_group(self, user_id: int, group_key: str) -> None:
        # Paid users may re-open the flow; never downgrade their status.
        await self.execute(
            "UPDATE users SET group_key=?, "
            "status=CASE WHEN status IN ('paid', 'pending') THEN status ELSE 'chose_group' END, "
            "group_chosen_at=COALESCE(group_chosen_at, ?) WHERE user_id=?",
            group_key, time.time(), user_id,
        )

    async def set_currency(self, user_id: int, currency: str) -> None:
        await self.execute("UPDATE users SET currency=? WHERE user_id=?", currency, user_id)

    async def set_method(self, user_id: int, method: str) -> None:
        await self.execute("UPDATE users SET method=? WHERE user_id=?", method, user_id)

    async def set_status(self, user_id: int, status: str) -> None:
        await self.execute("UPDATE users SET status=? WHERE user_id=?", status, user_id)

    async def mark_blocked(self, user_id: int) -> None:
        await self.execute("UPDATE users SET blocked=1 WHERE user_id=?", user_id)

    async def claim_reminders(self, delay_hours: float) -> list[int]:
        threshold = time.time() - delay_hours * 3600
        rows = await self.fetch(
            "SELECT user_id FROM users WHERE status='chose_group' AND reminder_sent=0 "
            "AND blocked=0 AND group_chosen_at IS NOT NULL AND group_chosen_at<=?",
            threshold,
        )
        # Claim each row atomically so overlapping cron runs never double-send.
        claimed = []
        for row in rows:
            if await self.execute(
                "UPDATE users SET reminder_sent=1 WHERE user_id=? AND reminder_sent=0", row["user_id"]
            ):
                claimed.append(row["user_id"])
        return claimed

    async def closing_recipients(self) -> list[int]:
        rows = await self.fetch(
            "SELECT user_id FROM users WHERE status NOT IN ('paid', 'pending') "
            "AND blocked=0 AND closing_sent=0"
        )
        return [r["user_id"] for r in rows]

    async def claim_closing(self, user_id: int) -> bool:
        return bool(await self.execute(
            "UPDATE users SET closing_sent=1 WHERE user_id=? AND closing_sent=0", user_id
        ))

    async def stats(self) -> list[dict]:
        return await self.fetch(
            "SELECT status, group_key, COUNT(*) AS n FROM users GROUP BY status, group_key"
        )

    # -- relay (admin chat message -> user) ---------------------------------

    async def save_relay(self, admin_msg_id: int, user_id: int) -> None:
        await self.execute(
            "INSERT INTO relay (admin_msg_id, user_id) VALUES (?, ?) "
            "ON CONFLICT(admin_msg_id) DO UPDATE SET user_id=excluded.user_id",
            admin_msg_id, user_id,
        )

    async def relay_user(self, admin_msg_id: int) -> int | None:
        row = await self.fetchone("SELECT user_id FROM relay WHERE admin_msg_id=?", admin_msg_id)
        return row["user_id"] if row else None

    # -- key/value (media file_ids etc.) ------------------------------------

    async def kv_get(self, key: str) -> str | None:
        row = await self.fetchone("SELECT v FROM kv WHERE k=?", key)
        return row["v"] if row else None

    async def kv_set(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", key, value
        )

    # -- FSM state ----------------------------------------------------------

    async def fsm_get(self, key: str) -> tuple[str | None, dict]:
        row = await self.fetchone("SELECT state, data FROM fsm WHERE k=?", key)
        if not row:
            return None, {}
        return row["state"], json.loads(row["data"] or "{}")

    async def fsm_set_state(self, key: str, state: str | None) -> None:
        await self.execute(
            "INSERT INTO fsm (k, state, data) VALUES (?, ?, '{}') "
            "ON CONFLICT(k) DO UPDATE SET state=excluded.state",
            key, state,
        )

    async def fsm_set_data(self, key: str, data: dict) -> None:
        await self.execute(
            "INSERT INTO fsm (k, state, data) VALUES (?, NULL, ?) "
            "ON CONFLICT(k) DO UPDATE SET data=excluded.data",
            key, json.dumps(data, ensure_ascii=False),
        )
