"""Rose Bikes (rosebikes.nl) adapter.

The LLM fallback was misreading rosebikes.nl prices (e.g. reporting
€2.339,00 for a €233,90 item): the price on the product page is split
across sibling elements for styling, and the plain-text extraction fed to
the LLM loses the decimal separator between them.

Verified against a live product page (SRAM Rival AXS 12-speed
achterderailleur, 2026-07-09): the price `<li>` itself carries a
`data-test-price-value` attribute with the unambiguous value, as an
integer number of cents (e.g. "23390" for €233,90). That sidesteps the
split-markup problem entirely.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from selectolax.parser import HTMLParser

from daalder.scraping import PriceResult
from daalder.scraping.adapters import register


def _price_from_cents(node) -> Optional[Decimal]:
    raw = node.attributes.get("data-test-price-value")
    if raw is None or not raw.isdigit():
        return None
    return Decimal(raw) / 100


def extract(html: str, url: str) -> Optional[PriceResult]:
    tree = HTMLParser(html)

    node = tree.css_first('[data-test="price-value-sale"]') or tree.css_first('[data-test="price-rrp"]')
    if node is None:
        return None
    price = _price_from_cents(node)
    if price is None:
        return None

    return PriceResult(
        ok=True,
        status="ok",
        name=None,
        price=price,
        currency="EUR",
        in_stock=None,
        strategy="adapter",
    )


register("rosebikes.nl")(extract)
