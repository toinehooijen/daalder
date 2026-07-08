"""JobQueue jobs: periodic price checks and daily Plus-plan lapse handling."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from typing import Any, Dict, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from daalder import config, db, texts
from daalder.handlers.payments import revoke_plus
from daalder.scraping import extract_price

logger = logging.getLogger(__name__)


async def check_due_products_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        products = await db.get_due_products(config.FREE_CHECK_INTERVAL_HOURS, config.PLUS_CHECK_INTERVAL_HOURS)
    except Exception:
        logger.exception("Kon due products niet ophalen")
        return

    if not products:
        return

    logger.info("Scheduler: %d producten te controleren", len(products))

    by_domain: Dict[str, List[Any]] = defaultdict(list)
    for product in products:
        by_domain[product["domain"]].append(product)

    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_CHECKS)

    async def guarded_check(product: Any) -> None:
        async with semaphore:
            await _check_one_product(context, product)

    async def process_domain(items: List[Any]) -> None:
        for index, product in enumerate(items):
            if index > 0:
                delay = config.PER_DOMAIN_MIN_INTERVAL_SECONDS + random.uniform(0, config.PER_DOMAIN_JITTER_SECONDS)
                await asyncio.sleep(delay)
            await guarded_check(product)

    await asyncio.gather(*(process_domain(items) for items in by_domain.values()))


async def _check_one_product(context: ContextTypes.DEFAULT_TYPE, product: Any) -> None:
    try:
        result = await extract_price(product["url"])
    except Exception:
        logger.exception("Prijscontrole crashte voor product %s (%s)", product["id"], product["domain"])
        try:
            await db.update_check_result(product["id"], status="error")
        except Exception:
            logger.exception("Kon errorstatus niet opslaan voor product %s", product["id"])
        return

    if not result.ok:
        logger.info("Product %s (%s): status=%s", product["id"], product["domain"], result.status)
        await db.update_check_result(product["id"], status=result.status)
        return

    await db.update_check_result(
        product["id"],
        status="ok",
        price=result.price,
        currency=result.currency,
        in_stock=result.in_stock,
        strategy=result.strategy,
        name=result.name,
    )

    old_price = product["last_price"]
    new_price = result.price
    target_price = product["target_price"]
    last_notified = product["last_notified_price"]

    dropped = old_price is not None and new_price < old_price
    hit_target = target_price is not None and new_price <= target_price
    already_notified = last_notified is not None and new_price == last_notified

    if (dropped or hit_target) and not already_notified:
        await db.set_notified_price(product["id"], new_price)
        await _send_drop_alert(context, product, old_price, new_price)


async def _send_drop_alert(context: ContextTypes.DEFAULT_TYPE, product: Any, old_price, new_price) -> None:
    name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
    old_text = texts.format_price(old_price, product["currency"]) if old_price is not None else "?"
    new_text = texts.format_price(new_price, product["currency"])
    text = texts.drop_alert(name, old_text, new_text, product["url"])
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(texts.BTN_CHART, callback_data=f"detail:{product['id']}")]])
    try:
        await context.bot.send_message(product["user_id"], text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        logger.exception("Kon prijsdaling-melding niet versturen naar %s", product["user_id"])


async def check_lapsed_plans_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        users = await db.get_expired_plus_users()
    except Exception:
        logger.exception("Kon verlopen abonnementen niet ophalen")
        return

    for user in users:
        try:
            await revoke_plus(user["telegram_user_id"])
            await context.bot.send_message(user["telegram_user_id"], texts.PLAN_LAPSED, parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Kon verlopen abonnement niet afhandelen voor %s", user["telegram_user_id"])
