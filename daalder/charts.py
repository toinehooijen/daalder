"""Matplotlib (Agg) price-history chart rendering."""

from __future__ import annotations

import io
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)


def render_history_chart(
    product_name: str, points: Sequence[Tuple[datetime, Decimal]]
) -> Optional[io.BytesIO]:
    """Render a price-history PNG. Returns None if there are fewer than 2 points."""
    if len(points) < 2:
        return None

    dates = [p[0] for p in points]
    prices = [float(p[1]) for p in points]

    lowest_index = prices.index(min(prices))
    current_index = len(prices) - 1

    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)
    ax.plot(dates, prices, color="#2E86AB", linewidth=2)
    ax.scatter([dates[current_index]], [prices[current_index]], color="#2E86AB", zorder=5, label="Huidig")
    ax.scatter([dates[lowest_index]], [prices[lowest_index]], color="#E63946", zorder=5, label="Laagste")

    ax.set_title(product_name[:60], fontsize=11)
    ax.set_ylabel("Prijs (€)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m"))
    fig.autofmt_xdate()
    ax.legend(loc="best", fontsize=8, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    buffer.seek(0)
    buffer.name = "prijsverloop.png"
    return buffer
