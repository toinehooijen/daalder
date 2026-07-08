"""Tier 3: LLM-based price extraction fallback (works on almost any site)."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from anthropic import AsyncAnthropic
from selectolax.parser import HTMLParser

from daalder import config
from daalder.scraping import PriceResult

logger = logging.getLogger(__name__)

_client: Optional[AsyncAnthropic] = None

_SYSTEM_PROMPT = (
    "Je krijgt de tekst van een productpagina. Geef ALLEEN geldige JSON terug, "
    "exact in dit formaat, zonder markdown-codeblokken en zonder extra tekst:\n"
    '{"name": string, "price": number of null, "currency": string, "in_stock": true, false of null}\n'
    '- "price" is de huidige verkoopprijs als getal met een punt als decimaalteken '
    "(bijvoorbeeld 49.95), of null als er geen prijs te vinden is.\n"
    '- "currency" is een ISO-valutacode zoals EUR of USD; gebruik EUR als je het niet zeker weet.\n'
    "Geen uitleg, geen markdown, alleen het JSON-object."
)

_MAX_CHARS = 8000
_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg")


def _client_instance() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        close = getattr(_client, "close", None)
        if close is not None:
            await close()
        _client = None


def _visible_text(html: str) -> str:
    tree = HTMLParser(html)
    for tag in _STRIP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    body = tree.body
    text = body.text(separator=" ", deep=True) if body is not None else tree.text(separator=" ", deep=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_CHARS]


def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


async def extract_with_llm(html: str, url: str) -> PriceResult:
    text = _visible_text(html)
    if not text:
        return PriceResult(ok=False, status="not_found")

    try:
        response = await _client_instance().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"URL: {url}\n\nPaginatekst:\n{text}"}],
        )
    except Exception:
        logger.exception("LLM-extractie faalde voor %s", url)
        return PriceResult(ok=False, status="error", error="llm_call_failed")

    raw = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    raw = _strip_json_fences(raw)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM gaf geen geldige JSON terug voor %s: %r", url, raw[:200])
        return PriceResult(ok=False, status="error", error="invalid_llm_json")

    if not isinstance(data, dict):
        return PriceResult(ok=False, status="error", error="invalid_llm_json")

    price: Optional[Decimal] = None
    price_raw = data.get("price")
    if isinstance(price_raw, (int, float)) and not isinstance(price_raw, bool):
        try:
            price = Decimal(str(price_raw))
        except InvalidOperation:
            price = None

    if price is None:
        return PriceResult(ok=False, status="not_found")

    name = data.get("name")
    currency = data.get("currency") or "EUR"
    in_stock = data.get("in_stock")
    if not isinstance(in_stock, bool):
        in_stock = None

    return PriceResult(
        ok=True,
        status="ok",
        name=str(name).strip() if isinstance(name, str) and name.strip() else None,
        price=price,
        currency=str(currency),
        in_stock=in_stock,
        strategy="llm",
    )
