"""Data models for the Warenträger packing tool."""
from __future__ import annotations

from dataclasses import dataclass, field


def part_no_sort_key(part_no: str):
    """Numeric-aware ascending sort key for part numbers ("2" before "10",
    not the lexicographic "10" before "2") — shared by every part_no listing
    across the app (results table, Excel sheets, PPTX overview) so they all
    sort the same way. Falls back to plain string order for any part_no that
    isn't purely numeric."""
    try:
        return (0, int(part_no))
    except (TypeError, ValueError):
        return (1, part_no)


@dataclass
class Part:
    """A part type read from the parts list."""

    part_no: str
    l: float
    b: float
    h: float
    weight: float
    qty_per_machine: int

    @property
    def dims_sorted(self) -> tuple[float, float, float]:
        return tuple(sorted((self.l, self.b, self.h), reverse=True))

    @property
    def dim_a(self) -> float:
        """Larger of the two placement-footprint dimensions."""
        return self.dims_sorted[0]

    @property
    def dim_b(self) -> float:
        """Smaller of the two placement-footprint dimensions."""
        return self.dims_sorted[1]

    @property
    def depth(self) -> float:
        """Smallest original dimension; must fit within the rack's depth B."""
        return self.dims_sorted[2]


@dataclass
class Unit:
    """One physical instance of a part to be placed on a rack."""

    part: Part
    unit_index: int


@dataclass
class PlacedUnit:
    part_no: str
    x: float
    y: float
    w: float
    h: float
    rotated: bool
    weight: float
    depth: float


@dataclass
class Rack:
    index: int
    rack_l: float
    rack_h: float
    rack_b: float
    placed: list[PlacedUnit] = field(default_factory=list)

    @property
    def total_weight(self) -> float:
        return sum(p.weight for p in self.placed)

    def part_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.placed:
            counts[p.part_no] = counts.get(p.part_no, 0) + 1
        return counts

    def left_right_weight(self) -> tuple[float, float]:
        """Total weight left / right of the rack's L-axis midpoint. A part
        that straddles the midpoint (e.g. a single centered part) has its
        weight split proportionally by how much of its width lies on each
        side, rather than being assigned wholesale to one side — otherwise a
        lone, perfectly centered part would register as 100% "right"."""
        mid = self.rack_l / 2
        left = 0.0
        right = 0.0
        for p in self.placed:
            if p.w <= 0:
                continue
            left_overlap = min(p.w, max(0.0, mid - p.x))
            frac_left = left_overlap / p.w
            left += p.weight * frac_left
            right += p.weight * (1 - frac_left)
        return left, right

    def effective_edge_margin_range(self) -> tuple[float, float] | None:
        """(min, max) actual clearance from a placed part's own outer edge to
        the nearest corresponding rack edge (left/right/top/bottom),
        measured directly from this rack's real geometry — not a per-run
        setting. Meaningful mainly with a tilt angle, where this rack's own
        once-per-rack, per-direction tilt margin (see _rack_tilt_margin in
        packing.py) can make left/right differ from top/bottom; with no
        tilt, min and max both equal the plain configured edge margin. None
        if the rack holds nothing."""
        if not self.placed:
            return None
        left = min(p.x for p in self.placed)
        right = self.rack_l - max(p.x + p.w for p in self.placed)
        bottom = min(p.y for p in self.placed)
        top = self.rack_h - max(p.y + p.h for p in self.placed)
        margins = [left, right, top, bottom]
        return min(margins), max(margins)

    def all_edge_margin_details(self) -> list[tuple[str, PlacedUnit, float]]:
        """(side, bordering part, clearance) for all four rack edges
        ('left', 'right', 'bottom', 'top') — same underlying measurement as
        effective_edge_margin_range, but identifying WHICH part borders
        each edge and that edge's own clearance individually, so a caller
        (the PPTX export's dimension-line annotations) can draw all four,
        not just whichever one happens to be largest. Empty list if the
        rack holds nothing."""
        if not self.placed:
            return []
        left_p = min(self.placed, key=lambda p: p.x)
        right_p = max(self.placed, key=lambda p: p.x + p.w)
        bottom_p = min(self.placed, key=lambda p: p.y)
        top_p = max(self.placed, key=lambda p: p.y + p.h)
        return [
            ("left", left_p, left_p.x),
            ("right", right_p, self.rack_l - (right_p.x + right_p.w)),
            ("bottom", bottom_p, bottom_p.y),
            ("top", top_p, self.rack_h - (top_p.y + top_p.h)),
        ]

    def widest_edge_margin_detail(self) -> tuple[str, PlacedUnit, float] | None:
        """Which single rack edge ('left'/'right'/'bottom'/'top') has the
        largest actual clearance to its nearest part, that bordering part,
        and the clearance value itself -- same underlying measurement as
        effective_edge_margin_range's own max, but identifying WHICH edge/
        part achieves it. None if the rack holds nothing."""
        candidates = self.all_edge_margin_details()
        if not candidates:
            return None
        return max(candidates, key=lambda c: c[2])

    def tightest_gap_detail(self) -> tuple[PlacedUnit, PlacedUnit, float, str] | None:
        """The single most closely-spaced neighboring pair on this rack (the
        smallest observed gap among horizontal or vertical neighbors -- same
        adjacency notion as effective_gap_range), returned as (part_a,
        part_b, gap, direction) where direction is 'h' (part_a left of
        part_b) or 'v' (part_a below part_b). Used by the PPTX export to
        draw one representative dimension line showing the "regular"
        (least tilt-inflated) item gap. None if the rack holds fewer than 2
        parts or no adjacent pair qualifies."""
        placed = self.placed
        if len(placed) < 2:
            return None
        eps = 1e-6
        best: tuple[PlacedUnit, PlacedUnit, float, str] | None = None
        for i, p in enumerate(placed):
            for q in placed:
                if q is p:
                    continue
                y_overlap = min(p.y + p.h, q.y + q.h) - max(p.y, q.y)
                if y_overlap > 0.5 * min(p.h, q.h) and q.x >= p.x + p.w - eps:
                    gap = q.x - (p.x + p.w)
                    if gap >= -eps and (best is None or gap < best[2]):
                        best = (p, q, gap, "h")
                x_overlap = min(p.x + p.w, q.x + q.w) - max(p.x, q.x)
                if x_overlap > 0.5 * min(p.w, q.w) and q.y >= p.y + p.h - eps:
                    gap = q.y - (p.y + p.h)
                    if gap >= -eps and (best is None or gap < best[2]):
                        best = (p, q, gap, "v")
        return best

    def effective_gap_range(self) -> tuple[float, float] | None:
        """(min, max) actual gap between a placed part and its nearest
        neighbor (in whichever of the four cardinal directions has one),
        measured directly from this rack's real geometry. A "neighbor" is
        the closest other part overlapping this one by more than half its
        own extent along the perpendicular axis — the same adjacency notion
        as the packing engine's own row/column layout. The item-to-item gap
        itself is unaffected by tilt (see the module docstring in
        packing.py) — min and max both equal the plain configured item gap
        regardless of angle. None if the rack holds fewer than 2 parts
        (nothing to have a gap between)."""
        placed = self.placed
        if len(placed) < 2:
            return None
        eps = 1e-6
        observed: list[float] = []
        for i, p in enumerate(placed):
            best: float | None = None
            for j, q in enumerate(placed):
                if i == j:
                    continue
                y_overlap = min(p.y + p.h, q.y + q.h) - max(p.y, q.y)
                if y_overlap > 0.5 * min(p.h, q.h):
                    if q.x >= p.x + p.w - eps:
                        gap = q.x - (p.x + p.w)
                    elif p.x >= q.x + q.w - eps:
                        gap = p.x - (q.x + q.w)
                    else:
                        gap = None
                    if gap is not None and gap >= -eps and (best is None or gap < best):
                        best = gap
                x_overlap = min(p.x + p.w, q.x + q.w) - max(p.x, q.x)
                if x_overlap > 0.5 * min(p.w, q.w):
                    if q.y >= p.y + p.h - eps:
                        gap = q.y - (p.y + p.h)
                    elif p.y >= q.y + q.h - eps:
                        gap = p.y - (q.y + q.h)
                    else:
                        gap = None
                    if gap is not None and gap >= -eps and (best is None or gap < best):
                        best = gap
            if best is not None:
                observed.append(best)
        if not observed:
            return None
        return min(observed), max(observed)


@dataclass
class Issue:
    severity: str  # 'warning' or 'error'
    message: str
    part_no: str | None = None


@dataclass
class PackingResult:
    racks: list[Rack]
    rack_count_display: int
    issues: list[Issue]
    machines_per_day: int
    pooled_daily: bool

    def total_weight_per_day(self) -> float:
        return sum(r.total_weight for r in self.racks) * (1 if self.pooled_daily else self.machines_per_day)

    def aggregate_parts(self) -> list[tuple[str, int, float, float, float, float, float]]:
        """Per part-type summary across all racks, sorted by part_no: (part_no,
        count, unit_weight, total_weight, dim_l, dim_h, dim_b) — dims are the
        as-placed footprint (including rotation) of the part's first
        occurrence, same convention as the per-rack breakdown."""
        counts: dict[str, int] = {}
        weight_by_part: dict[str, float] = {}
        dims_by_part: dict[str, tuple[float, float, float]] = {}
        for rack in self.racks:
            for p in rack.placed:
                counts[p.part_no] = counts.get(p.part_no, 0) + 1
                weight_by_part[p.part_no] = p.weight
                dims_by_part.setdefault(p.part_no, (p.w, p.h, p.depth))
        rows = []
        for part_no in sorted(counts, key=part_no_sort_key):
            count = counts[part_no]
            unit_weight = weight_by_part.get(part_no, 0.0)
            dim_w, dim_h, dim_depth = dims_by_part.get(part_no, (0.0, 0.0, 0.0))
            rows.append((part_no, count, unit_weight, unit_weight * count, dim_w, dim_h, dim_depth))
        return rows

    def heaviest_rack(self) -> Rack | None:
        """The rack with the highest total weight, or None if there are no
        racks — used for the "schwerster Warenträger" summary shown in the
        GUI and both exports."""
        if not self.racks:
            return None
        return max(self.racks, key=lambda r: r.total_weight)


REIHENFOLGE_LABELS = {
    "frei": "Freie Optimierung",
    "chargenweise": "Chargenweise",
    "listenreihenfolge": "Strikte Listenreihenfolge",
}

# (reihenfolge_modus, maschinentrennung) -> short code used in export filenames.
MODE_ABBREVIATIONS = {
    ("frei", False): "FO",
    ("frei", True): "FOM",
    ("chargenweise", False): "Cf",
    ("chargenweise", True): "CfM",
    ("listenreihenfolge", False): "SL",
    ("listenreihenfolge", True): "SLM",
}


@dataclass
class RunParams:
    """Snapshot of the input parameters a packing run was computed with —
    threaded into the Excel/PowerPoint exports so a result stays traceable
    later without having to remember which settings produced it."""

    rack_l: float
    rack_h: float
    rack_b: float
    item_gap: float
    edge_margin: float
    maschinen_pro_tag: int
    reihenfolge_modus: str
    maschinentrennung: bool
    parts_file: str
    neigungswinkel_grad: float = 0.0

    @property
    def reihenfolge_label(self) -> str:
        return REIHENFOLGE_LABELS.get(self.reihenfolge_modus, self.reihenfolge_modus)

    @property
    def mode_abbreviation(self) -> str:
        return MODE_ABBREVIATIONS.get((self.reihenfolge_modus, self.maschinentrennung), self.reihenfolge_modus)
