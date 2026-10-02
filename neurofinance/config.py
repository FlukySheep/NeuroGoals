import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_chat_id: int | None
    admin_ids: frozenset[int]
    manager_username: str
    group_links: dict[str, str]
    reminder_delay_hours: float
    db_path: str
    media: dict[str, str] = field(default_factory=dict)

    @property
    def manager_url(self) -> str:
        return f"https://t.me/{self.manager_username}"


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set (see .env.example)")

    admin_chat_id = _int_or_none(os.getenv("ADMIN_CHAT_ID"))
    admin_ids = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
    if not admin_ids and admin_chat_id and admin_chat_id > 0:
        admin_ids = {admin_chat_id}

    return Config(
        bot_token=token,
        admin_chat_id=admin_chat_id,
        admin_ids=frozenset(admin_ids),
        manager_username=os.getenv("MANAGER_USERNAME", "ZoeRai").lstrip("@"),
        group_links={
            "weekend": os.getenv("GROUP_LINK_WEEKEND", "").strip(),
            "weekday": os.getenv("GROUP_LINK_WEEKDAY", "").strip(),
        },
        reminder_delay_hours=float(os.getenv("REMINDER_DELAY_HOURS", "20")),
        db_path=os.getenv("DB_PATH", "neurofinance.db"),
        media={
            "portrait": os.getenv("MEDIA_PORTRAIT", "media/portrait.jpg").strip(),
            "program": os.getenv("MEDIA_PROGRAM", "media/program.jpg").strip(),
            "video_note": os.getenv("MEDIA_VIDEO_NOTE", "media/zoe_circle.mp4").strip(),
        },
    )
