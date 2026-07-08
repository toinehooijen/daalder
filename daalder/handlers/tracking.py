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
            [InlineKeyboardButton(texts.BTN_ADD_STORE, callback_data=f"addurl:{product_id}")],
        ]
    )


def _detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(texts.BTN_TARGET, callback_data=f"target:{product_id}"),
                InlineKeyboardButton(texts.BTN_REMOVE, callback_data=f"remove:{product_id}"),
            ],
            [InlineKeyboardButton(texts.BTN_ADD_STORE, callback_data=f"addurl:{product_id}")],
        ]
    )


def _child_detail_keyboard(child_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(texts.BTN_REMOVE_STORE, callback_data=f"rmurl:{child_id}")]]
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
    awaiting_url_for = context.user_data.get("awaiting_url_for_product")

    if url and awaiting_url_for:
        context.user_data.pop("awaiting_url_for_product", None)
        context.user_data.pop("awaiting_target_for", None)
        await _handle_add_url_to_product(update, context, awaiting_url_for, url)
        return

    if url:
        context.user_data.pop("awaiting_target_for", None)
        context.user_data.pop("awaiting_url_for_product", None)
        await _handle_add_product(update, context, url)
        return

    if awaiting_url_for:
        await message.reply_html(texts.ADD_URL_NO_URL_HINT)
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


async def addurl_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    user = update.effective_user
    product = await db.get_owned_product(product_id, user.id)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return

    db_user = await db.get_or_create_user(user.id)
    if db_user["plan"] == "free":
        store_count = await db.count_product_urls(product_id)
        if store_count >= config.FREE_STORE_LIMIT:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(texts.BTN_UPGRADE, callback_data="go_upgrade")]])
            await query.message.reply_html(texts.FREE_STORE_LIMIT_UPSELL, reply_markup=keyboard)
            return

    context.user_data["awaiting_url_for_product"] = product_id
    await query.message.reply_html(texts.ADD_URL_PROMPT)


async def _handle_add_url_to_product(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, url: str
) -> None:
    user = update.effective_user
    product = await db.get_owned_product(product_id, user.id)
    if product is None:
        await update.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return

    existing = await db.url_exists_for_user(user.id, url)
    if existing is not None:
        name = texts.escape(existing["name"] or texts.UNKNOWN_PRODUCT_NAME)
        await update.message.reply_html(texts.url_already_tracked(name))
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
    child = await db.add_product_url(
        product_id=product_id,
        user_id=user.id,
        url=url,
        domain=domain,
        name=result.name,
        currency=result.currency,
        strategy=result.strategy,
        price=result.price,
        in_stock=result.in_stock,
    )
    if child is None:
        await placeholder.edit_text(texts.PRODUCT_NOT_FOUND, parse_mode=ParseMode.HTML)
        return

    text = texts.url_added(domain, texts.format_price(result.price, result.currency))
    await placeholder.edit_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_product_keyboard(product_id)
    )


async def remove_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    url_id = int(query.data.split(":", 1)[1])
    user = update.effective_user
    row = await db.get_owned_product(url_id, user.id)
    if row is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return
    if row["parent_product_id"] is None:
        await query.message.reply_html(texts.REMOVE_URL_ONLY_ROOT_HINT)
        return
    await db.remove_product_url(url_id, user.id)
    await query.edit_message_text(texts.remove_url_done(row["domain"]), parse_mode=ParseMode.HTML)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.get_or_create_user(user.id)
    products = await db.list_products(user.id)

    if not products:
        await update.message.reply_html(texts.LIST_EMPTY)
        return

    await update.message.reply_html(texts.LIST_INTRO)
    for product in products:
        arrow, delta_text = _delta_display(product["first_price"], product["cheapest_price"], product["currency"])
        name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
        price_text = (
            texts.format_price(product["cheapest_price"], product["currency"])
            if product["cheapest_price"] is not None
            else "?"
        )
        line = texts.list_item(name, price_text, arrow, delta_text, store_count=product["store_count"])
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

    is_child = product["parent_product_id"] is not None
    keyboard = _child_detail_keyboard(product_id) if is_child else _detail_keyboard(product_id)

    points = await db.get_price_points(product_id)
    name = product["name"] or texts.UNKNOWN_PRODUCT_NAME

    if len(points) < 2:
        await query.message.reply_html(texts.CHART_NOT_ENOUGH_DATA, reply_markup=keyboard)
    else:
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
            reply_markup=keyboard,
        )

    if not is_child:
        children = await db.get_child_urls(product_id)
        for child in children:
            price_text = (
                texts.format_price(child["last_price"], child["currency"])
                if child["last_price"] is not None
                else "?"
            )
            child_keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(texts.BTN_DETAIL, callback_data=f"detail:{child['id']}"),
                        InlineKeyboardButton(texts.BTN_REMOVE_STORE, callback_data=f"rmurl:{child['id']}"),
                    ]
                ]
            )
            await query.message.reply_html(
                texts.store_list_item(child["domain"], price_text), reply_markup=child_keyboard
            )


async def _resolve_root(product):
    """Given an owned row, return its group root (itself if already a root)."""
    if product is None or product["parent_product_id"] is None:
        return product
    return await db.get_product(product["parent_product_id"])


async def target_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    product = await db.get_owned_product(product_id, update.effective_user.id)
    product = await _resolve_root(product)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return
    context.user_data["awaiting_target_for"] = product["id"]
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
    product = await _resolve_root(product)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return
    root_id = product["id"]
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(texts.BTN_REMOVE_CONFIRM, callback_data=f"remove_yes:{root_id}"),
                InlineKeyboardButton(texts.BTN_REMOVE_CANCEL, callback_data=f"remove_no:{root_id}"),
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
    product = await _resolve_root(product)
    if product is None:
        await query.edit_message_text(texts.PRODUCT_NOT_FOUND)
        return
    await db.deactivate_product(product["id"], update.effective_user.id)
    name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
    await query.edit_message_text(texts.remove_done(name), parse_mode=ParseMode.HTML)


async def remove_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(texts.REMOVE_CANCELLED)
