"""2D rendering of a Rack as a matplotlib Figure."""
from __future__ import annotations

import colorsys
from typing import Iterable

from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from app.models import Rack


def build_color_map(part_nos: Iterable[str]) -> dict[str, str]:
    """Assign a consistent, visually distinct color to each part number."""
    unique = sorted(set(part_nos))
    n = max(len(unique), 1)
    colors: dict[str, str] = {}
    for i, part_no in enumerate(unique):
        hue = (i / n) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.92)
        colors[part_no] = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
    return colors


def render_rack(rack: Rack, color_map: dict[str, str]) -> Figure:
    fig = Figure(figsize=(8, 8 * rack.rack_h / rack.rack_l if rack.rack_l else 8))
    ax = fig.add_subplot(111)

    ax.add_patch(
        Rectangle((0, 0), rack.rack_l, rack.rack_h, fill=False, edgecolor="black", linewidth=2)
    )

    for placed in rack.placed:
        color = color_map.get(placed.part_no, "#cccccc")
        ax.add_patch(
            Rectangle(
                (placed.x, placed.y),
                placed.w,
                placed.h,
                facecolor=color,
                edgecolor="black",
                linewidth=0.8,
            )
        )
        font_size = max(6, min(11, min(placed.w, placed.h) / 20))
        ax.text(
            placed.x + placed.w / 2,
            placed.y + placed.h / 2,
            str(placed.part_no),
            ha="center",
            va="center",
            fontsize=font_size,
            clip_on=True,
        )

    ax.set_xlim(-rack.rack_l * 0.02, rack.rack_l * 1.02)
    ax.set_ylim(-rack.rack_h * 0.02, rack.rack_h * 1.02)
    ax.set_aspect("equal")
    ax.set_title(
        f"Warenträger {rack.index + 1} — {len(rack.placed)} Bauteile, "
        f"{rack.total_weight:.1f} kg"
    )
    ax.set_xlabel("L (mm)")
    ax.set_ylabel("H (mm)")
    fig.tight_layout()
    return fig
