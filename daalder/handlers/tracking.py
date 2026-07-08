"""Add-by-URL flow, /lijst, product detail, target price, and removal."""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from daalder import config, db, texts
from daalder.scraping import extract_price, get_domain
from daalder.sparkline import render_price_sparkline
from daalder.scraping.structured import parse_price_string

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")


def _extract_url(text: str) -> Optional[str]:
    match = _URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(").,!?\"'")


def _group_keyboard(product_id: int, store_count: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(texts.BTN_ADD_STORE, callback_data=f"addurl:{product_id}")]]
    if store_count > 1:
        rows.append(
            [InlineKeyboardButton(texts.BTN_REMOVE_STORE_PROMPT, callback_data=f"rmstore_prompt:{product_id}")]
        )
    rows.append([InlineKeyboardButton(texts.BTN_TARGET, callback_data=f"target:{product_id}")])
    rows.append([InlineKeyboardButton(texts.BTN_REMOVE, callback_data=f"remove:{product_id}")])
    return InlineKeyboardMarkup(rows)


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
        text, parse_mode=ParseMode.HTML, reply_markup=_group_keyboard(product["id"], store_count=1)
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

    store_count = await db.count_product_urls(product_id)
    text = texts.url_added(domain, texts.format_price(result.price, result.currency))
    await placeholder.edit_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_group_keyboard(product_id, store_count)
    )


async def remove_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    url_id = int(query.data.split(":", 1)[1])
    user = update.effective_user
    removed = await db.remove_store(url_id, user.id)
    if removed is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return
    await query.edit_message_text(texts.remove_url_done(removed["domain"]), parse_mode=ParseMode.HTML)


async def remove_store_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    user_id = update.effective_user.id
    product = await db.get_owned_product(product_id, user_id)
    product = await _resolve_root(product)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return

    children = await db.get_child_urls(product["id"])
    stores = [product] + list(children)
    if len(stores) <= 1:
        await query.message.reply_html(texts.remove_store_only_one_hint())
        return

    buttons = [
        [
            InlineKeyboardButton(
                f"🗑 {store['domain']} — {texts.format_price(store['last_price'], store['currency'])}",
                callback_data=f"rmurl:{store['id']}",
            )
        ]
        for store in stores
    ]
    await query.message.reply_html(texts.remove_store_prompt(), reply_markup=InlineKeyboardMarkup(buttons))


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
    product = await _resolve_root(product)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return

    root_id = product["id"]
    name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
    currency = product["currency"]

    children = await db.get_child_urls(root_id)
    stores = [product] + list(children)
    store_lines = [
        (
            store["domain"],
            texts.format_price(store["last_price"], store["currency"])
            if store["last_price"] is not None
            else "?",
        )
        for store in stores
    ]

    prices = await db.get_group_prices(root_id)
    cheapest_text = texts.format_price(prices["cheapest"], currency)
    cheapest_domain = prices["cheapest_domain"] or ""
    average_text = texts.format_price(prices["average"], currency)
    target_text = (
        texts.format_price(product["target_price"], currency)
        if product["target_price"] is not None
        else texts.TARGET_NOT_SET
    )

    points = await db.get_group_price_points(root_id)

    lowest_price = None
    if points:
        lowest_point = min(points, key=lambda p: (p["price"], p["checked_at"]))
        lowest_price = (
            texts.format_price(lowest_point["price"], currency),
            lowest_point["domain"],
            lowest_point["checked_at"].strftime("%d-%m-%Y"),
        )

    sparkline = render_price_sparkline([(point["checked_at"], point["price"]) for point in points])

    text = texts.group_detail_text(
        name,
        store_lines,
        cheapest_text,
        cheapest_domain,
        average_text,
        target_text,
        lowest_price=lowest_price,
        sparkline=sparkline,
    )
    keyboard = _group_keyboard(root_id, store_count=len(stores))

    await query.message.reply_html(text, reply_markup=keyboard)


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

    store_count = await db.count_product_urls(product_id)
    await update.message.reply_html(
        texts.target_set(texts.format_price(price, updated["currency"])),
        reply_markup=_group_keyboard(product_id, store_count),
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
