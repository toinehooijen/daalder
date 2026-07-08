"""Matplotlib (Agg) price-history chart rendering."""

from __future__ import annotations

import io
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)


def render_group_chart(
    product_name: str, series: Dict[str, List[Tuple[datetime, Decimal]]]
) -> Optional[io.BytesIO]:
    """Render a price-history PNG with one line per store (keyed by domain).

    Stores with a single price point get a lone marker instead of a line.
    Returns None if the combined point count across all stores is < 2.
    """
    total_points = sum(len(points) for points in series.values())
    if total_points < 2:
        return None

    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)

    for domain, points in series.items():
        if not points:
            continue
        dates = [p[0] for p in points]
        prices = [float(p[1]) for p in points]
        if len(points) >= 2:
            line = ax.plot(dates, prices, linewidth=2, label=domain)[0]
            ax.scatter([dates[-1]], [prices[-1]], color=line.get_color(), zorder=5)
        else:
            ax.scatter(dates, prices, zorder=5, label=domain)

    ax.set_title(product_name[:60], fontsize=11)
    ax.set_ylabel("Prijs (€)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m"))
    fig.autofmt_xdate()
    ax.legend(loc="best", fontsize=8, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.margins(x=0.15, y=0.2)
    fig.tight_layout(pad=2.0)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", transparent=True)
    plt.close(fig)
    buffer.seek(0)
    buffer.name = "prijsverloop.png"
    return buffer
