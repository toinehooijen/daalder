"""Shared async HTTP client for the scraping module.

This is the anti-bot seam described in the README: everything above this
module works against a `FetchResult`, not against httpx directly. Swapping in
a Playwright + residential-proxy backed fetch for hard targets later means
changing only this file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from daalder import config

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

_CHALLENGE_MARKERS = (
    "checking your browser",
    "cf-browser-verification",
    "just a moment",
    "captcha",
    "attention required",
    "access denied",
    "verify you are human",
)


def init_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=httpx.Timeout(config.HTTP_TIMEOUT_SECONDS),
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@dataclass
class FetchResult:
    ok: bool
    status_code: Optional[int]
    html: Optional[str]
    blocked: bool
    final_url: str
    error: Optional[str] = None


def _looks_like_challenge(html: str) -> bool:
    sample = html[:4000].lower()
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


async def fetch(url: str) -> FetchResult:
    client = init_client()
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("Fetch mislukt voor %s: %s", url, exc)
        return FetchResult(ok=False, status_code=None, html=None, blocked=False, final_url=url, error=str(exc))

    blocked = response.status_code in (403, 429) or _looks_like_challenge(response.text)
    if response.status_code >= 400 or blocked:
        return FetchResult(
            ok=False,
            status_code=response.status_code,
            html=response.text,
            blocked=blocked,
            final_url=str(response.url),
            error=f"HTTP {response.status_code}",
        )

    return FetchResult(
        ok=True,
        status_code=response.status_code,
        html=response.text,
        blocked=False,
        final_url=str(response.url),
    )
