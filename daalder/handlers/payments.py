"""/upgrade, /status, /paysupport, Stars invoices and payment callbacks.

The `grant_plus` / `revoke_plus` pair is the whole payment abstraction: every
other module that needs to change a user's plan goes through these two
functions instead of touching `db.set_plan` directly. Swapping Telegram Stars
for Mollie/iDEAL later means adding a new checkout entry point that still
calls `grant_plus`/`revoke_plus` — see the README's "Future: Mollie" section.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.ext import ContextTypes

from daalder import config, db, texts

logger = logging.getLogger(__name__)

_PAYLOAD_MONTHLY = "plus_monthly"
_PAYLOAD_ANNUAL = "plus_annual"


async def grant_plus(user_id: int, *, days: int, recurring: bool, charge_id: Optional[str]) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    await db.set_plan(
        user_id,
        plan="plus",
        plan_expires_at=expires_at,
        is_recurring=recurring,
        telegram_charge_id=charge_id,
    )


async def revoke_plus(user_id: int) -> None:
    await db.set_plan(user_id, plan="free", plan_expires_at=None, is_recurring=False)


def _upgrade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(texts.upgrade_button_monthly(config.MONTHLY_STARS), callback_data="upgrade_monthly")],
            [InlineKeyboardButton(texts.upgrade_button_annual(config.ANNUAL_STARS), callback_data="upgrade_annual")],
        ]
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.get_or_create_user(user.id)
    db_user = await db.get_user(user.id)
    product_count = await db.count_active_products(user.id)
    expires_text = db_user["plan_expires_at"].strftime("%d-%m-%Y") if db_user["plan_expires_at"] else None
    text = texts.status_text(db_user["plan"], product_count, expires_text, db_user["is_recurring"])
    await update.message.reply_html(text)


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(texts.UPGRADE_INTRO, reply_markup=_upgrade_keyboard())


async def go_upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_html(texts.UPGRADE_INTRO, reply_markup=_upgrade_keyboard())


async def upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    kind = query.data.split("_", 1)[1]  # "monthly" | "annual"
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if kind == "monthly":
        payload = f"{_PAYLOAD_MONTHLY}:{user_id}:{uuid.uuid4().hex}"
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=texts.INVOICE_TITLE_MONTHLY,
            description=texts.INVOICE_DESC_MONTHLY,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(texts.INVOICE_TITLE_MONTHLY, config.MONTHLY_STARS)],
            subscription_period=config.MONTHLY_SUBSCRIPTION_PERIOD_SECONDS,
        )
    else:
        payload = f"{_PAYLOAD_ANNUAL}:{user_id}:{uuid.uuid4().hex}"
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=texts.INVOICE_TITLE_ANNUAL,
            description=texts.INVOICE_DESC_ANNUAL,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(texts.INVOICE_TITLE_ANNUAL, config.ANNUAL_STARS)],
        )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""
    if payload.startswith(_PAYLOAD_MONTHLY) or payload.startswith(_PAYLOAD_ANNUAL):
        await query.answer(ok=True)
    else:
        logger.warning("Onbekende precheckout-payload: %s", payload)
        await query.answer(ok=False, error_message="Onbekende bestelling, probeer /upgrade opnieuw.")


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload or ""

    if payload.startswith(_PAYLOAD_MONTHLY):
        await grant_plus(
            user_id,
            days=config.MONTHLY_PLAN_DAYS,
            recurring=True,
            charge_id=payment.telegram_payment_charge_id,
        )
        await update.message.reply_html(texts.PAYMENT_THANKS_MONTHLY)
    elif payload.startswith(_PAYLOAD_ANNUAL):
        await grant_plus(
            user_id,
            days=config.ANNUAL_PLAN_DAYS,
            recurring=False,
            charge_id=payment.telegram_payment_charge_id,
        )
        await update.message.reply_html(texts.PAYMENT_THANKS_ANNUAL)
    else:
        logger.warning("Onbekende betaalpayload ontvangen: %s", payload)


async def paysupport_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(texts.PAYSUPPORT_TEXT)


async def refund_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.ADMIN_USER_ID or update.effective_user.id != config.ADMIN_USER_ID:
        await update.message.reply_html(texts.ADMIN_ONLY)
        return

    if not context.args:
        await update.message.reply_html(texts.REFUND_USAGE)
        return

    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_html(texts.REFUND_USAGE)
        return

    target_user = await db.get_user(target_user_id)
    if target_user is None or not target_user["telegram_charge_id"]:
        await update.message.reply_html(texts.REFUND_NO_USER)
        return

    try:
        await context.bot.refund_star_payment(
            user_id=target_user_id,
            telegram_payment_charge_id=target_user["telegram_charge_id"],
        )
    except Exception as exc:
        logger.exception("Terugbetaling mislukt voor %s", target_user_id)
        await update.message.reply_html(texts.REFUND_FAILED.format(error=str(exc)))
        return

    await revoke_plus(target_user_id)
    await update.message.reply_html(texts.REFUND_SUCCESS.format(user_id=target_user_id))
