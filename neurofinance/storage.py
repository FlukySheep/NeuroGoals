"""aiogram FSM storage kept in the database, so input state survives serverless invocations."""

from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from .db import Database


def _key(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id or 0}:{key.destiny}"


class DbStorage(BaseStorage):
    def __init__(self, db: Database):
        self.db = db

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self.db.fsm_set_state(_key(key), value)

    async def get_state(self, key: StorageKey) -> str | None:
        state, _ = await self.db.fsm_get(_key(key))
        return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self.db.fsm_set_data(_key(key), dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        _, data = await self.db.fsm_get(_key(key))
        return data

    async def close(self) -> None:
        pass
