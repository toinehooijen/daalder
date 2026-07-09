"""Shared async HTTP client for the scraping module.

This is the anti-bot seam described in the README: everything above this
module works against a `FetchResult`, not against httpx or Playwright
directly. A plain httpx request is tried first; when a site blocks it (403,
429, or a JS challenge page), a headless Chromium browser is used as a
fallback for that one request. A residential-proxy backend for even harder
targets is left as a config-only seam (`SCRAPE_PROXY_URL`), not implemented.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from daalder import config

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_browser_unavailable = False

_CHALLENGE_MARKERS = (
    "checking your browser",
    "cf-browser-verification",
    "just a moment",
    "captcha",
    "attention required",
    "access denied",
    "verify you are human",
)

# Headers a real Chrome/124 desktop browser sends on every navigation, to match
# the User-Agent in config.USER_AGENT. Cloudflare-style bot management flags a
# request missing these as inconsistent with the claimed browser.
_BROWSER_EXTRA_HEADERS = {
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "upgrade-insecure-requests": "1",
}

# Patches the handful of headless-Chromium tells (navigator.webdriver, a
# missing window.chrome, empty plugins/languages) that bot-management edges
# like Cloudflare check for before a real JS challenge is even evaluated.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' })),
});
Object.defineProperty(navigator, 'languages', {
    get: () => ['nl-NL', 'nl', 'en-US', 'en'],
});
"""


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


async def init_browser() -> Optional[Browser]:
    """Lazily start Playwright and launch the shared headless Chromium instance.

    Never raises: if launching fails (e.g. browser binaries not installed),
    this logs once, marks the fallback unavailable for the rest of the
    process, and every caller gets `None` instead of retrying a broken
    browser on every blocked fetch.
    """
    global _playwright, _browser, _browser_unavailable
    if _browser_unavailable or not config.ENABLE_BROWSER_FALLBACK:
        return None
    if _browser is not None:
        return _browser

    try:
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
    except Exception:
        logger.exception("Kon Playwright-browser niet starten; browser-fallback uitgeschakeld")
        _browser_unavailable = True
        _browser = None
        if _playwright is not None:
            await _playwright.stop()
            _playwright = None
        return None

    return _browser


async def close_browser() -> None:
    global _playwright, _browser
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


async def _fetch_with_browser(url: str) -> FetchResult:
    browser = await init_browser()
    if browser is None:
        return FetchResult(ok=False, status_code=None, html=None, blocked=True, final_url=url, error="browser_unavailable")

    context_kwargs = {
        "user_agent": config.USER_AGENT,
        "locale": "nl-NL",
        "timezone_id": "Europe/Amsterdam",
        "viewport": {"width": 1920, "height": 1080},
        "extra_http_headers": _BROWSER_EXTRA_HEADERS,
    }
    if config.SCRAPE_PROXY_URL:
        context_kwargs["proxy"] = {"server": config.SCRAPE_PROXY_URL}

    context = await browser.new_context(**context_kwargs)
    await context.add_init_script(_STEALTH_INIT_SCRIPT)
    try:
        page = await context.new_page()
        try:
            response = await page.goto(
                url, timeout=config.BROWSER_TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded"
            )
        except PlaywrightTimeoutError:
            logger.warning("Browser-fetch timeout voor %s", url)
            return FetchResult(ok=False, status_code=None, html=None, blocked=False, final_url=url, error="browser_timeout")

        # Poll for a client-side JS challenge (e.g. Cloudflare) to resolve,
        # instead of blindly waiting a fixed duration.
        deadline = asyncio.get_event_loop().time() + config.BROWSER_CHALLENGE_WAIT_SECONDS
        html = await page.content()
        while _looks_like_challenge(html) and asyncio.get_event_loop().time() < deadline:
            await page.wait_for_timeout(500)
            html = await page.content()

        status_code = response.status if response is not None else None
        final_url = page.url

        blocked = (status_code in (403, 429) if status_code is not None else False) or _looks_like_challenge(html)
        if blocked or (status_code is not None and status_code >= 400):
            return FetchResult(
                ok=False,
                status_code=status_code,
                html=html,
                blocked=blocked,
                final_url=final_url,
                error=f"HTTP {status_code}" if status_code is not None else "blocked",
            )

        return FetchResult(ok=True, status_code=status_code, html=html, blocked=False, final_url=final_url)
    except Exception as exc:
        logger.warning("Browser-fetch mislukt voor %s: %s", url, exc)
        return FetchResult(ok=False, status_code=None, html=None, blocked=False, final_url=url, error=str(exc))
    finally:
        await context.close()


async def fetch(url: str) -> FetchResult:
    client = init_client()
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("Fetch mislukt voor %s: %s", url, exc)
        return FetchResult(ok=False, status_code=None, html=None, blocked=False, final_url=url, error=str(exc))

    blocked = response.status_code in (403, 429) or _looks_like_challenge(response.text)
    if response.status_code >= 400 or blocked:
        httpx_result = FetchResult(
            ok=False,
            status_code=response.status_code,
            html=response.text,
            blocked=blocked,
            final_url=str(response.url),
            error=f"HTTP {response.status_code}",
        )
        if not blocked:
            return httpx_result

        logger.info("httpx-fetch geblokkeerd voor %s (status=%s), val terug op browser", url, response.status_code)
        browser_result = await _fetch_with_browser(url)
        return browser_result if browser_result.ok else httpx_result

    return FetchResult(
        ok=True,
        status_code=response.status_code,
        html=response.text,
        blocked=False,
        final_url=str(response.url),
    )
