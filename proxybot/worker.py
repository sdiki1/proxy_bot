from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from .database import Database
from .proxy_pool_loader import load_proxy_pool

logger = logging.getLogger(__name__)


async def expiration_worker(bot: Bot, db: Database, check_interval: int) -> None:
    while True:
        try:
            user_ids = await db.expire_due_and_get_notified_users()
            for tg_user_id in user_ids:
                try:
                    await bot.send_message(
                        tg_user_id,
                        (
                            "Срок действия вашей подписки истек.\n"
                            "Прокси-ссылки деактивированы. Вы можете купить новый тариф через /buy."
                        ),
                    )
                except (TelegramBadRequest, TelegramForbiddenError):
                    logger.warning("Could not send expiration notification to user %s", tg_user_id)
        except Exception:
            logger.exception("Expiration worker iteration failed")

        await asyncio.sleep(max(10, check_interval))


async def proxy_pool_sync_worker(db: Database, pool_file: str, check_interval: int = 30) -> None:
    while True:
        try:
            pool = load_proxy_pool(pool_file)
            if pool:
                await db.sync_proxy_pool(pool)
        except Exception:
            logger.exception("Proxy pool sync iteration failed")

        await asyncio.sleep(max(10, check_interval))
