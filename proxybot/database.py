from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.parse import quote

import aiosqlite


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    devices_count: int
    price_rub: int
    duration_days: int


@dataclass(frozen=True)
class ProxyPoolEntry:
    port: int
    username: str
    password: str


DEFAULT_PLANS = (
    Plan(code="one", title="1 ссылка (1 устройство)", devices_count=1, price_rub=10, duration_days=30),
    Plan(code="five", title="5 ссылок (5 устройств)", devices_count=5, price_rub=25, duration_days=30),
    Plan(code="fifteen", title="15 ссылок (15 устройств)", devices_count=15, price_rub=50, duration_days=30),
)


def now_ts() -> int:
    return int(time.time())


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        return self._conn

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plans (
                code TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                devices_count INTEGER NOT NULL,
                price_rub INTEGER NOT NULL,
                duration_days INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                plan_code TEXT NOT NULL REFERENCES plans(code),
                amount_rub INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'paid', 'cancelled')),
                created_at INTEGER NOT NULL,
                paid_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                plan_code TEXT NOT NULL REFERENCES plans(code),
                payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id),
                status TEXT NOT NULL CHECK(status IN ('active', 'expired')),
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                notified_expired INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS proxy_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                device_number INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                link TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active', 'expired')),
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proxy_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                port INTEGER NOT NULL UNIQUE,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('free', 'assigned')),
                assigned_link_id INTEGER UNIQUE REFERENCES proxy_links(id),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proxy_delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_link_id INTEGER NOT NULL REFERENCES proxy_links(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                tg_user_id INTEGER NOT NULL,
                user_label TEXT NOT NULL,
                subscription_id INTEGER REFERENCES subscriptions(id),
                device_number INTEGER,
                delivery_source TEXT NOT NULL CHECK(delivery_source IN ('purchase', 'my_links')),
                proxy_url TEXT NOT NULL,
                delivered_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_users_tg_user_id ON users(tg_user_id);
            CREATE INDEX IF NOT EXISTS idx_payments_user_status ON payments(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_subscriptions_user_status ON subscriptions(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_subscriptions_expires_at ON subscriptions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_proxy_links_user_status ON proxy_links(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_proxy_links_expires_at ON proxy_links(expires_at);
            CREATE INDEX IF NOT EXISTS idx_proxy_pool_status ON proxy_pool(status);
            CREATE INDEX IF NOT EXISTS idx_proxy_delivery_logs_tg_user_id ON proxy_delivery_logs(tg_user_id);
            CREATE INDEX IF NOT EXISTS idx_proxy_delivery_logs_proxy_link_id ON proxy_delivery_logs(proxy_link_id);
            """
        )
        await self.seed_plans()
        await self.conn.commit()

    async def seed_plans(self) -> None:
        for plan in DEFAULT_PLANS:
            await self.conn.execute(
                """
                INSERT INTO plans (code, title, devices_count, price_rub, duration_days)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    title = excluded.title,
                    devices_count = excluded.devices_count,
                    price_rub = excluded.price_rub,
                    duration_days = excluded.duration_days
                """,
                (plan.code, plan.title, plan.devices_count, plan.price_rub, plan.duration_days),
            )

    async def sync_proxy_pool(self, entries: list[ProxyPoolEntry]) -> None:
        if not entries:
            return

        timestamp = now_ts()
        for item in entries:
            await self.conn.execute(
                """
                INSERT INTO proxy_pool (port, username, password, status, created_at, updated_at)
                VALUES (?, ?, ?, 'free', ?, ?)
                ON CONFLICT(port) DO UPDATE SET
                    username = excluded.username,
                    password = excluded.password,
                    updated_at = excluded.updated_at
                """,
                (item.port, item.username, item.password, timestamp, timestamp),
            )
        await self.conn.commit()

    async def upsert_user(
        self,
        tg_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> int:
        timestamp = now_ts()
        await self.conn.execute(
            """
            INSERT INTO users (tg_user_id, username, first_name, last_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tg_user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                updated_at = excluded.updated_at
            """,
            (tg_user_id, username, first_name, last_name, timestamp, timestamp),
        )
        cursor = await self.conn.execute(
            "SELECT id FROM users WHERE tg_user_id = ?",
            (tg_user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        await self.conn.commit()
        if row is None:
            raise RuntimeError("Failed to upsert user.")
        return int(row["id"])

    async def get_plan(self, code: str) -> Plan | None:
        cursor = await self.conn.execute(
            """
            SELECT code, title, devices_count, price_rub, duration_days
            FROM plans
            WHERE code = ?
            """,
            (code,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return Plan(
            code=row["code"],
            title=row["title"],
            devices_count=int(row["devices_count"]),
            price_rub=int(row["price_rub"]),
            duration_days=int(row["duration_days"]),
        )

    async def get_plans(self) -> list[Plan]:
        cursor = await self.conn.execute(
            """
            SELECT code, title, devices_count, price_rub, duration_days
            FROM plans
            ORDER BY devices_count ASC
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            Plan(
                code=row["code"],
                title=row["title"],
                devices_count=int(row["devices_count"]),
                price_rub=int(row["price_rub"]),
                duration_days=int(row["duration_days"]),
            )
            for row in rows
        ]

    async def create_payment(self, user_id: int, plan_code: str, amount_rub: int) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO payments (user_id, plan_code, amount_rub, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (user_id, plan_code, amount_rub, now_ts()),
        )
        payment_id = int(cursor.lastrowid)
        await self.conn.commit()
        return payment_id

    async def get_payment_for_user(self, payment_id: int, user_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT id, user_id, plan_code, amount_rub, status, created_at, paid_at
            FROM payments
            WHERE id = ? AND user_id = ?
            """,
            (payment_id, user_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def cancel_pending_payment(self, payment_id: int, user_id: int) -> bool:
        cursor = await self.conn.execute(
            """
            UPDATE payments
            SET status = 'cancelled'
            WHERE id = ? AND user_id = ? AND status = 'pending'
            """,
            (payment_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def count_free_pool(self) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM proxy_pool WHERE status = 'free'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["cnt"]) if row is not None else 0

    async def activate_payment_and_create_subscription_from_pool(
        self,
        *,
        payment_id: int,
        user_id: int,
        plan_code: str,
        expires_at: int,
        devices_count: int,
        proxy_public_host: str,
    ) -> tuple[int, list[dict[str, Any]]] | None:
        timestamp = now_ts()
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.conn.execute(
                """
                UPDATE payments
                SET status = 'paid', paid_at = ?
                WHERE id = ? AND user_id = ? AND status = 'pending'
                """,
                (timestamp, payment_id, user_id),
            )
            if cursor.rowcount == 0:
                await self.conn.rollback()
                return None

            cursor = await self.conn.execute(
                """
                SELECT id, port, username, password
                FROM proxy_pool
                WHERE status = 'free'
                ORDER BY port ASC
                LIMIT ?
                """,
                (devices_count,),
            )
            proxy_rows = await cursor.fetchall()
            await cursor.close()
            if len(proxy_rows) < devices_count:
                await self.conn.rollback()
                return None

            cursor = await self.conn.execute(
                """
                INSERT INTO subscriptions (user_id, plan_code, payment_id, status, created_at, expires_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (user_id, plan_code, payment_id, timestamp, expires_at),
            )
            subscription_id = int(cursor.lastrowid)

            created: list[dict[str, Any]] = []
            for device_number, proxy_row in enumerate(proxy_rows, start=1):
                port = int(proxy_row["port"])
                username = str(proxy_row["username"])
                password = str(proxy_row["password"])

                username_safe = quote(username, safe="")
                password_safe = quote(password, safe="")
                link = f"socks5://{username_safe}:{password_safe}@{proxy_public_host}:{port}"

                cursor = await self.conn.execute(
                    """
                    INSERT INTO proxy_links (
                        subscription_id, user_id, device_number, token, link, status, created_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        subscription_id,
                        user_id,
                        device_number,
                        secrets.token_urlsafe(18),
                        link,
                        timestamp,
                        expires_at,
                    ),
                )
                link_id = int(cursor.lastrowid)

                updated = await self.conn.execute(
                    """
                    UPDATE proxy_pool
                    SET status = 'assigned', assigned_link_id = ?, updated_at = ?
                    WHERE id = ? AND status = 'free'
                    """,
                    (link_id, timestamp, int(proxy_row["id"])),
                )
                if updated.rowcount == 0:
                    raise RuntimeError("Failed to assign proxy from pool")

                created.append(
                    {
                        "proxy_id": link_id,
                        "device_number": device_number,
                        "port": port,
                        "username": username,
                        "password": password,
                        "link": link,
                    }
                )

            await self.conn.commit()
            return subscription_id, created
        except Exception:
            await self.conn.rollback()
            raise

    async def log_proxy_delivery(
        self,
        *,
        proxy_link_id: int,
        user_id: int,
        tg_user_id: int,
        user_label: str,
        subscription_id: int | None,
        device_number: int | None,
        delivery_source: str,
        proxy_url: str,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO proxy_delivery_logs (
                proxy_link_id,
                user_id,
                tg_user_id,
                user_label,
                subscription_id,
                device_number,
                delivery_source,
                proxy_url,
                delivered_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proxy_link_id,
                user_id,
                tg_user_id,
                user_label,
                subscription_id,
                device_number,
                delivery_source,
                proxy_url,
                now_ts(),
            ),
        )
        await self.conn.commit()

    async def get_active_links_for_user(self, user_id: int) -> list[dict[str, Any]]:
        timestamp = now_ts()
        cursor = await self.conn.execute(
            """
            SELECT
                pl.id,
                pl.subscription_id,
                pl.device_number,
                pl.link,
                pl.expires_at,
                p.title AS plan_title
            FROM proxy_links pl
            JOIN subscriptions s ON s.id = pl.subscription_id
            JOIN plans p ON p.code = s.plan_code
            WHERE
                pl.user_id = ?
                AND pl.status = 'active'
                AND pl.expires_at > ?
                AND s.status = 'active'
            ORDER BY pl.expires_at ASC, pl.subscription_id ASC, pl.device_number ASC
            """,
            (user_id, timestamp),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def get_active_subscriptions_for_user(self, user_id: int) -> list[dict[str, Any]]:
        timestamp = now_ts()
        cursor = await self.conn.execute(
            """
            SELECT
                s.id,
                s.plan_code,
                s.expires_at,
                p.title AS plan_title,
                p.price_rub,
                p.devices_count
            FROM subscriptions s
            JOIN plans p ON p.code = s.plan_code
            WHERE s.user_id = ? AND s.status = 'active' AND s.expires_at > ?
            ORDER BY s.expires_at ASC
            """,
            (user_id, timestamp),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def expire_due_and_get_notified_users(self) -> list[int]:
        timestamp = now_ts()
        await self.conn.execute(
            """
            UPDATE subscriptions
            SET status = 'expired'
            WHERE status = 'active' AND expires_at <= ?
            """,
            (timestamp,),
        )
        await self.conn.execute(
            """
            UPDATE proxy_links
            SET status = 'expired'
            WHERE status = 'active' AND expires_at <= ?
            """,
            (timestamp,),
        )
        await self.conn.execute(
            """
            UPDATE proxy_pool
            SET status = 'free', assigned_link_id = NULL, updated_at = ?
            WHERE assigned_link_id IN (
                SELECT id FROM proxy_links WHERE status = 'expired'
            )
            """,
            (timestamp,),
        )

        cursor = await self.conn.execute(
            """
            SELECT DISTINCT u.tg_user_id
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.status = 'expired' AND s.notified_expired = 0
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()

        await self.conn.execute(
            """
            UPDATE subscriptions
            SET notified_expired = 1
            WHERE status = 'expired' AND notified_expired = 0
            """
        )
        await self.conn.commit()

        return [int(row["tg_user_id"]) for row in rows]
