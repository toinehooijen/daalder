"""Add-by-URL flow, /lijst, product detail, target price, and removal."""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from daalder import config, db, texts
from daalder.charts import render_history_chart
from daalder.scraping import extract_price, get_domain
from daalder.scraping.structured import parse_price_string

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")


def _extract_url(text: str) -> Optional[str]:
    match = _URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(").,!?\"'")


def _product_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(texts.BTN_CHART, callback_data=f"detail:{product_id}")],
            [
                InlineKeyboardButton(texts.BTN_TARGET, callback_data=f"target:{product_id}"),
                InlineKeyboardButton(texts.BTN_REMOVE, callback_data=f"remove:{product_id}"),
            ],
        ]
    )


def _detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(texts.BTN_TARGET, callback_data=f"target:{product_id}"),
                InlineKeyboardButton(texts.BTN_REMOVE, callback_data=f"remove:{product_id}"),
            ]
        ]
    )


def _delta_display(first_price, last_price, currency: str) -> Tuple[str, str]:
    if first_price is None or last_price is None:
        return "", ""
    diff = last_price - first_price
    if diff > 0:
        return "▲", texts.format_price(diff, currency)
    if diff < 0:
        return "▼", texts.format_price(-diff, currency)
    return "→", ""


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or not message.text:
        return

    url = _extract_url(message.text)
    awaiting_target = context.user_data.get("awaiting_target_for")

    if url:
        context.user_data.pop("awaiting_target_for", None)
        await _handle_add_product(update, context, url)
        return

    if awaiting_target:
        await _handle_target_price_reply(update, context, awaiting_target)
        return

    await message.reply_html(texts.NO_URL_HINT)


async def _handle_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    user = update.effective_user
    db_user = await db.get_or_create_user(user.id)

    existing = await db.count_active_products(user.id)
    if db_user["plan"] == "free" and existing >= config.FREE_PRODUCT_LIMIT:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(texts.BTN_UPGRADE, callback_data="go_upgrade")]])
        await update.message.reply_html(texts.FREE_LIMIT_UPSELL, reply_markup=keyboard)
        return

    placeholder = await update.message.reply_html(texts.FETCHING_PLACEHOLDER)

    try:
        result = await extract_price(url)
    except Exception:
        logger.exception("extract_price crashte voor %s", url)
        await placeholder.edit_text(texts.ADD_FAILED_ERROR, parse_mode=ParseMode.HTML)
        return

    if not result.ok:
        message_text = {
            "blocked": texts.ADD_FAILED_BLOCKED,
            "not_found": texts.ADD_FAILED_NOT_FOUND,
        }.get(result.status, texts.ADD_FAILED_ERROR)
        await placeholder.edit_text(message_text, parse_mode=ParseMode.HTML)
        return

    domain = get_domain(url)
    product = await db.create_product(
        user_id=user.id,
        url=url,
        domain=domain,
        name=result.name,
        currency=result.currency,
        strategy=result.strategy,
        price=result.price,
        in_stock=result.in_stock,
    )

    name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
    text = texts.product_added(name, texts.format_price(result.price, result.currency))
    await placeholder.edit_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_product_keyboard(product["id"])
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.get_or_create_user(user.id)
    products = await db.list_products(user.id)

    if not products:
        await update.message.reply_html(texts.LIST_EMPTY)
        return

    await update.message.reply_html(texts.LIST_INTRO)
    for product in products:
        arrow, delta_text = _delta_display(product["first_price"], product["last_price"], product["currency"])
        name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
        price_text = (
            texts.format_price(product["last_price"], product["currency"])
            if product["last_price"] is not None
            else "?"
        )
        line = texts.list_item(name, price_text, arrow, delta_text)
        if product["last_check_status"] == "blocked":
            line += f"\n{texts.LIST_ITEM_BLOCKED}"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(texts.BTN_DETAIL, callback_data=f"detail:{product['id']}")]]
        )
        await update.message.reply_html(line, reply_markup=keyboard)


async def detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    user_id = update.effective_user.id

    product = await db.get_owned_product(product_id, user_id)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return

    points = await db.get_price_points(product_id)
    name = product["name"] or texts.UNKNOWN_PRODUCT_NAME

    if len(points) < 2:
        await query.message.reply_html(texts.CHART_NOT_ENOUGH_DATA, reply_markup=_detail_keyboard(product_id))
        return

    prices = [p["price"] for p in points]
    lowest = min(prices)
    chart = render_history_chart(name, [(p["checked_at"], p["price"]) for p in points])
    caption = texts.detail_caption(
        texts.escape(name),
        texts.format_price(product["last_price"], product["currency"]),
        texts.format_price(lowest, product["currency"]),
    )
    await query.message.reply_photo(
        photo=chart,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=_detail_keyboard(product_id),
    )


async def target_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    product = await db.get_owned_product(product_id, update.effective_user.id)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return
    context.user_data["awaiting_target_for"] = product_id
    await query.message.reply_html(texts.TARGET_PROMPT)


async def _handle_target_price_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    price = parse_price_string(update.message.text)
    if price is None or price <= 0:
        await update.message.reply_html(texts.TARGET_INVALID)
        return

    updated = await db.set_target_price(product_id, update.effective_user.id, price)
    context.user_data.pop("awaiting_target_for", None)
    if updated is None:
        await update.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return

    await update.message.reply_html(
        texts.target_set(texts.format_price(price, updated["currency"])),
        reply_markup=_detail_keyboard(product_id),
    )


async def remove_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    product = await db.get_owned_product(product_id, update.effective_user.id)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(texts.BTN_REMOVE_CONFIRM, callback_data=f"remove_yes:{product_id}"),
                InlineKeyboardButton(texts.BTN_REMOVE_CANCEL, callback_data=f"remove_no:{product_id}"),
            ]
        ]
    )
    name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
    await query.message.reply_html(texts.remove_confirm(name), reply_markup=keyboard)


async def remove_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    product = await db.get_owned_product(product_id, update.effective_user.id)
    if product is None:
        await query.edit_message_text(texts.PRODUCT_NOT_FOUND)
        return
    await db.deactivate_product(product_id, update.effective_user.id)
    name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
    await query.edit_message_text(texts.remove_done(name), parse_mode=ParseMode.HTML)


async def remove_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(texts.REMOVE_CANCELLED)
