"""Environment configuration and tunable constants for Daalder."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Verplichte omgevingsvariabele ontbreekt: {name}")
    return value


def _get_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


# --- required ---------------------------------------------------------------

TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
DATABASE_URL = _require("DATABASE_URL")

# --- LLM fallback ------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# --- payments (Telegram Stars) ------------------------------------------------

MONTHLY_STARS = _get_int("MONTHLY_STARS", 150)
ANNUAL_STARS = _get_int("ANNUAL_STARS", 900)

MONTHLY_PLAN_DAYS = 31  # grace day beyond the 30-day subscription period
ANNUAL_PLAN_DAYS = 365
MONTHLY_SUBSCRIPTION_PERIOD_SECONDS = 2592000  # fixed by Telegram: must be 30 days

# --- admin --------------------------------------------------------------------

ADMIN_USER_ID = _get_int("ADMIN_USER_ID", 0) or None

# --- scheduling -----------------------------------------------------------------

FREE_CHECK_INTERVAL_HOURS = _get_int("FREE_CHECK_INTERVAL_HOURS", 24)
PLUS_CHECK_INTERVAL_HOURS = _get_int("PLUS_CHECK_INTERVAL_HOURS", 4)
SCHEDULER_INTERVAL_MINUTES = _get_int("SCHEDULER_INTERVAL_MINUTES", 30)
LAPSE_CHECK_INTERVAL_HOURS = _get_int("LAPSE_CHECK_INTERVAL_HOURS", 24)
RENEWAL_REMINDER_DAYS_BEFORE = _get_int("RENEWAL_REMINDER_DAYS_BEFORE", 7)

FREE_PRODUCT_LIMIT = 1
FREE_STORE_LIMIT = 2

# Politeness settings for the scheduler: never hammer one domain in a burst.
PER_DOMAIN_MIN_INTERVAL_SECONDS = _get_int("PER_DOMAIN_MIN_INTERVAL_SECONDS", 5)
PER_DOMAIN_JITTER_SECONDS = _get_int("PER_DOMAIN_JITTER_SECONDS", 4)
MAX_CONCURRENT_CHECKS = _get_int("MAX_CONCURRENT_CHECKS", 5)

# --- HTTP fetching --------------------------------------------------------------

HTTP_TIMEOUT_SECONDS = 15.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --- browser fallback (anti-bot seam) --------------------------------------------

ENABLE_BROWSER_FALLBACK = os.environ.get("ENABLE_BROWSER_FALLBACK", "true").lower() not in ("0", "false", "")
BROWSER_TIMEOUT_SECONDS = _get_int("BROWSER_TIMEOUT_SECONDS", 20)
BROWSER_CHALLENGE_WAIT_SECONDS = _get_int("BROWSER_CHALLENGE_WAIT_SECONDS", 8)
SCRAPE_PROXY_URL = os.environ.get("SCRAPE_PROXY_URL", "")

# --- search fallback: Claude web_search tool (Tier 4) -----------------------------

# Used when a site blocks direct scraping entirely (fetch() reports blocked):
# asks Claude to search the web for the current price instead of giving up.
ENABLE_SEARCH_FALLBACK = os.environ.get("ENABLE_SEARCH_FALLBACK", "true").lower() not in ("0", "false", "")
ANTHROPIC_SEARCH_MODEL = os.environ.get("ANTHROPIC_SEARCH_MODEL", ANTHROPIC_MODEL)
# Basic web_search tool variant; works on any current model tier (the newer
# dynamic-filtering dated variant requires an Opus-4.6+/Sonnet-4.6+-class model).
WEB_SEARCH_TOOL_TYPE = os.environ.get("WEB_SEARCH_TOOL_TYPE", "web_search_20250305")
SEARCH_FALLBACK_MAX_USES = _get_int("SEARCH_FALLBACK_MAX_USES", 3)

# --- store discovery: find other stores via search (Daalder Plus perk) -----------

ENABLE_STORE_DISCOVERY = os.environ.get("ENABLE_STORE_DISCOVERY", "true").lower() not in ("0", "false", "")
STORE_DISCOVERY_PLUS_ONLY = os.environ.get("STORE_DISCOVERY_PLUS_ONLY", "true").lower() not in ("0", "false", "")
# Candidates shown per search, and the cumulative cap of stores added via this
# feature per product (manually-pasted stores are unaffected by this cap).
STORE_DISCOVERY_MAX_CANDIDATES = _get_int("STORE_DISCOVERY_MAX_CANDIDATES", 5)
STORE_DISCOVERY_MAX_TOTAL = _get_int("STORE_DISCOVERY_MAX_TOTAL", 5)
STORE_DISCOVERY_COOLDOWN_HOURS = _get_int("STORE_DISCOVERY_COOLDOWN_HOURS", 24)
