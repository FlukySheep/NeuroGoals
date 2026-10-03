import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _ids(value: str | None) -> frozenset[int]:
    return frozenset(int(x) for x in (value or "").replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class WorkConfig:
    bot_token: str
    chat_ids: frozenset[int]     # groups the bot works in; empty = answers only /chatid
    admin_ids: frozenset[int]    # besides the group's own admins
    trello_key: str
    trello_token: str
    trello_workspace: str        # optional Trello workspace (organization) id/name for the board
    tz: ZoneInfo
    database_url: str
    webhook_secret: str
    public_url: str


def load_work_config() -> WorkConfig:
    token = os.getenv("WORK_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WORK_BOT_TOKEN is not set (see .env.example)")
    return WorkConfig(
        bot_token=token,
        chat_ids=_ids(os.getenv("WORK_CHAT_ID")),
        admin_ids=_ids(os.getenv("WORK_ADMIN_IDS")),
        trello_key=os.getenv("TRELLO_KEY", "").strip(),
        trello_token=os.getenv("TRELLO_TOKEN", "").strip(),
        trello_workspace=os.getenv("TRELLO_WORKSPACE", "").strip(),
        tz=ZoneInfo(os.getenv("WORK_TZ", "Europe/Moscow").strip()),
        database_url=(
            os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("WORK_DB_PATH") or "workbot.db"
        ).strip(),
        webhook_secret=os.getenv("WORK_WEBHOOK_SECRET", "").strip(),
        public_url=os.getenv("PUBLIC_URL", "").strip().rstrip("/"),
    )
