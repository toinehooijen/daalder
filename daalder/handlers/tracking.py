"""Add-by-URL flow, /lijst, product detail, target price, and removal."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from daalder import config, db, texts
from daalder.scraping import extract_price, get_domain
from daalder.scraping.search import find_other_stores_via_search
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
    if config.ENABLE_STORE_DISCOVERY:
        rows.append([InlineKeyboardButton(texts.BTN_FIND_STORES, callback_data=f"findstores:{product_id}")])
    if store_count > 1:
        rows.append(
            [InlineKeyboardButton(texts.BTN_REMOVE_STORE_PROMPT, callback_data=f"rmstore_prompt:{product_id}")]
        )
    rows.append([InlineKeyboardButton(texts.BTN_TARGET, callback_data=f"target:{product_id}")])
    rows.append([InlineKeyboardButton(texts.BTN_REMOVE, callback_data=f"remove:{product_id}")])
    return InlineKeyboardMarkup(rows)


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


async def _attach_store_url(
    product_id: int,
    user_id: int,
    url: str,
    *,
    discovered_via_search: bool = False,
    name_hint: Optional[str] = None,
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Fetch the price for `url` and attach it as a store to `product_id`.

    Shared by the manual "paste a link" flow and the "pick a discovered
    store" flow. Returns (message_text, keyboard) — keyboard is None on any
    failure (dedupe/not-found/fetch failure), so callers show a plain
    message rather than group actions on a row that was never created.

    The whole body is wrapped in one try/except: both call sites show a
    placeholder message before calling this, so any unhandled exception here
    (not just extract_price's) must still resolve to a text the caller can
    show — otherwise the placeholder is left stuck forever.
    """
    try:
        product = await db.get_owned_product(product_id, user_id)
        if product is None:
            return texts.PRODUCT_NOT_FOUND, None

        existing = await db.url_exists_for_user(user_id, url)
        if existing is not None:
            name = texts.escape(existing["name"] or texts.UNKNOWN_PRODUCT_NAME)
            return texts.url_already_tracked(name), None

        try:
            result = await extract_price(url, name_hint=name_hint)
        except Exception:
            logger.exception("extract_price crashte voor %s", url)
            return texts.ADD_FAILED_ERROR, None

        if not result.ok:
            message_text = {
                "blocked": texts.ADD_FAILED_BLOCKED,
                "not_found": texts.ADD_FAILED_NOT_FOUND,
            }.get(result.status, texts.ADD_FAILED_ERROR)
            return message_text, None

        domain = get_domain(url)
        max_discovered = config.STORE_DISCOVERY_MAX_TOTAL if discovered_via_search else None
        child = await db.add_product_url(
            product_id=product_id,
            user_id=user_id,
            url=url,
            domain=domain,
            name=result.name,
            currency=result.currency,
            strategy=result.strategy,
            price=result.price,
            in_stock=result.in_stock,
            discovered_via_search=discovered_via_search,
            max_discovered=max_discovered,
        )
        if child is None:
            # add_product_url returns None both for "root not found/inactive"
            # and (when discovered_via_search) "cap already reached" — the
            # latter is the far more likely cause here since the caller
            # already did an initial cap check before starting this fetch.
            not_found_text = texts.FIND_STORES_MAX_REACHED if discovered_via_search else texts.PRODUCT_NOT_FOUND
            return not_found_text, None

        store_count = await db.count_product_urls(product_id)
        text = texts.url_added(domain, texts.format_price(result.price, result.currency))
        return text, _group_keyboard(product_id, store_count)
    except Exception:
        logger.exception("_attach_store_url crashte voor product %s (%s)", product_id, url)
        return texts.ADD_FAILED_ERROR, None


async def _handle_add_url_to_product(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, url: str
) -> None:
    placeholder = await update.message.reply_html(texts.FETCHING_PLACEHOLDER)
    text, keyboard = await _attach_store_url(product_id, update.effective_user.id, url)
    await placeholder.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


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


async def findstores_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not config.ENABLE_STORE_DISCOVERY:
        return

    product_id = int(query.data.split(":", 1)[1])
    user = update.effective_user
    # Independent reads: run concurrently instead of paying two round trips in series.
    product_row, db_user = await asyncio.gather(
        db.get_owned_product(product_id, user.id), db.get_or_create_user(user.id)
    )
    product = await _resolve_root(product_row)
    if product is None:
        await query.message.reply_html(texts.PRODUCT_NOT_FOUND)
        return

    if config.STORE_DISCOVERY_PLUS_ONLY and db_user["plan"] != "plus":
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(texts.BTN_UPGRADE, callback_data="go_upgrade")]])
        await query.message.reply_html(texts.FIND_STORES_PLUS_ONLY, reply_markup=keyboard)
        return

    root_id = product["id"]
    children = await db.get_child_urls(root_id)
    discovered_count = (1 if product["discovered_via_search"] else 0) + sum(
        1 for child in children if child["discovered_via_search"]
    )
    remaining_slots = config.STORE_DISCOVERY_MAX_TOTAL - discovered_count
    if remaining_slots <= 0:
        await query.message.reply_html(texts.FIND_STORES_MAX_REACHED)
        return

    # Atomic check-and-set: two near-simultaneous taps of this button can't
    # both pass the cooldown and both trigger a paid web-search call.
    claimed = await db.try_claim_store_search(root_id, config.STORE_DISCOVERY_COOLDOWN_HOURS)
    if not claimed:
        await query.message.reply_html(texts.FIND_STORES_COOLDOWN)
        return

    placeholder = await query.message.reply_html(texts.FIND_STORES_SEARCHING)

    exclude_domains = [product["domain"]] + [child["domain"] for child in children]
    max_results = min(config.STORE_DISCOVERY_MAX_CANDIDATES, remaining_slots)

    try:
        search_result = await find_other_stores_via_search(
            product["name"] or texts.UNKNOWN_PRODUCT_NAME, exclude_domains, max_results=max_results
        )
    except Exception:
        logger.exception("Winkel-zoeken crashte voor product %s", root_id)
        search_result = None

    if search_result is None or not search_result.ok:
        is_error = search_result is None or search_result.status == "error"
        text = texts.FIND_STORES_ERROR if is_error else texts.FIND_STORES_NONE
        await placeholder.edit_text(text, parse_mode=ParseMode.HTML)
        return

    candidates = search_result.candidates
    context.user_data.setdefault("store_candidates", {})[root_id] = candidates

    buttons = [
        [
            InlineKeyboardButton(
                texts.store_candidate_button_label(
                    candidate.domain,
                    texts.format_price(candidate.price, candidate.currency) if candidate.price is not None else "?",
                ),
                callback_data=f"pickstore:{root_id}:{idx}",
            )
        ]
        for idx, candidate in enumerate(candidates)
    ]
    buttons.append([InlineKeyboardButton(texts.BTN_REMOVE_CANCEL, callback_data=f"stores_cancel:{root_id}")])
    await placeholder.edit_text(
        texts.find_stores_prompt(len(candidates)),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def pickstore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, product_id_str, idx_str = query.data.split(":")
    product_id = int(product_id_str)
    idx = int(idx_str)

    candidates = context.user_data.get("store_candidates", {}).get(product_id)
    if not candidates or idx >= len(candidates):
        await query.edit_message_text(texts.STORE_CANDIDATE_EXPIRED)
        return

    discovered_count = await db.count_discovered_stores(product_id)
    if discovered_count >= config.STORE_DISCOVERY_MAX_TOTAL:
        context.user_data.get("store_candidates", {}).pop(product_id, None)
        await query.edit_message_text(texts.FIND_STORES_MAX_REACHED)
        return

    candidate = candidates[idx]
    await query.edit_message_text(texts.FETCHING_PLACEHOLDER, parse_mode=ParseMode.HTML)
    text, keyboard = await _attach_store_url(
        product_id,
        update.effective_user.id,
        candidate.url,
        discovered_via_search=True,
        name_hint=candidate.name,
    )
    context.user_data.get("store_candidates", {}).pop(product_id, None)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def stores_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    context.user_data.get("store_candidates", {}).pop(product_id, None)
    await query.edit_message_text(texts.REMOVE_CANCELLED)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.get_or_create_user(user.id)
    products = await db.list_products(user.id)

    if not products:
        await update.message.reply_html(texts.LIST_EMPTY)
        return

    await update.message.reply_html(texts.LIST_INTRO)
    for product in products:
        name = texts.escape(product["name"] or texts.UNKNOWN_PRODUCT_NAME)
        currency = product["currency"]
        start_text = (
            texts.format_price(product["first_price"], currency) if product["first_price"] is not None else "?"
        )
        current_text = (
            texts.format_price(product["cheapest_price"], currency)
            if product["cheapest_price"] is not None
            else "?"
        )
        target_text = (
            texts.format_price(product["target_price"], currency)
            if product["target_price"] is not None
            else texts.TARGET_NOT_SET
        )
        line = texts.list_item(name, start_text, current_text, target_text, store_count=product["store_count"])
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
