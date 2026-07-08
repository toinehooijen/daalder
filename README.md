# Daalder

Daalder is a Dutch-language Telegram bot that tracks online product prices and
messages you when the price drops. The bot is the entire product — there is
no web frontend and no separate app.

- Paste a product URL into the chat and Daalder starts tracking it.
- Free: track 1 product, checked every 24h.
- **Daalder Plus**: unlimited products, checked every 4h. €2/month
  (recurring) or €12/year (one-off), paid in Telegram Stars.

Single Python process, polling mode, `python-telegram-bot`'s `JobQueue` for
scheduling. No FastAPI, no Redis, no Celery, no webhook server.

## Project layout

```
daalder/
  bot.py               entry point: builds the Application, registers handlers, starts polling
  config.py            env vars + tunable constants
  db.py                asyncpg pool, idempotent schema init, query helpers
  texts.py             every Dutch user-facing string
  handlers/
    start.py           /start, /help, /over
    tracking.py        add-by-URL, /lijst, product detail, target price, remove
    payments.py        /upgrade, /status, /paysupport, Stars invoices + payment callbacks
  scraping/
    __init__.py        extract_price() orchestrator (tier1 -> tier2 -> tier3)
    fetch.py            shared httpx client — the anti-bot seam (see below)
    structured.py       Tier 1: JSON-LD / OpenGraph / microdata
    adapters/
      __init__.py       Tier 2: per-domain adapter registry
      _template.py      example adapter, selectors intentionally unverified
    llm.py              Tier 3: Claude Haiku fallback
  scheduler.py          JobQueue jobs: price checks + Plus-plan lapse handling
  charts.py             matplotlib (Agg) price-history PNG
requirements.txt
.env.example
Procfile
```

## Environment variables

See `.env.example` for the full list with defaults. Required:

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | PostgreSQL connection string |

Everything else (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `MONTHLY_STARS`,
`ANNUAL_STARS`, `ADMIN_USER_ID`, the check-interval and politeness
constants) has a sane default and can be tuned via env vars.

## Running locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

createdb daalder   # or point DATABASE_URL at any Postgres instance
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, DATABASE_URL, ANTHROPIC_API_KEY, ADMIN_USER_ID

python -m daalder.bot
```

The schema (users / products / price_points) is created automatically on
startup — there's no separate migration step.

### Registering commands with BotFather

Send `/setcommands` to @BotFather and register:

```
start - Begin met Daalder
lijst - Bekijk je gevolgde producten
help - Hoe werkt Daalder
status - Jouw abonnement
upgrade - Daalder Plus activeren
paysupport - Hulp bij betalingen
over - Welke gegevens ik bewaar
```

(`/refund` is an admin-only command and is intentionally not listed here.)

## Deploying to Railway (worker service)

1. Create a new Railway project from this repo.
2. Add a PostgreSQL plugin; Railway injects `DATABASE_URL` automatically.
3. Add the remaining env vars from `.env.example` under the service's
   Variables tab.
4. Railway detects the `Procfile` and runs `python -m daalder.bot` as a
   **worker** (no public port, no HTTP server — this is polling mode, so
   that's correct).
5. Deploy. Watch the logs for `Daalder gestart en verbonden met de
   database.`

There is nothing to expose publicly — a worker service with an outbound
connection to `api.telegram.org` is the entire deployment.

## The scraping pipeline

`daalder.scraping.extract_price(url)` tries three tiers in order and returns
the first success:

1. **Structured data** (`structured.py`) — JSON-LD `Product` objects (with
   `@graph`/array handling), falling back to OpenGraph/meta price tags and
   `itemprop="price"` microdata. Covers most Dutch webshops (bol.com and
   similar) without any per-shop code.
2. **Per-domain adapter** (`adapters/`) — a registry of
   `domain -> (html, url) -> PriceResult | None` callables, consulted when
   Tier 1 finds nothing.
3. **LLM fallback** (`llm.py`) — strips `<script>/<style>/<nav>/<footer>`,
   sends the remaining visible text to Claude Haiku with a strict
   JSON-only prompt, and parses the response defensively. This is what
   makes "paste any URL and it just works" true for shops with no
   structured data.

### Adding a Tier-2 adapter for a real shop

`adapters/_template.py` shows the pattern but registers nothing — the
selectors in it are illustrative, not verified against any real site.
To add one for an actual shop:

1. Fetch a real product page from that shop and inspect the HTML.
2. Copy `_template.py`, replace the placeholder selectors with ones you've
   checked against that live HTML.
3. Register it:
   ```python
   from daalder.scraping.adapters import register
   register("shop-domain.nl")(your_extract_function)
   ```

Never commit an adapter with guessed selectors — a wrong selector can
silently report a wrong price, which is worse than falling through to the
LLM tier.

### The anti-bot seam (not built yet, on purpose)

Hard targets like Amazon or Zalando will block a plain `httpx` client (403,
429, or a JS challenge page). `fetch.py` is the single chokepoint every
extraction tier depends on — it returns a `FetchResult` (ok / blocked /
html / status), and nothing above it knows or cares how the HTML was
obtained. When a Playwright + residential-proxy backend is needed, it's a
change to the body of `fetch()` only. Until then, a blocked fetch sets
`last_check_status='blocked'` on the product and `/lijst` surfaces it as
"⚠️ tijdelijk niet te checken" instead of failing silently.

## Payments: Telegram Stars today, Mollie/iDEA later

Every plan change goes through two functions in
`daalder/handlers/payments.py`:

```python
async def grant_plus(user_id: int, *, days: int, recurring: bool, charge_id: str | None) -> None
async def revoke_plus(user_id: int) -> None
```

The scheduler's lapse job and the admin refund path call `revoke_plus`
instead of touching `db.set_plan` directly, and the Stars payment callback
calls `grant_plus`. Adding a Mollie/iDEAL checkout later means adding a new
entry point (e.g. a webhook-less polling check against Mollie's API, or —
if a small webhook becomes unavoidable — a separate tiny process) that
still ends by calling `grant_plus`/`revoke_plus`. The rest of the bot
(scheduler, `/status`, `/lijst`) never needs to change.

### What was verified before implementing the Stars flow

Telegram's Stars subscription API has changed over time, so before wiring
this up we checked the current behavior rather than assuming:

- `XTR` (Telegram Stars) has no minor currency unit — `LabeledPrice` amounts
  are literal Star counts, not cents.
- `provider_token` must be omitted (empty string) for Stars invoices.
- `subscription_period` (used for the monthly plan) currently only accepts
  one value: `2592000` seconds (30 days). Subscription price is capped at
  2500 Stars — our default of 150 is well under that.
- Refunds go through `Bot.refund_star_payment(user_id, telegram_payment_charge_id)`.
- **There is no push notification to the bot when a user cancels their
  subscription** — the bot only learns via the *absence* of a renewal
  payment. So instead of listening for a "cancelled" event, Daalder relies
  on `plan_expires_at`: a monthly grant sets it 31 days out (one day of
  grace beyond the 30-day billing period); a renewal payment pushes it out
  another 31 days; a daily job (`check_lapsed_plans_job`) demotes anyone
  whose `plan_expires_at` has passed back to `free`. This also covers the
  one-off annual plan, which has no renewal at all.

On lapse, products are never deleted — they stay stored, but only the
single most-recently-added active product per free-tier user gets rechecked
(`db.get_due_products`), and the user gets a win-back message pointing at
`/upgrade`.

## Money and units

All prices are `Decimal`, never `float`, from the database column type
through scraping, formatting, and notification logic. `NUMERIC` columns in
Postgres round-trip to `Decimal` via asyncpg automatically.

## Logging and failure isolation

Every per-product scheduler check is wrapped so a single bad page (bad
HTML, a timeout, an unexpected LLM response) can never take down the
scheduler loop — it's logged with the domain and status, the product's
`last_check_status` is updated, and the loop moves on. Unhandled errors in
handlers go through a PTB error handler that logs and, if `ADMIN_USER_ID`
is set, pings the admin directly in Telegram.

## Out of scope for this phase

- Playwright / residential proxies for hard anti-bot targets (seam is in
  place in `fetch.py`, not implemented).
- Mollie/iDEAL payments (seam is in place via `grant_plus`/`revoke_plus`,
  not implemented — Stars only).
- Any web frontend (the marketing/landing page is a separate static site,
  not part of this repo).
- Multi-language support (`texts.py` centralises every string so this is a
  contained future change; Dutch is the only shipped language).
