"""Tier 2: per-domain adapter registry.

An adapter is a plain callable `(html, url) -> PriceResult | None` that knows
how to read the price off one specific shop's HTML. The orchestrator
(`daalder.scraping.extract_price`) consults this registry after structured
data (Tier 1) fails to find a price, and before falling back to the LLM
(Tier 3).

To add a real adapter for a shop:
    1. Fetch a live product page from that shop and inspect its HTML.
    2. Write a small module (see `_template.py` for the pattern) with the
       verified CSS selectors for price / name / stock.
    3. Register it: `register("shop-domain.nl")(your_function)`.

Do not guess selectors for a real shop and register them as if verified —
an unverified adapter can silently return a wrong price. Leave a shop on the
LLM fallback until someone has checked the selectors against live HTML.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from daalder.scraping import PriceResult

logger = logging.getLogger(__name__)

AdapterFunc = Callable[[str, str], Optional[PriceResult]]

_REGISTRY: Dict[str, AdapterFunc] = {}


def register(domain: str) -> Callable[[AdapterFunc], AdapterFunc]:
    def decorator(func: AdapterFunc) -> AdapterFunc:
        _REGISTRY[domain.lower()] = func
        return func

    return decorator


def get_adapter(domain: str) -> Optional[AdapterFunc]:
    return _REGISTRY.get(domain.lower())


from daalder.scraping.adapters import _template  # noqa: E402,F401
