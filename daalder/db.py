"""asyncpg pool management, idempotent schema setup, and query helpers."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_pool(database_url: str) -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=10)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is niet geïnitialiseerd")
    return _pool


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        telegram_user_id BIGINT PRIMARY KEY,
        plan TEXT NOT NULL DEFAULT 'free',
        plan_expires_at TIMESTAMPTZ NULL,
        is_recurring BOOLEAN NOT NULL DEFAULT FALSE,
        telegram_charge_id TEXT NULL,
        language TEXT NOT NULL DEFAULT 'nl',
        renewal_reminder_sent BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS renewal_reminder_sent BOOLEAN NOT NULL DEFAULT FALSE",
    """
    CREATE TABLE IF NOT EXISTS products (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(telegram_user_id),
        url TEXT NOT NULL,
        domain TEXT NOT NULL,
        name TEXT,
        currency TEXT DEFAULT 'EUR',
        extraction_strategy TEXT,
        target_price NUMERIC NULL,
        last_price NUMERIC NULL,
        last_notified_price NUMERIC NULL,
        last_checked_at TIMESTAMPTZ NULL,
        last_check_status TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_points (
        id BIGSERIAL PRIMARY KEY,
        product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        price NUMERIC NOT NULL,
        in_stock BOOLEAN,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_price_points_product_checked ON price_points(product_id, checked_at)",
    "CREATE INDEX IF NOT EXISTS idx_products_active_checked ON products(active, last_checked_at)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS parent_product_id BIGINT NULL REFERENCES products(id)",
    "CREATE INDEX IF NOT EXISTS idx_products_parent ON products(parent_product_id)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS target_price_set_at TIMESTAMPTZ NULL",
]


async def init_schema() -> None:
    async with pool().acquire() as conn:
        async with conn.transaction():
            for statement in SCHEMA_STATEMENTS:
                await conn.execute(statement)
    logger.info("Databaseschema gecontroleerd/aangemaakt.")


# --- users -------------------------------------------------------------------


async def get_or_create_user(telegram_user_id: int) -> asyncpg.Record:
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO users (telegram_user_id)
            VALUES ($1)
            ON CONFLICT (telegram_user_id) DO UPDATE SET telegram_user_id = users.telegram_user_id
            RETURNING *
            """,
            telegram_user_id,
        )


async def get_user(telegram_user_id: int) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id = $1", telegram_user_id)


async def set_plan(
    telegram_user_id: int,
    *,
    plan: str,
    plan_expires_at: Optional[datetime],
    is_recurring: bool,
    telegram_charge_id: Optional[str] = None,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET plan = $2,
                plan_expires_at = $3,
                is_recurring = $4,
                telegram_charge_id = COALESCE($5, telegram_charge_id),
                renewal_reminder_sent = false
            WHERE telegram_user_id = $1
            """,
            telegram_user_id,
            plan,
            plan_expires_at,
            is_recurring,
            telegram_charge_id,
        )


async def get_expired_plus_users() -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM users
            WHERE plan = 'plus' AND plan_expires_at IS NOT NULL AND plan_expires_at < now()
            """
        )


async def get_users_due_for_renewal_reminder(days_before: int) -> list[asyncpg.Record]:
    """Non-recurring (annual) Plus users whose plan expires within `days_before`
    days and who haven't been reminded yet for this expiry."""
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM users
            WHERE plan = 'plus'
            AND is_recurring = false
            AND renewal_reminder_sent = false
            AND plan_expires_at IS NOT NULL
            AND plan_expires_at BETWEEN now() AND now() + ($1 * INTERVAL '1 day')
            """,
            days_before,
        )


async def mark_renewal_reminder_sent(telegram_user_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET renewal_reminder_sent = true WHERE telegram_user_id = $1",
            telegram_user_id,
        )


async def list_users_with_usage() -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT u.*,
                (
                    SELECT count(*) FROM products
                    WHERE user_id = u.telegram_user_id AND active = true AND parent_product_id IS NULL
                ) AS product_count,
                (
                    SELECT count(*) FROM products
                    WHERE user_id = u.telegram_user_id AND active = true
                ) AS store_count
            FROM users u
            ORDER BY u.created_at DESC
            """
        )


# --- products ------------------------------------------------------------------


async def count_active_products(user_id: int) -> int:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM products WHERE user_id = $1 AND active = true AND parent_product_id IS NULL",
            user_id,
        )


async def count_product_urls(product_id: int) -> int:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            """
            SELECT count(*) FROM products
            WHERE (id = $1 OR parent_product_id = $1) AND active = true
            """,
            product_id,
        )


async def create_product(
    *,
    user_id: int,
    url: str,
    domain: str,
    name: Optional[str],
    currency: str,
    strategy: str,
    price: Decimal,
    in_stock: Optional[bool],
) -> asyncpg.Record:
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO products (
                    user_id, url, domain, name, currency, extraction_strategy,
                    last_price, last_checked_at, last_check_status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, now(), 'ok')
                RETURNING *
                """,
                user_id,
                url,
                domain,
                name,
                currency,
                strategy,
                price,
            )
            await conn.execute(
                "INSERT INTO price_points (product_id, price, in_stock) VALUES ($1, $2, $3)",
                row["id"],
                price,
                in_stock,
            )
            return row


async def add_product_url(
    *,
    product_id: int,
    user_id: int,
    url: str,
    domain: str,
    name: Optional[str],
    currency: str,
    strategy: str,
    price: Decimal,
    in_stock: Optional[bool],
) -> Optional[asyncpg.Record]:
    """Attach a new store URL as a child row of an owned root product."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO products (
                    user_id, parent_product_id, url, domain, name, currency, extraction_strategy,
                    last_price, last_checked_at, last_check_status
                )
                SELECT $1, $2, $3, $4, $5, $6, $7, $8, now(), 'ok'
                WHERE EXISTS (
                    SELECT 1 FROM products WHERE id = $2 AND user_id = $1 AND parent_product_id IS NULL
                )
                RETURNING *
                """,
                user_id,
                product_id,
                url,
                domain,
                name,
                currency,
                strategy,
                price,
            )
            if row is None:
                return None
            await conn.execute(
                "INSERT INTO price_points (product_id, price, in_stock) VALUES ($1, $2, $3)",
                row["id"],
                price,
                in_stock,
            )
            return row


async def get_product(product_id: int) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)


async def get_owned_product(product_id: int, user_id: int) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM products WHERE id = $1 AND user_id = $2", product_id, user_id
        )


async def get_child_urls(product_id: int) -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM products
            WHERE parent_product_id = $1 AND active = true
            ORDER BY created_at ASC
            """,
            product_id,
        )


async def url_exists_for_user(user_id: int, url: str) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM products WHERE user_id = $1 AND url = $2 AND active = true LIMIT 1",
            user_id,
            url,
        )


async def list_products(user_id: int) -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT p.*,
                (
                    SELECT price FROM price_points
                    WHERE product_id = p.id
                    ORDER BY checked_at ASC LIMIT 1
                ) AS first_price,
                (
                    SELECT MIN(last_price) FROM products
                    WHERE id = p.id OR parent_product_id = p.id
                ) AS cheapest_price,
                (
                    SELECT count(*) FROM products
                    WHERE (id = p.id OR parent_product_id = p.id) AND active = true
                ) AS store_count
            FROM products p
            WHERE p.user_id = $1 AND p.active = true AND p.parent_product_id IS NULL
            ORDER BY p.created_at ASC
            """,
            user_id,
        )


async def get_group_prices(product_id: int) -> asyncpg.Record:
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT MIN(last_price) AS cheapest, AVG(last_price) AS average,
                (
                    SELECT domain FROM products
                    WHERE (id = $1 OR parent_product_id = $1) AND active = true
                    ORDER BY last_price ASC NULLS LAST LIMIT 1
                ) AS cheapest_domain
            FROM products
            WHERE (id = $1 OR parent_product_id = $1) AND active = true
            """,
            product_id,
        )


async def get_group_price_points(product_id: int) -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT pr.domain, pp.checked_at, pp.price
            FROM price_points pp
            JOIN products pr ON pr.id = pp.product_id
            WHERE pr.id = $1 OR pr.parent_product_id = $1
            ORDER BY pp.checked_at ASC
            """,
            product_id,
        )


async def set_target_price(product_id: int, user_id: int, target_price: Decimal) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            "UPDATE products SET target_price = $3, target_price_set_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
            product_id,
            user_id,
            target_price,
        )


async def deactivate_product(product_id: int, user_id: int) -> bool:
    """Deactivate a product and, if it's a group root, all of its child stores."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE products SET active = false
            WHERE user_id = $2 AND (id = $1 OR parent_product_id = $1)
            RETURNING id
            """,
            product_id,
            user_id,
        )
        return len(rows) > 0


async def remove_store(product_id: int, user_id: int) -> Optional[asyncpg.Record]:
    """Deactivate one store URL from a group.

    If it's a child row, simply deactivate it. If it's the group root,
    promote the oldest remaining active child to root (carrying over the
    target price and reparenting any siblings) before deactivating the old
    root row, so the rest of the group keeps being tracked as one product.
    Falls back to deactivating the whole group if the root has no other
    active stores left.
    """
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM products WHERE id = $1 AND user_id = $2 AND active = true",
                product_id,
                user_id,
            )
            if row is None:
                return None

            if row["parent_product_id"] is not None:
                await conn.execute("UPDATE products SET active = false WHERE id = $1", row["id"])
                return row

            new_root = await conn.fetchrow(
                """
                SELECT * FROM products
                WHERE parent_product_id = $1 AND active = true
                ORDER BY created_at ASC LIMIT 1
                """,
                row["id"],
            )
            if new_root is None:
                await conn.execute(
                    "UPDATE products SET active = false WHERE id = $1 OR parent_product_id = $1",
                    row["id"],
                )
                return row

            await conn.execute(
                "UPDATE products SET parent_product_id = NULL, target_price = $2, target_price_set_at = $3 WHERE id = $1",
                new_root["id"],
                row["target_price"],
                row["target_price_set_at"],
            )
            await conn.execute(
                "UPDATE products SET parent_product_id = $1 WHERE parent_product_id = $2",
                new_root["id"],
                row["id"],
            )
            await conn.execute("UPDATE products SET active = false WHERE id = $1", row["id"])
            return row


async def get_due_products(free_hours: int, plus_hours: int) -> list[asyncpg.Record]:
    """Rows (roots or child store URLs) due for a check: Plus owners on
    `plus_hours`, free owners (only the stores under their single
    most-recently-added active root product) on `free_hours`."""
    async with pool().acquire() as conn:
        return await conn.fetch(
            """
            SELECT p.*,
                COALESCE(root.name, p.name) AS group_name,
                (
                    p.parent_product_id IS NOT NULL
                    OR EXISTS (
                        SELECT 1 FROM products c
                        WHERE c.parent_product_id = p.id AND c.active = true
                    )
                ) AS is_multi_store
            FROM products p
            LEFT JOIN products root ON root.id = p.parent_product_id
            JOIN users u ON u.telegram_user_id = p.user_id
            WHERE p.active = true
            AND (
                (
                    u.plan = 'plus'
                    AND (p.last_checked_at IS NULL OR p.last_checked_at < now() - ($2 * INTERVAL '1 hour'))
                )
                OR
                (
                    u.plan = 'free'
                    AND COALESCE(p.parent_product_id, p.id) = (
                        SELECT id FROM products p2
                        WHERE p2.user_id = p.user_id AND p2.active = true AND p2.parent_product_id IS NULL
                        ORDER BY p2.created_at DESC LIMIT 1
                    )
                    AND (p.last_checked_at IS NULL OR p.last_checked_at < now() - ($1 * INTERVAL '1 hour'))
                )
            )
            """,
            free_hours,
            plus_hours,
        )


async def update_check_result(
    product_id: int,
    *,
    status: str,
    price: Optional[Decimal] = None,
    currency: Optional[str] = None,
    in_stock: Optional[bool] = None,
    strategy: Optional[str] = None,
    name: Optional[str] = None,
) -> None:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE products
                SET last_check_status = $2,
                    last_checked_at = now(),
                    last_price = COALESCE($3, last_price),
                    currency = COALESCE($4, currency),
                    extraction_strategy = COALESCE($5, extraction_strategy),
                    name = COALESCE(name, $6)
                WHERE id = $1
                """,
                product_id,
                status,
                price,
                currency,
                strategy,
                name,
            )
            if price is not None:
                await conn.execute(
                    "INSERT INTO price_points (product_id, price, in_stock) VALUES ($1, $2, $3)",
                    product_id,
                    price,
                    in_stock,
                )


async def set_notified_price(product_id: int, price: Decimal) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE products SET last_notified_price = $2 WHERE id = $1", product_id, price
        )


