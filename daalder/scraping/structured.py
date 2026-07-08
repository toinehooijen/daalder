"""Tier 1: structured-data extraction (JSON-LD, OpenGraph/meta, microdata).

Covers most Dutch webshops (e.g. bol.com) that publish schema.org Product
markup.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from selectolax.parser import HTMLParser

from daalder.scraping import PriceResult

logger = logging.getLogger(__name__)


def parse_price_string(raw: Optional[str]) -> Optional[Decimal]:
    """Normalise a price string (comma/dot decimals, currency symbols,
    thousands separators) to a Decimal, or None if it can't be parsed."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = re.sub(r"[^\d.,]", "", text)
    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        integer_part, _, frac = text.rpartition(",")
        if len(frac) == 2:
            text = f"{integer_part.replace(',', '').replace('.', '')}.{frac}"
        else:
            text = text.replace(",", "")
    elif "." in text:
        integer_part, _, frac = text.rpartition(".")
        if len(frac) != 2:
            text = text.replace(".", "")

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


_AVAILABILITY_IN_STOCK = {"instock", "limitedavailability", "presale", "preorder", "onlineonly", "instorestock"}
_AVAILABILITY_OUT_STOCK = {"outofstock", "soldout", "discontinued"}


def _parse_availability(raw: Any) -> Optional[bool]:
    if not raw:
        return None
    value = str(raw).rsplit("/", 1)[-1].strip().lower()
    if value in _AVAILABILITY_IN_STOCK:
        return True
    if value in _AVAILABILITY_OUT_STOCK:
        return False
    return None


def _iter_products(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _iter_products(item)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        yield from _iter_products(node["@graph"])
    type_ = node.get("@type")
    types = type_ if isinstance(type_, list) else [type_]
    if types and any(str(t).lower() == "product" for t in types if t):
        yield node


def _offer_price(offer: dict) -> Optional[Decimal]:
    price_raw = offer.get("price")
    if price_raw is None:
        spec = offer.get("priceSpecification")
        if isinstance(spec, dict):
            price_raw = spec.get("price")
    if price_raw is None:
        price_raw = offer.get("lowPrice")
    return parse_price_string(price_raw)


def _extract_from_jsonld(html: str) -> Optional[PriceResult]:
    tree = HTMLParser(html)
    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text(deep=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for product in _iter_products(data):
            offers = product.get("offers")
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if not isinstance(offer, dict):
                continue
            price = _offer_price(offer)
            if price is None:
                continue
            return PriceResult(
                ok=True,
                status="ok",
                name=product.get("name"),
                price=price,
                currency=offer.get("priceCurrency") or "EUR",
                in_stock=_parse_availability(offer.get("availability")),
                strategy="structured",
            )
    return None


def _extract_from_meta(html: str) -> Optional[PriceResult]:
    tree = HTMLParser(html)

    def meta(*names: str) -> Optional[str]:
        for name in names:
            node = tree.css_first(f'meta[property="{name}"]') or tree.css_first(f'meta[name="{name}"]')
            if node:
                content = node.attributes.get("content")
                if content:
                    return content
        return None

    raw_price = meta("product:price:amount", "og:price:amount")
    if raw_price is None:
        node = tree.css_first('[itemprop="price"]')
        if node:
            raw_price = node.attributes.get("content") or node.text(deep=True)

    price = parse_price_string(raw_price) if raw_price else None
    if price is None:
        return None

    currency = meta("product:price:currency", "og:price:currency") or "EUR"
    title_node = tree.css_first("title")
    name = meta("og:title") or (title_node.text(deep=True) if title_node else None)

    return PriceResult(
        ok=True,
        status="ok",
        name=name.strip() if name else None,
        price=price,
        currency=currency,
        in_stock=None,
        strategy="structured",
    )


def extract_from_html(html: str, url: str) -> PriceResult:
    try:
        result = _extract_from_jsonld(html)
        if result:
            return result
        result = _extract_from_meta(html)
        if result:
            return result
    except Exception:
        logger.exception("Structured extraction faalde voor %s", url)
    return PriceResult(ok=False, status="not_found")
