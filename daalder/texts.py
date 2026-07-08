"""All Dutch user-facing strings for Daalder, in one place.

Every message the bot sends should be built from a constant or helper
function in this module rather than an inline string in a handler. That
keeps the product fully Dutch today and makes a future translation a
change to one file instead of a codebase-wide hunt.
"""

from __future__ import annotations

import html as _html
from decimal import Decimal
from typing import Optional


def escape(text: str) -> str:
    """Escape a value for safe interpolation into an HTML-parse-mode message."""
    return _html.escape(str(text), quote=False)


def format_price(value: Optional[Decimal], currency: str = "EUR") -> str:
    if value is None:
        return "?"
    symbol = "€" if currency in ("EUR", "") else f"{currency} "
    formatted = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{symbol}{formatted}"


UNKNOWN_PRODUCT_NAME = "dit product"

# --- /start, /help, /over -----------------------------------------------------

WELCOME = (
    "👋 <b>Welkom bij Daalder!</b>\n\n"
    "Ik hou de prijs van producten die je online vindt in de gaten, en stuur je "
    "een berichtje zodra de prijs daalt.\n\n"
    "📎 Plak hieronder een productlink om te beginnen met volgen."
)

HELP = (
    "<b>Zo werkt Daalder</b>\n\n"
    "1. Plak een productlink in dit gesprek.\n"
    "2. Ik zoek de huidige prijs op en onthoud die.\n"
    "3. Zodra ik een prijsdaling zie, krijg je een bericht.\n\n"
    "<b>Gratis</b>: 1 product volgen, elke 24 uur gecontroleerd.\n"
    "<b>Daalder Plus</b> (€2/mnd of €12/jr): onbeperkt producten, elke 4 uur gecontroleerd.\n\n"
    "<b>Commando's</b>\n"
    "/lijst — jouw gevolgde producten\n"
    "/status — jouw abonnement\n"
    "/upgrade — Daalder Plus activeren\n"
    "/paysupport — hulp bij betalingen\n"
    "/over — welke gegevens ik bewaar"
)

OVER = (
    "<b>Over Daalder</b>\n\n"
    "Ik bewaar alleen wat nodig is om prijzen voor je te volgen:\n"
    "• je Telegram-gebruikers-ID\n"
    "• de productlinks die je laat volgen\n"
    "• de prijsgeschiedenis van die producten\n\n"
    "Prijzen worden opgehaald van openbare productpagina's. Ik deel je gegevens niet met derden."
)

NO_URL_HINT = (
    "Ik zag geen productlink in je bericht 🤔 Plak een volledige link "
    "(beginnend met http:// of https://) om een product te volgen."
)

# --- adding a product ----------------------------------------------------------

FETCHING_PLACEHOLDER = "🔎 Bezig met ophalen van de prijs…"


def product_added(name: str, price_text: str) -> str:
    return (
        f"✅ Ik volg nu <b>{name}</b> — {price_text}.\n"
        "Je krijgt een bericht zodra de prijs daalt. 📉"
    )


ADD_FAILED_BLOCKED = (
    "⚠️ Deze site blokkeert automatische bezoekjes, dus ik kon de prijs niet ophalen. "
    "Probeer het later opnieuw, of probeer een andere link."
)
ADD_FAILED_NOT_FOUND = (
    "🤷 Ik kon geen prijs vinden op deze pagina. Klopt de link, en staat de prijs op de "
    "productpagina zelf (niet op een overzichtspagina)?"
)
ADD_FAILED_ERROR = "😕 Er ging iets mis bij het ophalen van deze pagina. Probeer het straks nog eens."

FREE_LIMIT_UPSELL = "Gratis volg je 1 product. Met Daalder Plus volg je er onbeperkt — €2/mnd of €12/jr."

# --- buttons ---------------------------------------------------------------------

BTN_UPGRADE = "⭐️ Upgraden"
BTN_CHART = "📊 Prijsverloop"
BTN_TARGET = "🎯 Doelprijs"
BTN_REMOVE = "🗑 Verwijderen"
BTN_DETAIL = "📊 Bekijken"
BTN_REMOVE_CONFIRM = "🗑 Ja, verwijderen"
BTN_REMOVE_CANCEL = "Annuleren"

# --- /lijst ------------------------------------------------------------------------

LIST_EMPTY = "Je volgt nog geen producten. Plak een productlink om te beginnen! 📎"
LIST_INTRO = "<b>Jouw producten</b> 📦"
LIST_ITEM_BLOCKED = "⚠️ tijdelijk niet te checken"


def list_item(name: str, price_text: str, arrow: str, delta_text: str) -> str:
    delta = f" {arrow} {delta_text}" if arrow else ""
    return f"📦 <b>{name}</b>\n{price_text}{delta}"


# --- product detail / chart -----------------------------------------------------------

CHART_NOT_ENOUGH_DATA = "Nog te weinig data voor een grafiek — kom later terug. 📉"


def detail_caption(name: str, current_price: str, lowest_price: str) -> str:
    return f"<b>{name}</b>\nHuidige prijs: {current_price}\nLaagste prijs ooit: {lowest_price}"


PRODUCT_NOT_FOUND = "Ik kon dit product niet vinden. Misschien is het al verwijderd."

# --- target price ------------------------------------------------------------------------

TARGET_PROMPT = (
    "Stuur me het doelbedrag (bijv. 49,95). Ik laat het weten zodra de prijs op of onder "
    "dit bedrag komt. 🎯"
)
TARGET_INVALID = "Dat herken ik niet als bedrag. Stuur bijvoorbeeld: 49,95"


def target_set(price_text: str) -> str:
    return f"🎯 Doelprijs ingesteld op {price_text}. Ik laat het weten zodra de prijs zo laag is!"


# --- remove ----------------------------------------------------------------------------------


def remove_confirm(name: str) -> str:
    return f"Weet je zeker dat je <b>{name}</b> wilt stoppen met volgen?"


def remove_done(name: str) -> str:
    return f"🗑 Gestopt met het volgen van <b>{name}</b>."


REMOVE_CANCELLED = "Oké, ik blijf dit product volgen."

# --- drop alerts --------------------------------------------------------------------------------


def drop_alert(name: str, old_price: str, new_price: str, url: str) -> str:
    return f"📉 <b>{name}</b> is gedaald!\nWas {old_price} → nu {new_price}\n{url}"


PLAN_LAPSED = (
    "Je Daalder Plus-abonnement is verlopen. Je producten staan nog opgeslagen — "
    "upgrade om ze weer allemaal te volgen. /upgrade"
)

# --- payments --------------------------------------------------------------------------------------

UPGRADE_INTRO = (
    "<b>Daalder Plus</b> ⭐️\n\n"
    "Onbeperkt producten volgen, elke 4 uur gecontroleerd in plaats van elke 24 uur.\n\n"
    "Kies je abonnement:"
)


def upgrade_button_monthly(stars: int) -> str:
    return f"⭐️ Maandelijks — {stars} Stars"


def upgrade_button_annual(stars: int) -> str:
    return f"⭐️ Jaarlijks — {stars} Stars"


INVOICE_TITLE_MONTHLY = "Daalder Plus — Maandelijks"
INVOICE_DESC_MONTHLY = "Onbeperkt producten volgen, elke 4 uur gecontroleerd. Maandelijks opzegbaar."
INVOICE_TITLE_ANNUAL = "Daalder Plus — Jaarlijks"
INVOICE_DESC_ANNUAL = "Onbeperkt producten volgen, elke 4 uur gecontroleerd. Eenmalige betaling voor 12 maanden."

PAYMENT_THANKS_MONTHLY = "⭐️ Bedankt! Je Daalder Plus-abonnement (maandelijks) is actief."
PAYMENT_THANKS_ANNUAL = "⭐️ Bedankt! Je Daalder Plus-abonnement (jaarlijks) is actief."

PAYSUPPORT_TEXT = (
    "<b>Hulp bij betalingen</b>\n\n"
    "Daalder Plus wordt betaald met Telegram Stars. Wil je een betaling terugvragen? "
    "Stuur een bericht naar de beheerder van dit bot-account met je gebruikers-ID en het "
    "moment van betalen — dan wordt de betaling teruggeboekt via Telegrams officiële "
    "terugbetalingsfunctie."
)

REFUND_USAGE = "Gebruik: /refund <telegram_user_id>"
REFUND_NO_USER = "Onbekende gebruiker of geen betaalgeschiedenis gevonden."
REFUND_SUCCESS = "✅ Terugbetaling verwerkt voor gebruiker {user_id}."
REFUND_FAILED = "❌ Terugbetaling mislukt: {error}"
ADMIN_ONLY = "Dit commando is alleen voor de beheerder."


def status_text(plan: str, product_count: int, expires_text: Optional[str], is_recurring: bool) -> str:
    lines = ["<b>Jouw Daalder-status</b>"]
    if plan == "plus":
        lines.append("Abonnement: Daalder Plus ⭐️")
        if is_recurring and expires_text:
            lines.append(f"Verlengt automatisch op {expires_text}.")
        elif expires_text:
            lines.append(f"Actief tot {expires_text}.")
    else:
        lines.append("Abonnement: Gratis")
    lines.append(f"Gevolgde producten: {product_count}")
    if plan != "plus":
        lines.append("\nMeer volgen? /upgrade voor Daalder Plus.")
    return "\n".join(lines)
