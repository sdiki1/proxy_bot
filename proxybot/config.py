from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    database_path: str
    admin_tg_ids: tuple[int, ...]
    proxy_public_host: str
    proxy_pool_file: str
    expiration_check_interval: int


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _int_tuple_env(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return tuple()
    result: list[int] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        result.append(int(value))
    return tuple(result)


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is required. Set it in environment or .env file.")

    return Settings(
        bot_token=bot_token,
        database_url=os.getenv("DATABASE_URL", "").strip(),
        database_path=os.getenv("DATABASE_PATH", "bot.db").strip() or "bot.db",
        admin_tg_ids=_int_tuple_env("ADMIN_TG_IDS"),
        proxy_public_host=os.getenv("PROXY_PUBLIC_HOST", "127.0.0.1").strip() or "127.0.0.1",
        proxy_pool_file=os.getenv("PROXY_POOL_FILE", "data/proxy_pool.json").strip() or "data/proxy_pool.json",
        expiration_check_interval=_int_env("EXPIRATION_CHECK_INTERVAL", 60),
    )
