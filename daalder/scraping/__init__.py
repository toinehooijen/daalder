"""Price extraction orchestrator: structured data -> per-domain adapter -> LLM.

Anti-bot seam: `fetch()` in `fetch.py` is the single place that talks HTTP to
the outside world. Hard targets (Amazon, Zalando, ...) will block a plain
httpx client. When that day comes, swap the body of `fetch()` for a
Playwright + proxy backed implementation without touching any of the three
extraction tiers below — they only depend on `FetchResult`, not on how the
HTML was obtained.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from urllib.parse import urlparse

from daalder import config

logger = logging.getLogger(__name__)


@dataclass
class PriceResult:
    ok: bool
    status: str  # 'ok' | 'blocked' | 'not_found' | 'error'
    name: Optional[str] = None
    price: Optional[Decimal] = None
    currency: str = "EUR"
    in_stock: Optional[bool] = None
    strategy: Optional[str] = None  # 'structured' | 'adapter' | 'llm' | 'search'
    error: Optional[str] = None


def get_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


async def extract_price(url: str, *, name_hint: Optional[str] = None) -> PriceResult:
    """Try structured data, then a per-domain adapter, then the LLM fallback.

    `name_hint` (an already-known product name, if any) is passed through to
    the Tier 4 search fallback so it has something to search for even when
    the page can't be fetched at all.
    """
    # Imported lazily so this module can define PriceResult/get_domain first;
    # the submodules import those back from here at *their* import time, which
    # only happens on first call, once this package is fully initialized.
    from daalder.scraping.adapters import get_adapter
    from daalder.scraping.fetch import fetch
    from daalder.scraping.llm import extract_with_llm
    from daalder.scraping.search import find_price_via_search
    from daalder.scraping.structured import extract_from_html

    fetched = await fetch(url)
    if not fetched.ok:
        status = "blocked" if fetched.blocked else "error"
        logger.info("Fetch niet ok voor %s: status=%s", url, status)
        if status == "blocked" and config.ENABLE_SEARCH_FALLBACK:
            domain = get_domain(url)
            try:
                search_result = await find_price_via_search(url, domain, name_hint)
            except Exception:
                logger.exception("Zoek-fallback crashte voor %s", url)
                search_result = None
            if search_result is not None and search_result.ok:
                return search_result
        return PriceResult(ok=False, status=status, error=fetched.error)

    domain = get_domain(fetched.final_url or url)

    result = extract_from_html(fetched.html, fetched.final_url)
    if result.ok:
        return result

    adapter = get_adapter(domain)
    if adapter is not None:
        try:
            adapter_result = adapter(fetched.html, fetched.final_url)
        except Exception:
            logger.exception("Adapter voor domein %s faalde", domain)
            adapter_result = None
        if adapter_result is not None and adapter_result.ok:
            return adapter_result

    try:
        return await extract_with_llm(fetched.html, fetched.final_url)
    except Exception:
        logger.exception("LLM-fallback faalde voor %s", url)
        return PriceResult(ok=False, status="error", error="llm_failed")
