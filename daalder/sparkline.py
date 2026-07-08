"""Text-based price-history sparkline.

A Unicode block sparkline embedded directly in the message text. Unlike a
rendered chart image, it has no background to get wrong across Telegram's
photo/document handling and light/dark themes — it's just text.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

_BLOCKS = "▁▂▃▄▅▆▇█"
_MAX_POINTS = 24


def render_price_sparkline(points: List[Tuple[datetime, Decimal]]) -> Optional[str]:
    """Render a compact sparkline of the lowest price seen per day.

    Combines price points across all of a group's stores, keeping the
    cheapest price per day. Returns None if there's less than two days of
    history to show a trend for.
    """
    daily: dict = {}
    for checked_at, price in points:
        day = checked_at.date()
        if day not in daily or price < daily[day]:
            daily[day] = price

    days = sorted(daily)
    if len(days) < 2:
        return None

    if len(days) > _MAX_POINTS:
        step = (len(days) - 1) / (_MAX_POINTS - 1)
        days = [days[round(i * step)] for i in range(_MAX_POINTS)]

    prices = [float(daily[day]) for day in days]
    lowest, highest = min(prices), max(prices)
    if highest == lowest:
        return _BLOCKS[0] * len(prices)

    span = highest - lowest
    return "".join(_BLOCKS[round((price - lowest) / span * (len(_BLOCKS) - 1))] for price in prices)
