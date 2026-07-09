"""Tier 4: search-engine price fallback + other-store discovery via Claude's
built-in server-side `web_search` tool.

Used when `fetch()` reports a site as fully blocked (see `fetch.py`) — instead
of giving up, ask Claude to search the web for the current price. The same
tool also powers "find other stores selling this product" (a Daalder Plus
perk), reusing the same client/parsing machinery as `llm.py`'s single-page
extraction, but talking to the live web instead of already-fetched HTML.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from anthropic import AsyncAnthropic

from daalder import config
from daalder.scraping import PriceResult, get_domain
from daalder.scraping.llm import _strip_json_fences

logger = logging.getLogger(__name__)

_client: Optional[AsyncAnthropic] = None

# Server-side tool loops can hit Claude's documented iteration cap and pause
# mid-search (stop_reason="pause_turn"); resume up to this many times.
_MAX_CONTINUATIONS = 3

_PRICE_SYSTEM_PROMPT = (
    "Je krijgt een productlink bij een specifieke webshop. De pagina zelf kon niet "
    "automatisch worden geladen (de winkel blokkeert dit). Gebruik de web_search-tool "
    "om de huidige prijs van dit product te vinden, UITSLUITEND bij deze ene winkel "
    "(niet bij een andere winkel die het product ook verkoopt).\n"
    "Geef als laatste bericht ALLEEN geldige JSON terug, exact in dit formaat, zonder "
    "markdown-codeblokken en zonder extra tekst:\n"
    '{"found": true of false, "name": string of null, "price": number of null, '
    '"currency": string, "in_stock": true, false of null}\n'
    '- "found" is false als je geen betrouwbare, actuele prijs bij DEZE winkel kunt vinden.\n'
    '- "price" is een getal met een punt als decimaalteken (bijvoorbeeld 49.95).\n'
    '- "currency" is een ISO-valutacode zoals EUR of USD; gebruik EUR als je het niet zeker weet.\n'
    "Geen uitleg, geen markdown, alleen het JSON-object."
)


def _stores_system_prompt(max_results: int) -> str:
    return (
        "De gebruiker volgt de prijs van een product. Gebruik de web_search-tool om ANDERE "
        "webwinkels te vinden die EXACT hetzelfde product verkopen (zelfde merk, model, "
        "variant, kleur en uitvoering/bundel — GEEN vergelijkbare of net-niet-identieke "
        "producten). Sla winkels over die al genoemd worden in de lijst met uitgesloten "
        f"domeinen. Geef als laatste bericht ALLEEN een geldige JSON-array terug (max "
        f"{max_results} items), zonder markdown-codeblokken en zonder extra tekst, exact in "
        "dit formaat:\n"
        '[{"domain": string, "url": string, "name": string of null, "price": number of null, '
        '"currency": string}, ...]\n'
        "Geef een lege array [] als je geen andere winkel met exact hetzelfde product kunt "
        "vinden. Geen uitleg, geen markdown, alleen de JSON-array."
    )


@dataclass
class StoreCandidate:
    domain: str
    url: str
    name: Optional[str] = None
    price: Optional[Decimal] = None
    currency: str = "EUR"


@dataclass
class StoreSearchResult:
    ok: bool
    status: str  # 'ok' | 'not_found' | 'error'
    candidates: List[StoreCandidate] = field(default_factory=list)
    error: Optional[str] = None


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


def _extract_last_json_value(raw: str, brackets: tuple = ("{", "}")):
    """Best-effort recovery for the noisier agentic-search output: try a
    straight parse first, then scan from the end for the last balanced
    block of the given bracket kind, since the model may narrate around
    the JSON.

    `brackets` must match the expected top-level shape — ("{", "}") for an
    object, ("[", "]") for an array — so a nested object inside an expected
    array (or vice versa) isn't matched by mistake before the real outer
    value is reached.
    """
    text = _strip_json_fences(raw)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    open_ch, close_ch = brackets
    end = text.rfind(close_ch)
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            if text[i] == close_ch:
                depth += 1
            elif text[i] == open_ch:
                depth -= 1
                if depth == 0:
                    candidate = text[i : end + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        break
        end = text.rfind(close_ch, 0, end)
    return None


def _web_search_tool(
    *, allowed_domains: Optional[List[str]] = None, blocked_domains: Optional[List[str]] = None
) -> Dict:
    tool: Dict = {
        "type": config.WEB_SEARCH_TOOL_TYPE,
        "name": "web_search",
        "max_uses": config.SEARCH_FALLBACK_MAX_USES,
    }
    if allowed_domains:
        tool["allowed_domains"] = allowed_domains
    if blocked_domains:
        tool["blocked_domains"] = blocked_domains
    return tool


async def _run_search(*, system: str, user_content: str, tool: Dict) -> str:
    """Call Claude with the web_search tool, resuming through pause_turn, and
    return the concatenated text of the final response."""
    client = _client_instance()
    messages = [{"role": "user", "content": user_content}]
    response = await client.messages.create(
        model=config.ANTHROPIC_SEARCH_MODEL,
        max_tokens=1024,
        system=system,
        tools=[tool],
        messages=messages,
    )
    continuations = 0
    while response.stop_reason == "pause_turn" and continuations < _MAX_CONTINUATIONS:
        messages = messages + [{"role": "assistant", "content": response.content}]
        response = await client.messages.create(
            model=config.ANTHROPIC_SEARCH_MODEL,
            max_tokens=1024,
            system=system,
            tools=[tool],
            messages=messages,
        )
        continuations += 1
    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")


def _parse_decimal(raw) -> Optional[Decimal]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


async def find_price_via_search(url: str, domain: str, name_hint: Optional[str] = None) -> PriceResult:
    """Ask Claude to search the web for the current price at `domain`, for a
    page that couldn't be fetched directly at all."""
    hint_line = f"Productnaam (indien bekend): {name_hint}\n" if name_hint else ""
    user_content = f"Link: {url}\nWinkel-domein: {domain}\n{hint_line}"
    tool = _web_search_tool(allowed_domains=[domain])

    try:
        raw = await _run_search(system=_PRICE_SYSTEM_PROMPT, user_content=user_content, tool=tool)
    except Exception:
        logger.exception("Zoek-fallback faalde voor %s", url)
        return PriceResult(ok=False, status="error", error="search_call_failed")

    data = _extract_last_json_value(raw)
    if not isinstance(data, dict):
        logger.warning("Zoek-fallback gaf geen geldige JSON terug voor %s: %r", url, raw[:200])
        return PriceResult(ok=False, status="error", error="invalid_search_json")

    if not data.get("found"):
        return PriceResult(ok=False, status="not_found", strategy="search")

    price = _parse_decimal(data.get("price"))
    if price is None:
        return PriceResult(ok=False, status="not_found", strategy="search")

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
        strategy="search",
    )


async def find_other_stores_via_search(
    product_name: str, exclude_domains: List[str], max_results: int = 5
) -> StoreSearchResult:
    """Ask Claude to find other retailers selling the exact same product,
    excluding domains already tracked for it."""
    excluded = sorted({d.strip().lower() for d in exclude_domains if d})
    user_content = (
        f"Product: {product_name}\n"
        f"Uitgesloten domeinen (al gevolgd, niet opnieuw voorstellen): "
        f"{', '.join(excluded) or '(geen)'}\n"
        f"Maximaal aantal winkels: {max_results}"
    )
    tool = _web_search_tool(blocked_domains=excluded or None)

    try:
        raw = await _run_search(
            system=_stores_system_prompt(max_results), user_content=user_content, tool=tool
        )
    except Exception:
        logger.exception("Winkel-zoeken faalde voor product %r", product_name)
        return StoreSearchResult(ok=False, status="error", error="search_call_failed")

    data = _extract_last_json_value(raw, brackets=("[", "]"))
    if not isinstance(data, list):
        logger.warning("Winkel-zoeken gaf geen geldige JSON-array terug: %r", raw[:200])
        return StoreSearchResult(ok=False, status="error", error="invalid_search_json")

    candidates: List[StoreCandidate] = []
    seen_domains = set(excluded)
    for item in data:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        domain = get_domain(url)
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        name = item.get("name")
        candidates.append(
            StoreCandidate(
                domain=domain,
                url=url.strip(),
                name=str(name).strip() if isinstance(name, str) and name.strip() else None,
                price=_parse_decimal(item.get("price")),
                currency=str(item.get("currency") or "EUR"),
            )
        )
        if len(candidates) >= max_results:
            break

    if not candidates:
        return StoreSearchResult(ok=False, status="not_found")

    return StoreSearchResult(ok=True, status="ok", candidates=candidates)
