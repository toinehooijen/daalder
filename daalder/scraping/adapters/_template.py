"""Template for a Tier-2 (per-domain) adapter.

This file deliberately registers nothing. The CSS selectors below illustrate
the *pattern* only — they are not verified selectors for any real shop.

To use this for an actual shop:
    1. Copy this file to e.g. `voorbeeldwinkel.py`.
    2. Fetch a real product page from that shop and replace the selectors
       below with ones you have checked against that live HTML.
    3. Register the adapter for the shop's domain:

           from daalder.scraping.adapters import register
           register("voorbeeldwinkel.nl")(extract)
"""

from __future__ import annotations

from typing import Optional

from selectolax.parser import HTMLParser

from daalder.scraping import PriceResult
from daalder.scraping.structured import parse_price_string


def extract(html: str, url: str) -> Optional[PriceResult]:
    tree = HTMLParser(html)

    # PLACEHOLDER — replace with the shop's real price selector.
    price_node = tree.css_first(".product-price__value")
    if price_node is None:
        return None
    price = parse_price_string(price_node.text(deep=True))
    if price is None:
        return None

    # PLACEHOLDER — replace with the shop's real title selector.
    name_node = tree.css_first("h1.product-title")
    name = name_node.text(deep=True).strip() if name_node else None

    # PLACEHOLDER — replace with however the shop marks something in stock.
    in_stock_node = tree.css_first(".stock-status--available")
    in_stock = True if in_stock_node else None

    return PriceResult(
        ok=True,
        status="ok",
        name=name,
        price=price,
        currency="EUR",
        in_stock=in_stock,
        strategy="adapter",
    )


# Not registered automatically — see the module docstring above.
# register("voorbeeldwinkel.nl")(extract)
