"""2D shelf-packing of parts onto Warenträger.

Custom shelf-packing algorithm (no external packing library): parts are placed
into horizontal shelf rows, rows are filled top-to-bottom, and each row is
horizontally centered on the rack's L-axis midpoint ("oben Mitte" anchor).

Every instance of a given part type hangs in exactly one fixed orientation
throughout a run (_fixed_rotation_for_part) — a quality requirement, not an
optimization: rotation is no longer chosen per-instance for packing density
or balance. Rectangular parts hang vertically (long side on H) by default; a
handful of parts may override that (PART_ORIENTATION_OVERRIDES). If that
preferred orientation can't even physically fit a fresh, empty rack, the
*other* orientation is used instead — uniformly for the whole part type, not
per instance, so every instance still hangs identically; this is the only
"fallback" involved, and it's resolved once per part type, never
case-by-case. A candidate that doesn't fit its fixed orientation at a
specific spot simply waits for the next row/rack rather than ever rotating
just to fit there. compute_packing still scans the finished result and
would warn if any placed unit's orientation ever disagreed with the rule —
a safety net for the invariant, not a code path expected to fire.

Within a row, parts are grouped into "columns" — a column is a vertical stack
of one or more parts sharing an x-slot, letting a small part fill the unused
headroom above a shorter part instead of forcing its own separate row.
Columns are distributed left/right by descending weight (balanced two-way
partition), except a single strongly weight- and width-dominant part in a
row, which is instead centered across the row on its own (its weight then
splits ~50/50 across the midline automatically) — but this stays a pure
balance nicety, never a placement obstacle: any leftover width on either
side of the centered item is filled with ordinary columns from the supplier
just like a normal row, so a dominant item never blocks other parts out of
its rack.

Minimal rack count is the overriding goal, but never above a mode's own
ordering rule: 'chargenweise' and 'listenreihenfolge' both consume a
strictly head-first queue (_StrictSupplier, which structurally cannot hand
out anything but its own head — see its docstring), so a part's rack
assignment can never depend on anything but that fixed order. 'frei' has no
such constraint. Within that hard limit, before a rack is considered full, a
rescue pass searches for anything still-pending that would fit a denser
repack of the rack so far — the whole remaining pool for 'frei', but only
ever the literal next part for 'chargenweise'/'listenreihenfolge'. After
every rack is provisionally built, a final consolidation pass (only for
'frei'/'chargenweise', never 'listenreihenfolge') looks for under-filled
racks that can be merged into fewer, fuller ones — for 'chargenweise' this
merges only immediately-adjacent racks in place, since collapsing two
consecutive slots can never separate or reorder a batch that was split
across racks, preserving its continuation on the very next rack.

If a fully packed rack still exceeds the ±25% left/right balance tolerance
after all of the above, the item(s) most responsible for the imbalance are
shifted to the next rack as a last resort — this only ever grows the rack
count, never used to justify a less space-saving arrangement earlier on.

A desired paint-line tilt angle (neigungswinkel_grad) never changes any
rectangle's own geometry, and it never changes the gap between two parts
either: two neighboring parts tilted by the same angle shear together, so
their mutual clearance is unaffected by the angle — item_gap stays exactly
as configured everywhere between parts (columns within a row, rows
stacked vertically, items stacked within a column).

The only place tilt adds anything is the rack's own edge, computed
individually for each Warenträger from that rack's own contents — chord
formula for a rectangle rotating about its own geometric center (exact,
not a small-angle approximation): a side of length s sweeps a clearance of
s x tan(angle/2) on each of its two ends. Left/right clearance (per side)
is therefore the tallest placed HEIGHT among this rack's own parts x
tan(angle/2); top/bottom clearance (per side) is the widest placed WIDTH
among this rack's own parts x tan(angle/2) — each checked independently,
and each only applied at all once that rack's own largest relevant
dimension exceeds TILT_MARGIN_MIN_DIMENSION_MM (small parts need no tilt
margin regardless of angle). This is exact per rack, with no artificial
cap.

Computing it per rack instead of once for the whole run means it must be
known *before* a rack's contents are finalized (row/column building needs
the shrunk usable envelope from the very first item placed), yet must
never need to grow afterward — a growing margin could invalidate rows
already built under a wider budget, which earlier prototypes solved by
retroactively trimming, and that trimming measurably fragmented racks and
weakened rack fill. Instead this is guaranteed by construction:
_establish_rack_margin peeks whichever part a fresh rack's supplier would
naturally offer first (largest-remaining-area first for 'frei'; the
strict queue head for 'chargenweise'/'listenreihenfolge') *before*
building anything, computes that rack's margin from it, shrinks the
usable envelope once for the rack's entire build, and activates a hard
per-rack cap on the supplier (see _StrictSupplier/_FreeSupplier) that
rejects any further candidate whose own height or width would exceed what
was just established — so nothing placed afterward can ever require a
larger margin than what the rack already committed to. For 'frei' this
naturally front-loads the largest remaining part into a new rack, exactly
matching "größte zuerst"; for 'chargenweise' the same per-rack peek
applies to whichever batch order is being tried, and a dedicated
"größte Charge zuerst" candidate order (see BATCH_ORDER_FUNCS) gives the
search a heuristic tuned for this. For 'listenreihenfolge' (strict
part-no order, no reordering possible at all), the natural head is
whatever is next on the list regardless of size: a later, larger part
that would violate the cap simply isn't accepted onto the current rack —
deferred to start the next one, exactly the required "kein rückwirkendes
Entfernen, stattdessen Verschieben auf den nächsten Warenträger" behavior,
achieved by the very same generic mechanism (no separate code path
needed).

Once a rack's rows are fully built (including rescue, mode!=frei's
compacting repack, rebalancing and shifting, and the cosmetic symmetry
pass — none of which ever *adds* a new item from outside the rack's
already-fixed unit set, so none of them can ever violate a margin that
was already satisfied by everything on the rack), _rack_tilt_margin
recomputes the same (extra_lr, extra_tb) directly from the rack's actual
final contents — safe to recompute rather than thread through every step,
since by the construction guarantee above it can only ever match or
undercut what was established, never exceed it.

fits_rack and _fixed_rotation_for_part both take a tan_half argument
directly: both check whether a single part, as the sole occupant of a
fresh, empty rack, would fit (including its own worst-case tilt
clearance, using the exact same formula/threshold as the per-rack margin
above) — used both as compute_packing's upfront exclusion filter and as
the worst-case reference _establish_rack_margin's own peek relies on.

Last of all, a purely cosmetic symmetry pass (_try_symmetrize_rack) looks
for part types appearing more than once on an already-finished, already-
balanced rack and tries to re-lay them out as a mirrored group centered on
the midline (equal-width columns, block-centered, so left-right symmetry
falls out of the existing centering logic for free). It only ever operates
within a single already-assigned rack — never moving a unit to a different
rack — so it cannot affect any mode's rack-assignment order, and it keeps
the change only when it needs no more space than before and does not make
the balance deviation worse; otherwise the original, asymmetric-but-valid
layout is kept unchanged. Symmetry here is strictly optional polish, never
allowed to cost a single rack.
"""
from __future__ import annotations

import math
import time
from collections import deque

from app.models import Issue, Part, PackingResult, PlacedUnit, Rack, Unit, part_no_sort_key

_EPS = 1e-6
BALANCE_TOLERANCE = 0.25  # max allowed relative deviation between left/right half weight

# A rack's own tilt-derived edge margin (per direction, see module
# docstring) is only ever applied once that rack's own largest relevant
# dimension (tallest HEIGHT for left/right, widest WIDTH for top/bottom)
# exceeds this threshold — racks whose parts stay under it in both
# directions the whole time need no adjustment and no special packing-order
# handling at all.
TILT_MARGIN_MIN_DIMENSION_MM = 500.0

REIHENFOLGE_MODI = ("frei", "chargenweise", "listenreihenfolge")

# A part is treated as "dominant" (centered on its own row instead of being
# assigned to one side) if it is markedly heavier than a typical part in this
# run AND its wider footprint orientation spans a large share of the rack.
DOMINANT_WEIGHT_MULTIPLIER = 2.0
DOMINANT_WIDTH_FRACTION = 0.45

MAX_REBALANCE_ITERATIONS = 30
MAX_SHIFT_ITERATIONS = 10

# A provisional rack using less than this share of its usable footprint area
# is a consolidation candidate: the final pass (frei/chargenweise only) looks
# for pairs of such racks that can be merged into one.
CONSOLIDATION_FILL_THRESHOLD = 0.5

# Wall-clock cap on how long _consolidate_underfilled_racks may spend
# scanning for merge candidates — a hard guarantee against ever hanging
# regardless of dataset/angle, since consolidation is a density optimization
# and never required for correctness (see its own docstring/comments).
CONSOLIDATION_TIME_BUDGET_SECONDS = 1.0


def _safe_avail_h(usable_h: float, row_heights: list[float], item_gap: float) -> float:
    """Largest height a new row (about to be added to `row_heights`) can be
    given as its own budget while guaranteeing the row stack still fits
    within usable_h, using the plain, constant item_gap between rows."""
    k = len(row_heights)
    return usable_h - sum(row_heights) - k * item_gap


def _tilt_clearance(dim: float, tan_half: float) -> float:
    """One side's worth of tilt-derived edge clearance for a placed
    dimension `dim` (its own height for left/right, its own width for
    top/bottom) — the exact chord-formula result for rotation about the
    part's own geometric center, s x tan(angle/2), applied only once `dim`
    exceeds TILT_MARGIN_MIN_DIMENSION_MM (see module docstring)."""
    if dim <= TILT_MARGIN_MIN_DIMENSION_MM:
        return 0.0
    return dim * tan_half


def fits_rack(
    w: float, h: float, edge_margin: float, rack_l: float, rack_h: float, tan_half: float = 0.0,
) -> bool:
    """Whether a lone w×h footprint fits within the rack's usable area (rack
    dimensions shrunk symmetrically by edge_margin along both L and H), in
    either orientation, with a tilt-derived clearance to all four edges if
    neigungswinkel_grad > 0 — the same exact, per-rack formula used
    everywhere else (see module docstring and _tilt_clearance): a rectangle
    rotated about its own center gains extra *horizontal* bounding-box room
    proportional to its own placed HEIGHT (once that height exceeds
    TILT_MARGIN_MIN_DIMENSION_MM), and extra *vertical* room proportional
    to its own placed WIDTH (same threshold), each reserved on both sides
    (fw + 2 x clearance(fh) <= usable_l, fh + 2 x clearance(fw) <=
    usable_h). This is the worst-case margin a lone part (as the sole
    occupant of its own rack) would need, so the same formula also anchors
    _fixed_rotation_for_part's own worst-case check and
    _establish_rack_margin's per-rack peek."""
    usable_l = rack_l - 2 * edge_margin
    usable_h = rack_h - 2 * edge_margin
    if usable_l <= _EPS or usable_h <= _EPS:
        return False

    def _fits_orientation(fw: float, fh: float) -> bool:
        needed_l = fw + 2 * _tilt_clearance(fh, tan_half)
        needed_h = fh + 2 * _tilt_clearance(fw, tan_half)
        return needed_l <= usable_l + _EPS and needed_h <= usable_h + _EPS

    return _fits_orientation(w, h) or _fits_orientation(h, w)


def rack_tilt_extra_margin(rack: Rack, neigungswinkel_grad: float) -> tuple[float, float]:
    """(extra_lr, extra_tb): the actual per-rack tilt margin this specific
    rack's own contents require (module docstring) — re-derived directly
    from its own placed geometry, not from an intermediate value threaded
    through packing, so a caller outside this module (the GUI, the PPTX
    export) always sees exactly what was actually computed and applied for
    THIS rack specifically, not a run-wide range or someone else's copy of
    the same calculation. (0.0, 0.0) at neigungswinkel_grad <= 0 or for an
    empty rack."""
    if neigungswinkel_grad <= _EPS or not rack.placed:
        return 0.0, 0.0
    tan_half = math.tan(math.radians(neigungswinkel_grad) / 2.0)
    max_h = max(p.h for p in rack.placed)
    max_w = max(p.w for p in rack.placed)
    return _tilt_clearance(max_h, tan_half), _tilt_clearance(max_w, tan_half)


_part_no_sort_key = part_no_sort_key  # local alias: kept for call-site brevity within this module


def _part_sort_key(part: Part):
    return _part_no_sort_key(part.part_no)


def _part_batch_order_key(part: Part):
    """Batches ordered by descending footprint area, largest part first."""
    return (-(part.dim_a * part.dim_b), _part_sort_key(part))


def _part_batch_order_key_area_asc(part: Part):
    """Batches ordered by ascending footprint area, smallest part first."""
    return (part.dim_a * part.dim_b, _part_sort_key(part))


def _part_batch_order_key_weight_desc(part: Part):
    """Batches ordered by descending per-unit weight, heaviest part first."""
    return (-part.weight, _part_sort_key(part))


def _part_batch_order_key_weight_asc(part: Part):
    """Batches ordered by ascending per-unit weight, lightest part first."""
    return (part.weight, _part_sort_key(part))


def _part_batch_order_key_height_desc(part: Part):
    """Batches ordered by descending dim_b (the shorter placement dimension,
    i.e. what typically ends up as row height), tallest-in-that-sense first."""
    return (-part.dim_b, _part_sort_key(part))


def _part_batch_order_key_height_asc(part: Part):
    return (part.dim_b, _part_sort_key(part))


def _part_batch_order_key_size_desc(part: Part):
    """Batches ordered by descending largest single dimension (max(dim_a,
    dim_b)), biggest part first — added specifically so a rack's own
    tilt-derived margin (see module docstring/_establish_rack_margin) gets
    established from the largest part a batch order can offer as early as
    possible, giving the search a heuristic tuned for that rather than
    relying on area/weight/height orderings to happen to do the same."""
    return (-max(part.dim_a, part.dim_b), _part_sort_key(part))


def _sort_by(key_fn):
    return lambda parts: sorted(parts, key=key_fn)


def _interleave_by_area(parts: list[Part]) -> list[Part]:
    """Alternating large/small batch order (largest, smallest, 2nd-largest,
    2nd-smallest, ...) — a simple best-fit-flavored heuristic: a big batch
    is immediately followed by a small one that might tuck into its leftover
    space, rather than several similarly large batches competing for room
    back to back."""
    ordered = sorted(parts, key=_part_batch_order_key)
    result: list[Part] = []
    lo, hi = 0, len(ordered) - 1
    take_front = True
    while lo <= hi:
        if take_front:
            result.append(ordered[lo])
            lo += 1
        else:
            result.append(ordered[hi])
            hi -= 1
        take_front = not take_front
    return result


# 'chargenweise' tries every one of these candidate batch orderings (see
# compute_packing) and keeps whichever needs fewest racks — since batch order
# is explicitly free to choose for that mode, a single fixed heuristic (e.g.
# always area-descending) cannot be assumed to dominate; in particular
# 'part_no' order must always be included, since it is itself a valid batch
# ordering, which is what *guarantees* 'chargenweise' can never need more
# racks than 'listenreihenfolge' (which is pinned to exactly that order).
# Each entry maps a name to a function parts -> ordered parts (plain sorts
# are wrapped via _sort_by; 'mixed_bestfit' is a genuine rearrangement, not
# expressible as a per-item sort key).
BATCH_ORDER_FUNCS: dict[str, object] = {
    "part_no": _sort_by(_part_sort_key),
    "area_desc": _sort_by(_part_batch_order_key),
    "area_asc": _sort_by(_part_batch_order_key_area_asc),
    "weight_desc": _sort_by(_part_batch_order_key_weight_desc),
    "weight_asc": _sort_by(_part_batch_order_key_weight_asc),
    "height_desc": _sort_by(_part_batch_order_key_height_desc),
    "height_asc": _sort_by(_part_batch_order_key_height_asc),
    "mixed_bestfit": _interleave_by_area,
    "size_desc": _sort_by(_part_batch_order_key_size_desc),
}
CHARGENWEISE_CANDIDATE_ORDERS = (
    "area_desc", "weight_desc", "area_asc", "weight_asc",
    "height_desc", "height_asc", "mixed_bestfit", "part_no", "size_desc",
)

# Local search (batch-pair-swap hill-climbing) on top of the best heuristic
# candidate. A *wall-clock* time budget (the original design) spends
# whatever swap evaluations fit in the time available — but for
# 'chargenweise ohne Maschinentrennung', each evaluation packs the full
# pooled (machines_per_day x) quantity, which costs measurably more per
# evaluation than the single-machine quantity's own local search (measured
# on the real ~63-part-type list: ~17 evaluations complete for the pooled
# search within 0.4s, vs. ~39 for the single-machine one) — so the exact
# case with the most to gain from a thorough search (the larger, pooled
# problem) was systematically searched *less* thoroughly, purely because
# each of its evaluations takes longer, not because it needs less search.
# A fixed evaluation COUNT instead guarantees the same amount of search
# effort regardless of how expensive each individual evaluation is —
# the batch *type* count (what actually determines how many distinct
# swaps exist to try) is identical for single-machine and pooled quantities
# anyway, only the per-type quantity differs. CHARGENWEISE_LOCAL_SEARCH_
# SAFETY_TIME_BUDGET_SECONDS remains as a wall-clock backstop only, to
# bound worst-case runtime on a pathological input; it is deliberately set
# high enough that it should never bind in normal use.
#
# MIN_EVAL_COUNT chosen empirically (runtime/quality sweep during
# development, sweep script not included in this repository): tested at
# 30/50/75/100/150/400/800 on a representative list of ~60 part types, 50
# was the clear sweet spot: the only value that meaningfully reduced how often the tiling
# fallback still won (fired in 4 of 16 representative runs, vs. 6 of 16
# both below and above this value), at a moderate ~3.9s per chargenweise-
# ohne-Maschinentrennung search (up from ~1.4s at the old 0.4s time
# budget). Pushing higher (100, 150, 400...) cost proportionally more time
# without reliably improving the fire rate further — the single-machine
# floor being compared against gets more thorough too, so extra search
# effort beyond this point mostly cancels out rather than closing the gap.
CHARGENWEISE_LOCAL_SEARCH_MIN_EVAL_COUNT = 50
CHARGENWEISE_LOCAL_SEARCH_SAFETY_TIME_BUDGET_SECONDS = 8.0


def _rank_key_fn(ordered_parts: list[Part]):
    rank = {p.part_no: i for i, p in enumerate(ordered_parts)}
    return lambda part: rank[part.part_no]


def _expand_units_from_ordered_parts(ordered_parts: list[Part], machines_per_day: int, pool_daily: bool) -> list[Unit]:
    units: list[Unit] = []
    for part in ordered_parts:
        qty = part.qty_per_machine * (machines_per_day if pool_daily else 1)
        for i in range(qty):
            units.append(Unit(part=part, unit_index=i))
    return units


def expand_units(
    parts: list[Part], machines_per_day: int, pool_daily: bool, batch_order: str = "part_no"
) -> list[Unit]:
    """Expand parts into individual placeable units, batches (all units of
    one part) ordered according to `batch_order` (a key in BATCH_ORDER_FUNCS),
    each batch's own units always kept contiguous."""
    order_func = BATCH_ORDER_FUNCS.get(batch_order, BATCH_ORDER_FUNCS["part_no"])
    return _expand_units_from_ordered_parts(order_func(parts), machines_per_day, pool_daily)


def _local_search_batch_swaps(
    ordered_parts: list[Part],
    best_count: int,
    machines_per_day: int,
    pool_daily: bool,
    rack_l: float,
    rack_h: float,
    rack_b: float,
    edge_margin: float,
    item_gap: float,
    mode: str,
    min_eval_count: int,
    safety_time_budget_seconds: float,
    tan_half: float = 0.0,
):
    """First-improvement hill-climbing over batch order: try swapping two
    batches' positions, keep the swap if it actually reduces the rack count,
    restart scanning from the new order, until a full scan finds nothing
    better, or min_eval_count evaluations have been spent (checked before
    every single evaluation, not just per-pass, since one full pass over all
    pairs can itself take far more than min_eval_count evaluations for large
    batch counts). safety_time_budget_seconds is a wall-clock backstop only
    — evaluated alongside min_eval_count so a pathologically expensive input
    still can't run away unbounded, but with min_eval_count set well below
    what that budget can realistically exhaust for any realistic problem
    size, it should never be what actually stops this loop in practice; the
    guaranteed, predictable amount of search effort is the evaluation count,
    not wall-clock time, precisely so a more expensive-per-evaluation
    quantity (pooled vs. single-machine) is never searched less thoroughly
    just because each of its evaluations costs more. Mirrors the existing
    balance swap mechanism, just applied to batch order instead of weight."""
    current_order = list(ordered_parts)
    current_count = best_count
    evals_used = 0
    start = time.monotonic()
    improved = True
    while improved:
        improved = False
        n = len(current_order)
        for i in range(n):
            if evals_used >= min_eval_count or (time.monotonic() - start) >= safety_time_budget_seconds:
                return current_order, current_count
            for j in range(i + 1, n):
                if evals_used >= min_eval_count or (time.monotonic() - start) >= safety_time_budget_seconds:
                    return current_order, current_count
                trial = list(current_order)
                trial[i], trial[j] = trial[j], trial[i]
                units = _expand_units_from_ordered_parts(trial, machines_per_day, pool_daily)
                key_fn = _rank_key_fn(trial)
                racks = pack_shelf(
                    units, rack_l, rack_h, rack_b, edge_margin, item_gap, mode,
                    order_key_fn=key_fn, apply_symmetry=False, tan_half=tan_half,
                )
                evals_used += 1
                if len(racks) < current_count:
                    current_order = trial
                    current_count = len(racks)
                    improved = True
                    break
            if improved:
                break
    return current_order, current_count


# Optional per part orientation overrides. Maps a part number (as it appears
# in the parts list) to the fixed orientation that every instance of that part
# must use: True = long side on H (hanging vertically), False = long side on L
# (lying horizontally). Empty by default, so the default rule applies to every
# part (rectangular parts hang vertically). Example: {"7": False}
PART_ORIENTATION_OVERRIDES: dict[str, bool] = {}


def _default_rotation_for_part(part: Part) -> bool:
    """The orientation every instance of this part type is *meant* to use
    (True = long side on H/vertical, False = long side on L/horizontal),
    before checking whether that's even physically possible on a fresh,
    empty rack — see _fixed_rotation_for_part, which applies that check and
    is what placement code should actually use. Default rule: rectangular
    (non-square) parts hang vertically (the longer side, dim_a, goes on H);
    square parts have no meaningful orientation, so False is just a
    deterministic pick. See PART_ORIENTATION_OVERRIDES for the few parts
    that deviate from the default rule entirely."""
    override = PART_ORIENTATION_OVERRIDES.get(part.part_no)
    if override is not None:
        return override
    if abs(part.dim_a - part.dim_b) < _EPS:
        return False
    return True


def _fixed_rotation_for_part(part: Part, usable_w: float, usable_h: float, tan_half: float = 0.0) -> bool:
    """The one orientation every instance of this part type must use
    throughout a run — quality requirement: identical parts must always
    hang the same way, never chosen per-instance for packing/balance
    convenience anymore.

    Starts from _default_rotation_for_part, but if that preferred
    orientation can't even physically fit a fresh, empty rack's usable
    envelope — including its own tilt-derived edge clearance as the sole
    (and therefore rack-largest) occupant, using the *exact same*
    per-orientation formula as fits_rack (upstream fits_rack already
    guarantees *some* orientation satisfies it, just maybe not the
    preferred one) — the *other* orientation is used instead, uniformly,
    for every instance of that part type, not just the ones that happen to
    need it. That's a permanent, structural fact about the part's size
    relative to the rack, resolved once and the same for all instances —
    not a per-instance deviation, so it needs no warning.

    Matching fits_rack's formula exactly here (not just fits_rack's own
    either/or *result*) matters: if this used a plain, tilt-unaware check
    instead, a part could pass fits_rack via one orientation yet actually
    get fixed to the *other* one here, which might not satisfy the
    tilt-aware fit at all — permanently stranding that part type (it would
    never fit anywhere, no matter how many racks/rows are tried, since its
    fixed orientation is wrong for every one of them).

    Because this always resolves to an orientation that fits a completely
    empty rack (tilt margin included), _choose_orientation/
    _choose_orientation_bounded never need a per-instance fallback either: a
    candidate that doesn't fit *here* simply isn't placed here (normal "try
    the next row/rack" flow), and a fresh rack's first row is guaranteed to
    accept it. compute_packing still scans the finished result and would
    warn if any placed unit's orientation ever disagreed with this
    function — a safety net for the invariant, not a code path expected to
    ever fire."""
    preferred = _default_rotation_for_part(part)
    h_if_preferred, w_if_preferred = (part.dim_a, part.dim_b) if preferred else (part.dim_b, part.dim_a)
    needed_w = w_if_preferred + 2 * _tilt_clearance(h_if_preferred, tan_half)
    needed_h = h_if_preferred + 2 * _tilt_clearance(w_if_preferred, tan_half)
    if needed_w <= usable_w + _EPS and needed_h <= usable_h + _EPS:
        return preferred
    return not preferred


def _is_dominant(part: Part, usable_w: float, usable_h: float, avg_unit_weight: float) -> bool:
    if avg_unit_weight <= _EPS:
        return False
    if part.weight < DOMINANT_WEIGHT_MULTIPLIER * avg_unit_weight:
        return False
    fixed_rotation = _fixed_rotation_for_part(part, usable_w, usable_h)
    placed_width = part.dim_b if fixed_rotation else part.dim_a
    if placed_width < DOMINANT_WIDTH_FRACTION * usable_w:
        return False
    return True


def _is_wide_anchor(part: Part, usable_w: float, usable_h: float) -> bool:
    """A part whose *fixed* orientation (see _fixed_rotation_for_part) alone
    spans a large share of the rack, regardless of weight — a *candidate*
    for the same centered/flanked treatment as a dominant part gets, purely
    to prefer a symmetric layout over an incidentally-asymmetric one (see
    _build_rack_rows). Unlike _is_dominant this is never used to
    unconditionally force centering: it only ever wins the comparison in
    _build_rack_rows when doing so places at least as many units as the
    normal row build would have, so it can never cost rack space — 'ohne
    Verlust an Flächennutzung' is enforced by that comparison, not by this
    (deliberately looser) predicate alone."""
    placed_width = part.dim_b if _fixed_rotation_for_part(part, usable_w, usable_h) else part.dim_a
    return placed_width >= DOMINANT_WIDTH_FRACTION * usable_w


def _choose_orientation(part: Part, row_height: float | None, avail_h: float, fixed_rotation: bool):
    """Pick (w, h, rotated) for `part` under the given height limit
    (row_height once a row is established, else the row's available height
    budget) using *only* the part type's fixed orientation (see
    _fixed_rotation_for_part) — every instance of a part must hang the same
    way. Returns None if it doesn't fit here — that's not a deviation, it's
    the normal "doesn't fit this spot, try the next row/rack" flow, and
    _fixed_rotation_for_part already guarantees the fixed orientation fits
    *some* fresh, empty rack, so that flow always terminates somewhere. No
    per-instance fallback to the other orientation here (that would let two
    instances of the same part end up oriented differently, which is exactly
    what this rule exists to prevent) — see the module docstring for the one
    place a deviation actually is allowed."""
    limit = avail_h if row_height is None else row_height
    w, h = (part.dim_b, part.dim_a) if fixed_rotation else (part.dim_a, part.dim_b)
    if h <= limit + _EPS:
        return w, h, fixed_rotation
    return None


def _choose_orientation_bounded(part: Part, max_height: float, max_width: float, fixed_rotation: bool):
    """Like _choose_orientation, but also bounded by a max width (used when
    trying to stack a part into an existing column's established width)."""
    w, h = (part.dim_b, part.dim_a) if fixed_rotation else (part.dim_a, part.dim_b)
    if h <= max_height + _EPS and w <= max_width + _EPS:
        return w, h, fixed_rotation
    return None


class _StrictSupplier:
    """Consumes a pre-ordered unit queue strictly head-first: never skips
    ahead to a later unit, matching 'chargenweise'/'listenreihenfolge'.

    usable_w/avg_unit_weight (if given) make every peek_* method refuse a
    dominant part — dominant parts must only ever be handed out via
    find_dominant()/_build_dominant_row(), never picked up as an ordinary
    column inside _build_row()'s own item-pulling loop. Without this, a
    dominant part queued right after a non-dominant one could slip into the
    non-dominant row as a regular side column instead of getting its own
    centered row, which is exactly the placement that caused avoidable
    imbalance (and thus avoidable rack splitting) in practice.

    tan_half is stored only for _fixed_rotation_for_part's own worst-case,
    lone-part-on-a-fresh-rack orientation check — everything else this
    supplier does (dominance, wide-anchor, actual row placement) works
    against the plain, nominal usable_w/usable_h (the rack's own
    tilt-derived margin already shrunk those before they were ever passed
    in here — see _establish_rack_margin).

    set_rack_cap()/clear_rack_cap() implement the per-rack construction-
    order guarantee (module docstring): once a rack's own margin has been
    established from its first (natural) candidate, every peek_*/find_*
    method below refuses anything whose own placed height or width would
    exceed that cap — so nothing accepted afterward can ever need a larger
    margin than what the rack already committed to."""

    def __init__(
        self, units: list[Unit], usable_w: float | None = None, avg_unit_weight: float | None = None,
        usable_h: float | None = None, tan_half: float = 0.0,
    ):
        self._queue: deque[Unit] = deque(units)
        self._usable_w = usable_w
        self._avg_unit_weight = avg_unit_weight
        self._usable_h = usable_h
        self._tan_half = tan_half
        self._cap_h: float | None = None
        self._cap_w: float | None = None

    def _dominant_blocked(self, part: Part) -> bool:
        if self._usable_w is None or self._avg_unit_weight is None or self._usable_h is None:
            return False
        return _is_dominant(part, self._usable_w, self._usable_h, self._avg_unit_weight)

    def _fixed_rotation(self, part: Part) -> bool:
        return _fixed_rotation_for_part(part, self._usable_w, self._usable_h, self._tan_half)

    def fixed_rotation_for(self, part: Part) -> bool:
        return self._fixed_rotation(part)

    def set_rack_cap(self, cap_h: float, cap_w: float) -> None:
        self._cap_h = cap_h
        self._cap_w = cap_w

    def clear_rack_cap(self) -> None:
        self._cap_h = None
        self._cap_w = None

    def _cap_blocked(self, w: float, h: float) -> bool:
        if self._cap_h is None:
            return False
        return h > self._cap_h + _EPS or w > self._cap_w + _EPS

    def has_remaining(self) -> bool:
        return bool(self._queue)

    def remaining_count(self) -> int:
        return len(self._queue)

    def peek_head_part(self) -> Part | None:
        return self._queue[0].part if self._queue else None

    def peek_fitting(self, row_height, avail_h, remaining_width, gap_needed):
        if not self._queue:
            return None
        unit = self._queue[0]
        if self._dominant_blocked(unit.part):
            return None
        orientation = _choose_orientation(unit.part, row_height, avail_h, self._fixed_rotation(unit.part))
        if orientation is None:
            return None
        w, h, rotated = orientation
        if self._cap_blocked(w, h):
            return None
        if w + gap_needed > remaining_width + _EPS:
            return None
        return unit, w, h, rotated

    def peek_fitting_in_slot(self, max_height, max_width):
        if not self._queue:
            return None
        unit = self._queue[0]
        if self._dominant_blocked(unit.part):
            return None
        orientation = _choose_orientation_bounded(unit.part, max_height, max_width, self._fixed_rotation(unit.part))
        if orientation is None:
            return None
        w, h, rotated = orientation
        if self._cap_blocked(w, h):
            return None
        return unit, w, h, rotated

    def peek_specific_fitting_in_slot(self, part_no, max_height, max_width):
        if not self._queue or self._queue[0].part.part_no != part_no:
            return None
        return self.peek_fitting_in_slot(max_height, max_width)

    def find_dominant(self, usable_w, usable_h, avg_unit_weight):
        if not self._queue:
            return None
        unit = self._queue[0]
        if not _is_dominant(unit.part, usable_w, usable_h, avg_unit_weight):
            return None
        fixed_rotation = self._fixed_rotation(unit.part)
        placed_w = unit.part.dim_b if fixed_rotation else unit.part.dim_a
        placed_h = unit.part.dim_a if fixed_rotation else unit.part.dim_b
        if self._cap_blocked(placed_w, placed_h):
            return None
        return unit

    def find_wide_anchor(self, usable_w, usable_h):
        if not self._queue:
            return None
        unit = self._queue[0]
        if not _is_wide_anchor(unit.part, usable_w, usable_h):
            return None
        fixed_rotation = self._fixed_rotation(unit.part)
        placed_w = unit.part.dim_b if fixed_rotation else unit.part.dim_a
        placed_h = unit.part.dim_a if fixed_rotation else unit.part.dim_b
        if self._cap_blocked(placed_w, placed_h):
            return None
        return unit

    def consume(self, unit: Unit) -> None:
        self._queue.popleft()

    def push_back(self, unit: Unit) -> None:
        self._queue.appendleft(unit)

    # Deliberately no peek_candidates()/consume_specific(): 'chargenweise'
    # and 'listenreihenfolge' both consume this queue strictly head-first —
    # a part's assigned rack must never depend on anything other than that
    # fixed order. Only ever popping self._queue[0] (via consume() above,
    # never an arbitrary later element) is what makes "no part ever ends up
    # on an earlier rack than the strict/batch order requires" a structural
    # guarantee rather than a convention several call sites have to uphold.


class _FreeSupplier:
    """Freely picks whichever remaining part (largest footprint area first)
    fits next, regardless of part ordering — used for 'frei' mode.

    usable_w/avg_unit_weight (if given) make every peek_* method skip over
    dominant parts entirely (not just refuse the first candidate found) and
    keep scanning for the next viable non-dominant one — dominant parts must
    only ever be handed out via find_dominant()/_build_dominant_row().

    tan_half is stored only for _fixed_rotation_for_part's own worst-case
    check — see _StrictSupplier's docstring, which also covers
    set_rack_cap()/clear_rack_cap()."""

    def __init__(
        self, units: list[Unit], usable_w: float | None = None, avg_unit_weight: float | None = None,
        usable_h: float | None = None, tan_half: float = 0.0,
    ):
        counts: dict[str, int] = {}
        parts_by_no: dict[str, Part] = {}
        for u in units:
            counts[u.part.part_no] = counts.get(u.part.part_no, 0) + 1
            parts_by_no[u.part.part_no] = u.part
        self._counts = counts
        self._parts = sorted(parts_by_no.values(), key=lambda p: (-(p.dim_a * p.dim_b), _part_sort_key(p)))
        self._usable_w = usable_w
        self._avg_unit_weight = avg_unit_weight
        self._usable_h = usable_h
        self._tan_half = tan_half
        self._cap_h: float | None = None
        self._cap_w: float | None = None

    def _dominant_blocked(self, part: Part) -> bool:
        if self._usable_w is None or self._avg_unit_weight is None or self._usable_h is None:
            return False
        return _is_dominant(part, self._usable_w, self._usable_h, self._avg_unit_weight)

    def _fixed_rotation(self, part: Part) -> bool:
        return _fixed_rotation_for_part(part, self._usable_w, self._usable_h, self._tan_half)

    def fixed_rotation_for(self, part: Part) -> bool:
        return self._fixed_rotation(part)

    def set_rack_cap(self, cap_h: float, cap_w: float) -> None:
        self._cap_h = cap_h
        self._cap_w = cap_w

    def clear_rack_cap(self) -> None:
        self._cap_h = None
        self._cap_w = None

    def _cap_blocked(self, w: float, h: float) -> bool:
        if self._cap_h is None:
            return False
        return h > self._cap_h + _EPS or w > self._cap_w + _EPS

    def has_remaining(self) -> bool:
        return any(c > 0 for c in self._counts.values())

    def remaining_count(self) -> int:
        return sum(c for c in self._counts.values() if c > 0)

    def peek_head_part(self) -> Part | None:
        for part in self._parts:
            if self._counts.get(part.part_no, 0) > 0:
                return part
        return None

    def peek_fitting(self, row_height, avail_h, remaining_width, gap_needed):
        for part in self._parts:
            if self._counts.get(part.part_no, 0) <= 0 or self._dominant_blocked(part):
                continue
            orientation = _choose_orientation(part, row_height, avail_h, self._fixed_rotation(part))
            if orientation is None:
                continue
            w, h, rotated = orientation
            if self._cap_blocked(w, h):
                continue
            if w + gap_needed > remaining_width + _EPS:
                continue
            return Unit(part=part, unit_index=0), w, h, rotated
        return None

    def peek_fitting_in_slot(self, max_height, max_width):
        for part in self._parts:
            if self._counts.get(part.part_no, 0) <= 0 or self._dominant_blocked(part):
                continue
            orientation = _choose_orientation_bounded(part, max_height, max_width, self._fixed_rotation(part))
            if orientation is None:
                continue
            w, h, rotated = orientation
            if self._cap_blocked(w, h):
                continue
            return Unit(part=part, unit_index=0), w, h, rotated
        return None

    def peek_specific_fitting_in_slot(self, part_no, max_height, max_width):
        if self._counts.get(part_no, 0) <= 0:
            return None
        part = next((p for p in self._parts if p.part_no == part_no), None)
        if part is None or self._dominant_blocked(part):
            return None
        orientation = _choose_orientation_bounded(part, max_height, max_width, self._fixed_rotation(part))
        if orientation is None:
            return None
        w, h, rotated = orientation
        if self._cap_blocked(w, h):
            return None
        return Unit(part=part, unit_index=0), w, h, rotated

    def find_dominant(self, usable_w, usable_h, avg_unit_weight):
        candidates = []
        for p in self._parts:
            if self._counts.get(p.part_no, 0) <= 0 or not _is_dominant(p, usable_w, usable_h, avg_unit_weight):
                continue
            fixed_rotation = self._fixed_rotation(p)
            placed_w = p.dim_b if fixed_rotation else p.dim_a
            placed_h = p.dim_a if fixed_rotation else p.dim_b
            if self._cap_blocked(placed_w, placed_h):
                continue
            candidates.append(p)
        if not candidates:
            return None
        return Unit(part=max(candidates, key=lambda p: p.weight), unit_index=0)

    def find_wide_anchor(self, usable_w, usable_h):
        for part in self._parts:
            if self._counts.get(part.part_no, 0) <= 0 or not _is_wide_anchor(part, usable_w, usable_h):
                continue
            fixed_rotation = self._fixed_rotation(part)
            placed_w = part.dim_b if fixed_rotation else part.dim_a
            placed_h = part.dim_a if fixed_rotation else part.dim_b
            if self._cap_blocked(placed_w, placed_h):
                continue
            return Unit(part=part, unit_index=0)
        return None

    def peek_candidates(self, max_height, max_width, max_count: int | None = None):
        """Like peek_fitting_in_slot, but returns up to max_count fitting
        distinct parts (area-descending) instead of stopping at the first
        one — used by the rescue lookahead to try several alternatives, not
        just the single largest-area candidate."""
        result: list[Unit] = []
        for part in self._parts:
            if self._counts.get(part.part_no, 0) <= 0 or self._dominant_blocked(part):
                continue
            orientation = _choose_orientation_bounded(part, max_height, max_width, self._fixed_rotation(part))
            if orientation is None:
                continue
            w, h, rotated = orientation
            if self._cap_blocked(w, h):
                continue
            result.append(Unit(part=part, unit_index=0))
            if max_count is not None and len(result) >= max_count:
                break
        return result

    def consume(self, unit: Unit) -> None:
        self._counts[unit.part.part_no] -= 1

    def consume_specific(self, unit: Unit) -> None:
        self._counts[unit.part.part_no] -= 1

    def push_back(self, unit: Unit) -> None:
        self._counts[unit.part.part_no] = self._counts.get(unit.part.part_no, 0) + 1


def _column_used_height(col, item_gap: float) -> float:
    """Total stacked height of a column's items, including the plain,
    constant item_gap between them."""
    items = col["items"]
    if not items:
        return 0.0
    total = items[0][2]
    for i in range(1, len(items)):
        total += item_gap + items[i][2]
    return total


def _column_weight(col) -> float:
    return sum(u.part.weight for u, _w, _h, _r in col["items"])


def _partition_row_columns(columns, start_side: str):
    """Balanced two-way partition of a row's columns: sort by (stack) weight
    descending and alternate assignment — the classic greedy partition
    heuristic for a balanced split, applied once to the row's full column set
    rather than deciding each column's side as it's picked. Each arm ends up
    heaviest-column-first, which `_finalize_rack` places closest to the row's
    center (reversed(left_arm) + right_arm)."""
    ordered = sorted(columns, key=lambda c: -_column_weight(c))
    left_arm = []
    right_arm = []
    left_w = 0.0
    right_w = 0.0
    prefer_left = start_side == "left"
    for col in ordered:
        w = _column_weight(col)
        if prefer_left:
            left_arm.append(col)
            left_w += w
        else:
            right_arm.append(col)
            right_w += w
        prefer_left = not prefer_left
    return left_arm, right_arm, left_w, right_w


def _fill_columns(supplier, max_width: float, row_height, avail_h: float, item_gap: float):
    """Greedily fill columns within a max_width budget: for each new
    candidate, first try to stack it into an existing column, preferring one
    that already holds the *same part type* (so repeated instances of one
    part group together instead of scattering), falling back to the column
    with the least leftover headroom among any type. Only start a new column
    (which costs additional width) if nothing fits any existing column.

    Gaps are a plain, constant item_gap (see module docstring) — order-
    independent, so a headroom/width check is exact upfront, with no
    estimate-then-reverify step needed.

    If row_height is given up front, every column is capped to it (used to
    fill a dominant row's flanks within its already-fixed height); if None,
    the first column placed establishes it (used for an ordinary row, whose
    height isn't known until something is placed). Returns
    (columns, row_height, used_width)."""
    columns: list[dict] = []
    used_width = 0.0

    while True:
        placed_this_round = False

        if row_height is not None:
            same_type_best = None  # (col_idx, unit, w, h, rotated, leftover)
            any_type_best = None
            for idx, col in enumerate(columns):
                used_h = _column_used_height(col, item_gap)
                headroom = row_height - used_h - item_gap
                if headroom <= _EPS:
                    continue
                existing_part_no = col["items"][-1][0].part.part_no
                same_type_candidate = supplier.peek_specific_fitting_in_slot(existing_part_no, headroom, col["width"])
                if same_type_candidate is not None:
                    unit, w, h, rotated = same_type_candidate
                    leftover = headroom - h
                    if same_type_best is None or leftover < same_type_best[-1]:
                        same_type_best = (idx, unit, w, h, rotated, leftover)
                    continue
                candidate = supplier.peek_fitting_in_slot(headroom, col["width"])
                if candidate is None:
                    continue
                unit, w, h, rotated = candidate
                leftover = headroom - h
                if any_type_best is None or leftover < any_type_best[-1]:
                    any_type_best = (idx, unit, w, h, rotated, leftover)

            best = same_type_best if same_type_best is not None else any_type_best
            if best is not None:
                idx, unit, w, h, rotated, _leftover = best
                supplier.consume(unit)
                columns[idx]["items"].append((unit, w, h, rotated))
                placed_this_round = True

        if not placed_this_round:
            gap_needed = item_gap if columns else 0.0
            remaining_width = max_width - used_width
            candidate = supplier.peek_fitting(row_height, avail_h, remaining_width, gap_needed)
            if candidate is not None:
                unit, w, h, rotated = candidate
                supplier.consume(unit)
                columns.append({"items": [(unit, w, h, rotated)], "width": w})
                used_width += gap_needed + w
                if row_height is None:
                    row_height = h
                placed_this_round = True

        if not placed_this_round:
            break

    return columns, row_height, used_width


def _fill_flank(supplier, side_width: float, row_height: float, item_gap: float):
    """Fill one side ('flank') of a dominant row's leftover width with
    ordinary columns from the supplier, capped to the dominant item's own
    established row height. Returns (columns, used_width) — ([], 0.0) if
    there's no leftover width or nothing fits."""
    if side_width <= _EPS:
        return [], 0.0
    columns, _row_height, used_width = _fill_columns(supplier, side_width, row_height, row_height, item_gap)
    return columns, used_width


def _build_row(supplier, usable_w: float, avail_h: float, item_gap: float, left_weight: float, right_weight: float):
    """Build one shelf row out of columns (see _fill_columns), then partition
    the finished column set for left/right balance. Gaps are a plain,
    constant item_gap (order-independent), so the width _fill_columns
    already computed is exactly the row's true width regardless of which
    columns end up in which arm — no re-verification against a different
    render order is needed."""
    columns, row_height, used_width = _fill_columns(supplier, usable_w, None, avail_h, item_gap)
    if not columns:
        return None

    start_side = "left" if left_weight <= right_weight else "right"
    left_arm, right_arm, _local_left, _local_right = _partition_row_columns(columns, start_side)
    local_left = sum(_column_weight(c) for c in left_arm)
    local_right = sum(_column_weight(c) for c in right_arm)

    return {
        "left_arm": left_arm,
        "right_arm": right_arm,
        "height": row_height,
        "used_width": used_width,
    }, local_left, local_right


def _build_dominant_row(unit: Unit, usable_w: float, usable_h: float, avail_h: float, supplier, item_gap: float):
    """A dominant part gets its own row, centered across the midline instead
    of assigned to a side. Its orientation follows the same fixed-per-type
    rule as everything else (_fixed_rotation_for_part), with the same
    single-instance fallback if that provably doesn't fit here.

    Any leftover width on either side of the centered item is filled with
    ordinary columns from the supplier, exactly like a normal row would. This
    is what keeps the dominant-item special case from ever *blocking* other
    items out of the same rack: centering only ever affects this one row's
    internal arrangement (the item's position stays fixed at the row's
    midline), never whether other items still fit on this rack. The
    dominant unit itself is consumed before the flanks are filled, so a
    strict/batch supplier's queue head advances past it and the flanks can
    actually see the next candidates instead of being blocked by it."""
    part = unit.part
    orientation = _choose_orientation(part, None, avail_h, _fixed_rotation_for_part(part, usable_w, usable_h))
    if orientation is None:
        return None
    w, h, rotated = orientation
    if w > usable_w + _EPS:
        return None

    supplier.consume(unit)
    col = {"items": [(unit, w, h, rotated)], "width": w}

    side_width = max(0.0, (usable_w - w) / 2 - item_gap)
    left_arm, _left_used = _fill_flank(supplier, side_width, h, item_gap)
    right_arm, _right_used = _fill_flank(supplier, side_width, h, item_gap)

    return {
        "left_arm": left_arm,
        "right_arm": right_arm,
        "center_column": col,
        "height": h,
        "used_width": w,
    }


def _clone_supplier(supplier):
    """Cheap snapshot of a supplier's mutable state, so two alternative row
    builds can be tried speculatively without permanently consuming from the
    real supplier until one candidate is chosen (see
    _build_row_preferring_symmetry). Both supplier classes only hold simple,
    shallow-copyable state (a deque / a counts dict, plus a few read-only
    attributes safe to share by reference)."""
    clone = supplier.__class__.__new__(supplier.__class__)
    clone.__dict__.update(supplier.__dict__)
    if isinstance(supplier, _StrictSupplier):
        clone._queue = deque(supplier._queue)
    else:
        clone._counts = dict(supplier._counts)
    return clone


def _adopt_supplier_state(supplier, chosen_clone) -> None:
    supplier.__dict__.update(chosen_clone.__dict__)


def _row_unit_count(row) -> int:
    if row is None:
        return 0
    columns = row["left_arm"] + row["right_arm"]
    if row.get("center_column") is not None:
        columns = columns + [row["center_column"]]
    return sum(len(col["items"]) for col in columns)


def _build_row_preferring_symmetry(supplier, wide_anchor_unit, usable_w, usable_h, avail_h, item_gap, rack_left, rack_right):
    """Compare a normal (weight-balanced) row build against a centered,
    symmetric build anchored on `wide_anchor_unit` (a part whose wide
    orientation alone spans a large share of the rack, regardless of weight
    — see _is_wide_anchor), and keep whichever places at least as many
    units. This is what turns "prefer the symmetric layout" into a change
    that can never cost rack space: the asymmetric layout only ever wins
    when it genuinely fits more; a tie is resolved in favor of the more
    symmetric arrangement, matching "auch wenn beide Varianten dieselbe
    Anzahl an Warenträgern benötigen".

    Returns (row, added_left, added_right) — added_left/added_right are 0.0
    for the centered variant, exactly like the weight-dominant case: a
    centered part's contribution to the rack's balance comes from its own
    straddling position (see Rack.left_right_weight), not a side
    assignment."""
    normal_supplier = _clone_supplier(supplier)
    normal_result = _build_row(normal_supplier, usable_w, avail_h, item_gap, rack_left, rack_right)

    centered_supplier = _clone_supplier(supplier)
    centered_row = _build_dominant_row(wide_anchor_unit, usable_w, usable_h, avail_h, centered_supplier, item_gap)

    normal_count = _row_unit_count(normal_result[0]) if normal_result is not None else 0
    centered_count = _row_unit_count(centered_row)

    if centered_row is not None and centered_count >= normal_count:
        _adopt_supplier_state(supplier, centered_supplier)
        return centered_row, 0.0, 0.0

    if normal_result is not None:
        _adopt_supplier_state(supplier, normal_supplier)
        return normal_result

    return None, 0.0, 0.0


def _establish_rack_margin(supplier, usable_w_nominal: float, usable_h_nominal: float, tan_half: float):
    """Peek whichever part `supplier` would naturally offer first for a
    brand-new rack (largest-remaining-area first for _FreeSupplier; the
    strict queue head for _StrictSupplier — see peek_head_part) and, if its
    own placed height or width exceeds TILT_MARGIN_MIN_DIMENSION_MM,
    establish and lock in this rack's own tilt margin from it right now,
    before a single item is placed — activating the supplier's rack cap so
    nothing accepted afterward can ever need a larger margin than this (see
    module docstring). Returns (usable_w_for_this_rack,
    usable_h_for_this_rack); always clears/sets the supplier's cap as a
    side effect, so callers never need to remember to do so themselves.

    tan_half <= 0 (no tilt angle at all) is a deliberate, cheap no-op: no
    cap, no shrinkage, nominal envelope returned unchanged — this is also
    what a caller passes when it wants to reuse an *already* established
    margin as-is instead (rescue mid-build, symmetrize's remainder repack
    — see their own call sites), since every unit they could possibly see
    already satisfies whatever cap an outer, still-active call already
    locked in.

    The cap is always set to at least TILT_MARGIN_MIN_DIMENSION_MM in each
    direction, even when the head part itself needs no margin at all (both
    its own placed height and width already under the threshold) — this is
    deliberate, not just a harmless default: leaving the cap fully
    *inactive* in that case would let a later, still-untried candidate
    exceeding the threshold slip onto this rack with no restriction at all
    (a small head establishes "no margin needed", then a much larger part
    joins afterward under that same, now-stale assumption), which is
    exactly the retroactive-margin-growth scenario construction is
    supposed to make impossible. Capping at the threshold even in the
    "no margin" case still allows anything up to the threshold through
    freely (matching "no margin below the threshold, regardless of
    order"), while guaranteeing anything that *would* need a margin gets
    deferred to a new rack instead — where it becomes that rack's own head
    and correctly establishes its own margin from the start."""
    if tan_half <= _EPS:
        supplier.clear_rack_cap()
        return usable_w_nominal, usable_h_nominal
    cap = _rack_cap_for_part(supplier, supplier.peek_head_part())
    if cap is None:
        supplier.clear_rack_cap()
        return usable_w_nominal, usable_h_nominal
    cap_h, cap_w = cap
    supplier.set_rack_cap(cap_h, cap_w)
    extra_lr = _tilt_clearance(cap_h, tan_half)
    extra_tb = _tilt_clearance(cap_w, tan_half)
    return usable_w_nominal - 2 * extra_lr, usable_h_nominal - 2 * extra_tb


def _rack_tilt_margin(rows, tan_half: float) -> tuple[float, float]:
    """(extra_lr, extra_tb): this rack's own tilt margin, computed directly
    from its actual final placed contents. Safe to recompute here — rather
    than threading a value through every step that can touch a rack's rows
    — because the construction-order guarantee (_establish_rack_margin,
    module docstring) already ensures nothing on this rack exceeds whatever
    was established when the rack was first built, and every later step
    (rescue, mode!=frei's compacting repack, rebalancing, shifting, the
    symmetry pass) only ever rearranges or removes units already on the
    rack, never adds one from outside — so recomputing from final content
    can only ever match or undercut the original margin, never exceed it."""
    if tan_half <= _EPS:
        return 0.0, 0.0
    max_h = max_w = 0.0
    for _unit, w, h, _rotated in _iter_row_items(rows):
        if h > max_h:
            max_h = h
        if w > max_w:
            max_w = w
    return _tilt_clearance(max_h, tan_half), _tilt_clearance(max_w, tan_half)


def _build_rack_rows(
    supplier, usable_w: float, usable_h: float, item_gap: float, avg_unit_weight: float,
    allow_rescue: bool = True, allow_reorder: bool = False,
):
    """Build one rack's rows from `supplier`, within the given usable_w/
    usable_h and whatever rack cap is currently active on `supplier` (see
    _StrictSupplier/_FreeSupplier's set_rack_cap/clear_rack_cap). This
    function never establishes or clears a cap itself, nor does it know
    anything about tilt — that is entirely the caller's responsibility
    (see _establish_rack_margin, _build_rack_rows_for_new_rack,
    _repack_items_compact)."""
    rows = []
    row_heights: list[float] = []
    rack_left = 0.0
    rack_right = 0.0
    while True:
        avail_h = _safe_avail_h(usable_h, row_heights, item_gap)
        if avail_h <= _EPS:
            break

        dominant_unit = supplier.find_dominant(usable_w, usable_h, avg_unit_weight)
        row = None
        if dominant_unit is not None:
            row = _build_dominant_row(dominant_unit, usable_w, usable_h, avail_h, supplier, item_gap)

        if row is not None:
            rows.append(row)
            row_heights.append(row["height"])
            continue

        wide_anchor_unit = supplier.find_wide_anchor(usable_w, usable_h)
        if wide_anchor_unit is not None:
            row, added_left, added_right = _build_row_preferring_symmetry(
                supplier, wide_anchor_unit, usable_w, usable_h, avail_h, item_gap, rack_left, rack_right
            )
        else:
            result = _build_row(supplier, usable_w, avail_h, item_gap, rack_left, rack_right)
            row, added_left, added_right = result if result is not None else (None, 0.0, 0.0)

        if row is None:
            if allow_rescue and rows and supplier.has_remaining():
                rescued = _try_rescue_with_repack(
                    rows, supplier, usable_w, usable_h, item_gap, avg_unit_weight, allow_reorder
                )
                if rescued is not None:
                    rows = rescued
                    row_heights = [r["height"] for r in rows]
                    rack_left, rack_right = _rack_left_right_from_rows(rows)
                    continue
            break
        rows.append(row)
        row_heights.append(row["height"])
        rack_left += added_left
        rack_right += added_right
    return rows


_MAX_MARGIN_GROWTH_ATTEMPTS = 25


def _row_true_width(row, item_gap: float) -> float:
    """A row's real occupied width, accounting for dominant/centered rows
    whose own 'used_width' only tracks the centered item's width (flanks
    sit outside that budget, placed outward from the fixed midline — see
    _build_dominant_row/_finalize_rack). Since a row is always centered on
    the rack's own midline, the binding width is twice whichever side
    (left or right) extends furthest from center."""
    if row.get("center_column") is not None:
        center_w = row["center_column"]["width"]
        left_extent = center_w / 2 + sum(c["width"] for c in row["left_arm"]) + item_gap * len(row["left_arm"])
        right_extent = center_w / 2 + sum(c["width"] for c in row["right_arm"]) + item_gap * len(row["right_arm"])
        return 2 * max(left_extent, right_extent)
    return row["used_width"]


def _rows_fit_envelope(rows, usable_w: float, usable_h: float, item_gap: float) -> bool:
    """Whether an already-built set of rows would still fit within a
    (possibly smaller) usable_w/usable_h — used by
    _build_rack_rows_for_new_rack to check whether growing this rack's
    tilt margin would invalidate anything already placed on it."""
    total_h = sum(r["height"] for r in rows) + item_gap * (len(rows) - 1) if rows else 0.0
    if total_h > usable_h + _EPS:
        return False
    return all(_row_true_width(r, item_gap) <= usable_w + _EPS for r in rows)


def _rack_cap_for_part(supplier, part: Part | None):
    """(cap_h, cap_w) this rack's tilt margin would need if `part` were its
    own largest occupant — None if there's no part to consider at all.
    Shared by _establish_rack_margin (single, fixed establishment — 'frei'
    and any already largest-first candidate order) and
    _build_rack_rows_for_new_rack's growth loop (repeated re-evaluation as
    the strict/batch queue's own head changes)."""
    if part is None:
        return None
    fixed_rotation = supplier.fixed_rotation_for(part)
    placed_h = part.dim_a if fixed_rotation else part.dim_b
    placed_w = part.dim_b if fixed_rotation else part.dim_a
    return max(placed_h, TILT_MARGIN_MIN_DIMENSION_MM), max(placed_w, TILT_MARGIN_MIN_DIMENSION_MM)


def _build_rack_rows_for_new_rack(
    supplier, usable_w_nominal: float, usable_h_nominal: float, item_gap: float, avg_unit_weight: float,
    allow_rescue: bool = True, allow_reorder: bool = False, tan_half: float = 0.0,
):
    """Build one brand-new rack's rows from `supplier`, establishing (and,
    where needed, growing) this rack's own tilt margin as it goes (module
    docstring).

    A single, fixed establishment from whichever part the supplier offers
    first (_establish_rack_margin) is exact whenever that first part is
    already this rack's own largest — true by construction for 'frei'
    (_FreeSupplier always offers largest-remaining-area first) and, once a
    batch order front-loads large batches, usually true for 'chargenweise'
    too. It is *not* generally true for strict part-no order
    ('listenreihenfolge', and any chargenweise candidate order that isn't
    size-sorted): a small head may be followed by a much larger part later
    in the fixed order, and rejecting that later part outright (deferring
    it to a brand-new rack solely because the small head under-estimated
    the margin) can fragment far more than necessary — the fixed order
    gives no opportunity to have tried the large part first on some other
    rack instead, unlike 'frei'/'chargenweise's own size-aware ordering.

    So after building under the current cap, this checks whether the
    supplier's very next candidate was excluded *only* by the cap (not by
    genuine lack of space — a candidate that wouldn't need a *larger* cap
    than what's already active was never cap-blocked in the first place)
    and, if so, whether growing the cap to admit it would still leave
    everything already built fitting the grown (smaller) envelope
    (_rows_fit_envelope) — exactly the question 'wird der Randabstand
    laufend als Maximum aktualisiert' asks. If growing is safe, the whole
    rack is rebuilt from scratch, from a snapshot of the supplier taken
    before this attempt (nothing already placed is ever individually
    removed — the previous attempt's rows are simply discarded and
    superseded by a fresh, larger build that starts from the exact same
    unconsumed state), under the grown cap. If growing would invalidate
    something already built, the previous attempt's rows are kept as-is
    and the next candidate is left for a new rack — 'kein rückwirkendes
    Entfernen, stattdessen wird das neue Bauteil auf den nächsten
    Warenträger verschoben', achieved here by simply never having
    committed to the larger cap in the first place."""
    if tan_half <= _EPS:
        supplier.clear_rack_cap()
        return _build_rack_rows(supplier, usable_w_nominal, usable_h_nominal, item_gap, avg_unit_weight, allow_rescue, allow_reorder)

    cap = _rack_cap_for_part(supplier, supplier.peek_head_part())
    rows: list = []
    for attempt in range(_MAX_MARGIN_GROWTH_ATTEMPTS):
        snapshot = _clone_supplier(supplier)
        if cap is None:
            supplier.clear_rack_cap()
            usable_w, usable_h = usable_w_nominal, usable_h_nominal
        else:
            cap_h, cap_w = cap
            supplier.set_rack_cap(cap_h, cap_w)
            usable_w = usable_w_nominal - 2 * _tilt_clearance(cap_h, tan_half)
            usable_h = usable_h_nominal - 2 * _tilt_clearance(cap_w, tan_half)

        rows = _build_rack_rows(supplier, usable_w, usable_h, item_gap, avg_unit_weight, allow_rescue, allow_reorder)

        if attempt == _MAX_MARGIN_GROWTH_ATTEMPTS - 1:
            # Last allowed attempt: keep this build and this exact consumed
            # supplier state as-is -- must NOT roll back to `snapshot` below
            # without rebuilding against whatever it reverts to, or the
            # returned `rows` would silently stop matching what the
            # supplier actually consumed (nothing placed would ever be
            # consumed at all, looping the caller forever).
            break
        next_cap = _rack_cap_for_part(supplier, supplier.peek_head_part())
        if next_cap is None:
            break
        if cap is not None:
            # The cap must only ever grow, height and width independently
            # (Maximum aus Höhe und Breite der bisher zugewiesenen
            # Bauteile) -- taking the *next candidate's own* cap in
            # isolation here (instead of combining it with what's already
            # required) would let a wide-but-short candidate silently
            # shrink an already-established height requirement (or vice
            # versa), understating the margin actually needed by whatever
            # taller/wider part is already relying on the current cap.
            next_cap = (max(cap[0], next_cap[0]), max(cap[1], next_cap[1]))
        if cap is not None and next_cap[0] <= cap[0] + _EPS and next_cap[1] <= cap[1] + _EPS:
            break  # next candidate isn't cap-blocked at all -- stopped here by genuine lack of space
        grown_usable_w = usable_w_nominal - 2 * _tilt_clearance(next_cap[0], tan_half)
        grown_usable_h = usable_h_nominal - 2 * _tilt_clearance(next_cap[1], tan_half)
        if not _rows_fit_envelope(rows, grown_usable_w, grown_usable_h, item_gap):
            break  # growing would invalidate what's already built on this rack
        _adopt_supplier_state(supplier, snapshot)
        cap = next_cap
    supplier.clear_rack_cap()
    return rows


def _iter_row_items(rows):
    for row in rows:
        columns = row["left_arm"] + row["right_arm"]
        if row.get("center_column") is not None:
            columns = columns + [row["center_column"]]
        for col in columns:
            for item in col["items"]:
                yield item


def _rack_left_right_from_rows(rows) -> tuple[float, float]:
    left = 0.0
    right = 0.0
    for row in rows:
        for col in row["left_arm"]:
            left += _column_weight(col)
        for col in row["right_arm"]:
            right += _column_weight(col)
    return left, right


def _try_rescue_with_repack(
    rows, supplier, usable_w: float, usable_h: float, item_gap: float, avg_unit_weight: float,
    allow_reorder: bool,
):
    """Before finally closing a rack because the next unit doesn't fit the
    current naive row-by-row layout, check whether a denser free repack of
    everything already on the rack *plus* some still-pending unit would fit
    within usable_h. The row-by-row greedy build can waste space a free
    rearrangement wouldn't (e.g. one row a few cm too full to accept a wide
    part that a slightly different arrangement of the rest would have made
    room for) — this rescues those genuine near-misses.

    allow_reorder=True ('frei' only, via _FreeSupplier — no ordering
    constraint exists there in the first place) searches every distinct
    still-fitting-by-itself candidate the supplier's whole remaining pool
    offers, in order, and takes the first one whose repack actually
    succeeds.

    allow_reorder=False ('chargenweise'/'listenreihenfolge', via
    _StrictSupplier) only ever tests that supplier's literal queue head —
    _StrictSupplier structurally cannot hand out anything else (no
    peek_candidates()/consume_specific()), which is what guarantees this
    rescue can never place a part on an earlier rack than the strict/batch
    order requires, no matter how tempting a later candidate looks.

    Returns the denser rows (with that one unit consumed) if it helped,
    else None (nothing consumed).

    Deliberately repacks at tan_half=0 (the _repack_items_compact default):
    every candidate considered here already came from the *outer*
    supplier's own cap-aware peek/find methods, i.e. already satisfies
    whatever tilt margin this rack committed to when it was first built
    (see _establish_rack_margin) — re-establishing a margin from scratch
    for just this trial unit set would be redundant at best and, worse,
    could compute a *different* (smaller) one than the rack's real,
    already-fixed margin if the rescued unit happens not to be the
    largest, silently under-sizing the rack's actual required clearance."""
    if allow_reorder:
        candidates = supplier.peek_candidates(usable_h, usable_w, None)
        if not candidates:
            return None
        existing_units = _flatten_rows_to_units(rows)
        for pending_unit in candidates:
            trial_units = existing_units + [pending_unit]
            repacked = _repack_items_compact(trial_units, usable_w, usable_h, item_gap, avg_unit_weight)
            if repacked is not None:
                supplier.consume_specific(pending_unit)
                return repacked
        return None

    candidate = supplier.peek_fitting(None, usable_h, usable_w, 0.0)
    if candidate is None:
        return None
    pending_unit = candidate[0]
    trial_units = _flatten_rows_to_units(rows) + [pending_unit]
    repacked = _repack_items_compact(trial_units, usable_w, usable_h, item_gap, avg_unit_weight)
    if repacked is None:
        return None
    supplier.consume(pending_unit)
    return repacked


def _place_column(placed: list[PlacedUnit], col, x: float, y_top: float, item_gap: float) -> None:
    """Stack one column's parts top-down at horizontal position x, each
    hanging from the row's own top edge (or the part above it) — same
    top-alignment principle applied within the column, not just across the
    row."""
    y_cursor = y_top
    for k, (unit, w, h, rotated) in enumerate(col["items"]):
        if k > 0:
            y_cursor -= item_gap
        placed.append(
            PlacedUnit(
                part_no=unit.part.part_no,
                x=x,
                y=y_cursor - h,
                w=w,
                h=h,
                rotated=rotated,
                weight=unit.part.weight,
                depth=unit.part.depth,
            )
        )
        y_cursor -= h


def _finalize_rack(
    rows, rack_index: int, rack_l: float, rack_h: float, rack_b: float, edge_margin: float, item_gap: float,
) -> Rack:
    """Convert `rows` into absolute placed positions. Every row is
    independently centered on the rack's own physical midline (rack_l/2,
    identical to the usable-width midline since edge_margin is symmetric),
    which automatically gives both the left and right edge at least
    edge_margin of clearance to whichever row is widest, since row-building
    itself never lets a row's own width exceed usable_w. Rows simply stack
    top-down starting at edge_margin below the rack's own top edge.

    `edge_margin` here (as everywhere row-building/positioning uses it) is
    already whatever value the caller decided on — for a tilt angle > 0,
    that's the plain configured margin plus this specific rack's own
    tilt-derived vertical clearance (see pack_shelf / _rack_tilt_margin);
    this function itself has no idea whether that's the case, and doesn't
    need to — it just places things within whatever usable envelope it was
    given, exactly as it always has."""
    placed: list[PlacedUnit] = []
    y_top = rack_h - edge_margin
    for i, row in enumerate(rows):
        if i > 0:
            y_top -= item_gap
        row_bottom = y_top - row["height"]

        if row.get("center_column") is not None:
            # The dominant item's own position always stays fixed at the
            # row's midline (preserving its ~50/50 balance benefit), no
            # matter what flank content did or didn't fit beside it — flank
            # columns are placed outward from there, never the other way
            # around (see _build_dominant_row).
            center_col = row["center_column"]
            center_w = center_col["width"]
            center_x = rack_l / 2 - center_w / 2
            _place_column(placed, center_col, center_x, y_top, item_gap)

            cursor_x = center_x
            for col in row["left_arm"]:
                cursor_x -= item_gap + col["width"]
                _place_column(placed, col, cursor_x, y_top, item_gap)

            cursor_x = center_x + center_w
            for col in row["right_arm"]:
                cursor_x += item_gap
                _place_column(placed, col, cursor_x, y_top, item_gap)
                cursor_x += col["width"]
        else:
            ordered_columns = list(reversed(row["left_arm"])) + row["right_arm"]
            start_x = rack_l / 2 - row["used_width"] / 2
            cursor_x = start_x
            for j, col in enumerate(ordered_columns):
                if j > 0:
                    cursor_x += item_gap
                _place_column(placed, col, cursor_x, y_top, item_gap)
                cursor_x += col["width"]
        y_top = row_bottom
    return Rack(index=rack_index, rack_l=rack_l, rack_h=rack_h, rack_b=rack_b, placed=placed)


def _row_sort_key(row):
    """Post-hoc display/positioning sort key: rows with a lighter *average*
    weight per part go on top, heavier average further down. Using the mean
    rather than the row total means a full row of many identical (therefore
    numerically heavier in total) parts doesn't outrank a half-empty row of
    the same part — ties (e.g. a rack holding only one part type, where every
    row's average is identical) fall back to preferring the fuller/more
    compact row on top. Used only for the final vertical ordering, never as
    a criterion during the actual packing/rebalancing."""
    columns = row["left_arm"] + row["right_arm"]
    if row.get("center_column") is not None:
        columns = columns + [row["center_column"]]
    items = [it for col in columns for it in col["items"]]
    if not items:
        return (0.0, 0)
    total_weight = sum(u.part.weight for u, _w, _h, _r in items)
    mean_weight = total_weight / len(items)
    return (mean_weight, -len(items))


def _deviation(left: float, right: float) -> float:
    heavier = max(left, right)
    if heavier <= _EPS:
        return 0.0
    return abs(left - right) / heavier


def _rows_deep_copy(rows):
    result = []
    for row in rows:
        new_row = {
            "left_arm": [{"items": list(c["items"]), "width": c["width"]} for c in row["left_arm"]],
            "right_arm": [{"items": list(c["items"]), "width": c["width"]} for c in row["right_arm"]],
            "height": row["height"],
            "used_width": row["used_width"],
        }
        if row.get("center_column") is not None:
            c = row["center_column"]
            new_row["center_column"] = {"items": list(c["items"]), "width": c["width"]}
        result.append(new_row)
    return result


def _recompute_row_width(row, item_gap: float) -> float:
    columns = list(reversed(row["left_arm"])) + row["right_arm"]
    if not columns:
        return 0.0
    total = columns[0]["width"]
    for col in columns[1:]:
        total += item_gap + col["width"]
    return total


def _reorient_column_for_height(col, max_height: float, item_gap: float):
    """Check whether a column still fits under a different row's height
    budget. Orientation is fixed per part type (_fixed_rotation_for_part) —
    a cross-row swap may relocate a column but must never change how it's
    oriented, so this only ever checks the column's *existing* geometry
    against the target height; it no longer tries the other orientation as
    a compatibility lever (that was rotation-as-balance-tool, now retired in
    favor of the fixed-orientation rule)."""
    if _column_used_height(col, item_gap) > max_height + _EPS:
        return None
    return {"items": list(col["items"]), "width": col["width"]}


def _apply_swap(rows, ref_a, ref_b, usable_w: float, item_gap: float) -> bool:
    """Exchange two columns (whole stacks), possibly in different rows.
    Same-row swaps just reshuffle arm membership. Cross-row swaps must find
    an orientation for each solo-item column that fits the other's row
    height (rotation allowed), or an unchanged fit for stacked columns.
    Rejected (rows left unmodified) if either row's true width would exceed
    usable_w."""
    ri_a, arm_a, ai_a = ref_a
    ri_b, arm_b, ai_b = ref_b
    col_a = rows[ri_a][arm_a][ai_a]
    col_b = rows[ri_b][arm_b][ai_b]

    if ri_a == ri_b:
        row = rows[ri_a]
        row[arm_a][ai_a] = col_b
        row[arm_b][ai_b] = col_a
        new_width = _recompute_row_width(row, item_gap)
        if new_width > usable_w + _EPS:
            row[arm_a][ai_a] = col_a
            row[arm_b][ai_b] = col_b
            return False
        row["used_width"] = new_width
        return True

    row_a = rows[ri_a]
    row_b = rows[ri_b]

    new_col_a_in_b = _reorient_column_for_height(col_a, row_b["height"], item_gap)
    new_col_b_in_a = _reorient_column_for_height(col_b, row_a["height"], item_gap)
    if new_col_a_in_b is None or new_col_b_in_a is None:
        return False

    row_a[arm_a][ai_a] = new_col_b_in_a
    row_b[arm_b][ai_b] = new_col_a_in_b
    new_used_a = _recompute_row_width(row_a, item_gap)
    new_used_b = _recompute_row_width(row_b, item_gap)
    if new_used_a > usable_w + _EPS or new_used_b > usable_w + _EPS:
        row_a[arm_a][ai_a] = col_a
        row_b[arm_b][ai_b] = col_b
        return False
    row_a["used_width"] = new_used_a
    row_b["used_width"] = new_used_b
    return True


def _all_column_refs(rows):
    """Swap/rotation candidates for rebalancing. Dominant rows' flank columns
    are deliberately excluded: their row's 'used_width' tracks only the
    centered item's own width (the flanks sit outside that budget, placed
    outward from the fixed midline — see _finalize_rack), so the swap/
    rotation width-budget math (which assumes 'used_width' reflects the sum
    of the arms it's mutating) would be silently wrong for them. They remain
    eligible for the separate shift-to-next-rack mechanism (_all_shift_refs),
    which only ever removes columns rather than rewriting widths in place."""
    refs = []
    for ri, row in enumerate(rows):
        if row.get("center_column") is not None:
            continue
        for ai in range(len(row["left_arm"])):
            refs.append((ri, "left_arm", ai))
        for ai in range(len(row["right_arm"])):
            refs.append((ri, "right_arm", ai))
    return refs


def _find_best_rebalancing_move(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, usable_w, current_deviation):
    refs = _all_column_refs(rows)
    best_rows = None
    best_deviation = current_deviation

    def _score(candidate_rows):
        rack = _finalize_rack(candidate_rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
        left, right = rack.left_right_weight()
        return _deviation(left, right)

    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            candidate = _rows_deep_copy(rows)
            if _apply_swap(candidate, refs[i], refs[j], usable_w, item_gap):
                dev = _score(candidate)
                if dev < best_deviation - _EPS:
                    best_deviation = dev
                    best_rows = candidate

    return best_rows


def _rebalance_rows(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, usable_w):
    """Post-hoc local search: if a fully-packed rack still exceeds the ±25%
    left/right balance tolerance, hill-climb via column swaps (across rows
    too), greedily applying the best-improving move each round until the
    tolerance is met or no improving move remains. Rotation is no longer a
    balance lever here (see _fixed_rotation_for_part) — swapping whole
    columns is the only move left, which never changes any item's
    orientation."""
    rack = _finalize_rack(rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
    left, right = rack.left_right_weight()
    deviation = _deviation(left, right)
    if deviation <= BALANCE_TOLERANCE:
        return rows

    for _ in range(MAX_REBALANCE_ITERATIONS):
        improved = _find_best_rebalancing_move(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, usable_w, deviation)
        if improved is None:
            break
        rows = improved
        rack = _finalize_rack(rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
        left, right = rack.left_right_weight()
        deviation = _deviation(left, right)
        if deviation <= BALANCE_TOLERANCE:
            break
    return rows


def _row_is_empty(row) -> bool:
    return not row["left_arm"] and not row["right_arm"] and row.get("center_column") is None


def _all_shift_refs(rows):
    """All (ri, arm_name, col_idx, item_idx) locations of individually
    removable units across the whole rack (including centered columns)."""
    refs = []
    for ri, row in enumerate(rows):
        arms = [("left_arm", row["left_arm"]), ("right_arm", row["right_arm"])]
        if row.get("center_column") is not None:
            arms.append(("center_column", [row["center_column"]]))
        for arm_name, cols in arms:
            for ci, col in enumerate(cols):
                for item_idx in range(len(col["items"])):
                    refs.append((ri, arm_name, ci, item_idx))
    return refs


def _remove_shift_refs(rows, refs, item_gap: float) -> list[Unit]:
    """Remove multiple (ri, arm_name, col_idx, item_idx) locations from rows
    in place, safely handling index shifts from removing several items (even
    several from the same column, or several columns from the same arm), and
    return the removed units. Rows left fully empty are NOT dropped here —
    the caller filters those out afterwards."""
    by_location: dict[tuple, list[int]] = {}
    for ri, arm_name, ci, item_idx in refs:
        by_location.setdefault((ri, arm_name, ci), []).append(item_idx)

    removed: list[Unit] = []
    for (ri, arm_name, ci), item_idxs in by_location.items():
        row = rows[ri]
        col = row["center_column"] if arm_name == "center_column" else row[arm_name][ci]
        for item_idx in sorted(item_idxs, reverse=True):
            removed.append(col["items"].pop(item_idx)[0])

    touched_rows = {ri for (ri, _arm, _ci) in by_location}
    for ri in touched_rows:
        row = rows[ri]
        for arm_name in ("left_arm", "right_arm"):
            arm = row[arm_name]
            for ci in sorted(range(len(arm)), reverse=True):
                if not arm[ci]["items"]:
                    arm.pop(ci)
        if row.get("center_column") is not None and not row["center_column"]["items"]:
            row["center_column"] = None
        row["used_width"] = (
            row["center_column"]["width"] if row.get("center_column") is not None
            else _recompute_row_width(row, item_gap)
        )
    return removed


def _find_best_refs_to_shift(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, current_deviation):
    """Find the unit or *pair* of units whose removal from this rack most
    improves the left/right balance — used only as a last resort when
    centering, gap-filling and swap/rotation rebalancing together still
    leave the rack over tolerance. A single dominant item's removal can
    sometimes worsen the split (its partial straddle of the midline was
    itself helping), so pairs are tried too before giving up. Returns a list
    of refs to remove together, or None if nothing improves."""
    refs = _all_shift_refs(rows)
    best_combo = None
    best_deviation = current_deviation

    def _score_after_removing(ref_list):
        candidate_rows = _rows_deep_copy(rows)
        _remove_shift_refs(candidate_rows, ref_list, item_gap)
        candidate_rows = [r for r in candidate_rows if not _row_is_empty(r)]
        if not candidate_rows:
            return None
        rack = _finalize_rack(candidate_rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
        left, right = rack.left_right_weight()
        return _deviation(left, right)

    for ref in refs:
        dev = _score_after_removing([ref])
        if dev is not None and dev < best_deviation - _EPS:
            best_deviation = dev
            best_combo = [ref]

    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            dev = _score_after_removing([refs[i], refs[j]])
            if dev is not None and dev < best_deviation - _EPS:
                best_deviation = dev
                best_combo = [refs[i], refs[j]]

    return best_combo


def _find_best_suffix_to_shift(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, current_deviation, order_key_fn):
    """Order-preserving variant of the shift search: only ever removes a
    *suffix* of the rack's chronological unit order (the latest-processed
    items first), trying increasingly larger suffixes until the tolerance is
    met or no more improvement is found. This guarantees shifting can never
    place an earlier-order unit on a later rack than a later-order unit —
    'strikte Listenreihenfolge'/'chargenweise' still decide chronological
    rack assignment; only the trailing edge of a rack's contents may move to
    the next one. Returns refs (latest-first) to remove, or None."""
    entries = []
    for ri, row in enumerate(rows):
        arms = [("left_arm", row["left_arm"]), ("right_arm", row["right_arm"])]
        if row.get("center_column") is not None:
            arms.append(("center_column", [row["center_column"]]))
        for arm_name, cols in arms:
            for ci, col in enumerate(cols):
                for item_idx, (unit, _w, _h, _r) in enumerate(col["items"]):
                    entries.append(((ri, arm_name, ci, item_idx), unit))

    entries.sort(key=lambda e: order_key_fn(e[1].part), reverse=True)  # latest-in-order first

    best_combo = None
    best_deviation = current_deviation
    for suffix_len in range(1, len(entries) + 1):
        combo = [ref for ref, _unit in entries[:suffix_len]]
        candidate_rows = _rows_deep_copy(rows)
        _remove_shift_refs(candidate_rows, combo, item_gap)
        candidate_rows = [r for r in candidate_rows if not _row_is_empty(r)]
        if not candidate_rows:
            continue
        rack = _finalize_rack(candidate_rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
        left, right = rack.left_right_weight()
        dev = _deviation(left, right)
        if dev <= BALANCE_TOLERANCE:
            return combo
        if dev < best_deviation - _EPS:
            best_deviation = dev
            best_combo = combo
    return best_combo


def _shift_excess_to_next_rack(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, usable_w, supplier, order_key_fn=None):
    """Last resort: if the rack still exceeds the balance tolerance after
    centering, gap-filling and swap/rotation rebalancing, move the item(s)
    most responsible for the imbalance to the next rack instead of accepting
    the violation — even though that grows the total rack count slightly.

    order_key_fn: when given (strict/batch modes), restricts shifting to a
    chronological suffix of the rack's contents, so the rack-to-rack
    assignment order established in Phase 1 can never be violated. When
    None ('frei' mode has no order to preserve), any improving unit or pair
    may be shifted."""
    rack = _finalize_rack(rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
    left, right = rack.left_right_weight()
    deviation = _deviation(left, right)
    if deviation <= BALANCE_TOLERANCE:
        return rows

    for _ in range(MAX_SHIFT_ITERATIONS):
        if not rows:
            break
        if order_key_fn is not None:
            best_combo = _find_best_suffix_to_shift(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, deviation, order_key_fn)
        else:
            best_combo = _find_best_refs_to_shift(rows, rack_l, rack_h, rack_b, edge_margin, item_gap, deviation)
        if best_combo is None:
            break
        removed_units = _remove_shift_refs(rows, best_combo, item_gap)
        rows[:] = [r for r in rows if not _row_is_empty(r)]
        # Push back in chronological order (earliest-of-the-removed last, so
        # it ends up at the very front of the queue / first for the next rack).
        for unit in removed_units:
            supplier.push_back(unit)

        if not rows:
            break
        rack = _finalize_rack(rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
        left, right = rack.left_right_weight()
        deviation = _deviation(left, right)
        if deviation <= BALANCE_TOLERANCE:
            break
    return rows


def _flatten_rows_to_units(rows) -> list[Unit]:
    units: list[Unit] = []
    for row in rows:
        columns = row["left_arm"] + row["right_arm"]
        if row.get("center_column") is not None:
            columns = columns + [row["center_column"]]
        for col in columns:
            for unit, _w, _h, _rotated in col["items"]:
                units.append(unit)
    return units


def _repack_items_compact(units: list[Unit], usable_w: float, usable_h: float, item_gap: float, avg_unit_weight: float, tan_half: float = 0.0):
    """Re-pack a fixed set of units as densely as possible (same free/
    best-fit row-building as 'frei' mode), ignoring their original relative
    order.

    'listenreihenfolge' and 'chargenweise' still decide, strictly/by batch,
    WHICH items end up on WHICH rack (that assignment is untouched). But once
    a rack's contents are fixed, there is no reason to keep them constrained
    to the original processing order internally — the user wants the
    tightest possible arrangement within that rack. Returns None (signalling
    "leave the original rows as-is") in the practically-impossible case that
    the free repack fails to place every item — it never invents room that
    wasn't already proven to exist by the original, less efficient packing.

    tan_half > 0 lets _build_rack_rows establish a *fresh* per-rack tilt
    margin from `units`' own largest part (used for consolidation's merged
    rack and mode!=frei's post-build compacting repack — a brand-new
    candidate rack built from this exact unit set, with no pre-existing
    margin to respect). tan_half=0 (the default) instead reuses whatever
    usable_w/usable_h is passed in as-is, no margin logic at all — used by
    rescue (mid-build, within an already-capped outer rack) and
    symmetrize's remainder repack (rearranging within an already-finalized
    rack's already-fixed margin): both operate on units that, by
    construction, already satisfy an outer margin this call must not
    recompute independently (see their own call sites)."""
    free_supplier = _FreeSupplier(units, usable_w, avg_unit_weight, usable_h, tan_half)
    # Uses the growth-capable establishment (_build_rack_rows_for_new_rack),
    # not a single fixed one: _FreeSupplier offers the largest-remaining-
    # AREA part first, but AREA-largest is not always HEIGHT/WIDTH-largest
    # (e.g. a long, thin part can out-dimension a squarer, larger-area one)
    # — establishing the cap from the area-largest item alone could wrongly
    # reject a later, still-untried part that needs a larger cap in only
    # one dimension, causing this function to spuriously report "doesn't
    # fit" (return None) for a unit set that actually does fit. The
    # growth-capable version detects exactly that case and grows the cap
    # to admit it, same as it already does for the strict/batch suppliers.
    rows = _build_rack_rows_for_new_rack(
        free_supplier, usable_w, usable_h, item_gap, avg_unit_weight, allow_rescue=False, tan_half=tan_half
    )
    if free_supplier.has_remaining():
        return None
    return rows


def _symmetric_stack_counts(cols_used: int, n: int) -> list[int] | None:
    """Palindromic per-column item counts for n identical items spread over
    cols_used columns — since every column here holds the *same* part in the
    *same* orientation (so every column has the same width), a palindromic
    counts list keeps the arrangement mirror-symmetric not just in x-position
    (which equal column widths and the existing block-centering already
    guarantee) but in stack height too. Returns None if no exactly-symmetric
    distribution exists for this cols_used (only possible when cols_used is
    odd — giving a center column to absorb an odd leftover — or when n
    divides evenly across cols_used)."""
    base, remainder = divmod(n, cols_used)
    counts = [base] * cols_used
    has_center = cols_used % 2 == 1
    center = cols_used // 2

    if remainder % 2 == 1:
        if not has_center:
            return None
        counts[center] += 1
        remainder -= 1

    pairs_needed = remainder // 2
    max_pairs = cols_used // 2
    if pairs_needed > max_pairs:
        return None
    for i in range(pairs_needed):
        offset = i + 1
        li = center - offset if has_center else center - 1 - i
        ri = center + offset if has_center else center + i
        counts[li] += 1
        counts[ri] += 1
    return counts


def _build_symmetric_group_row(part: Part, units: list, usable_w: float, usable_h: float, item_gap: float):
    """Build a single row containing only `units` (all the same part type),
    arranged as equal-width columns so the row's ordinary block-centering
    (see _finalize_rack) is automatically left-right mirror-symmetric around
    the rack's midline — the classic "39/39"/"18/18" case becomes exactly
    one column of each on either side of center. Uses only the part type's
    fixed orientation (_fixed_rotation_for_part) — this is a purely optional
    beautification pass, so unlike normal placement it has no single-instance
    fallback: if the fixed orientation doesn't allow a symmetric layout here,
    the group is simply left for the normal (fixed-orientation-respecting)
    repacker instead of ever deviating just for symmetry's sake. Tries every
    column count from "all side by side" down to a single stacked column,
    preferring whichever fits with the smallest row height (so it leaves the
    most room for the rest of the rack). Returns a row dict, or None if no
    column count lets all units fit symmetrically within usable_w x
    usable_h at all."""
    n = len(units)
    fixed_rotation = _fixed_rotation_for_part(part, usable_w, usable_h)
    w, h = (part.dim_b, part.dim_a) if fixed_rotation else (part.dim_a, part.dim_b)
    rotated = fixed_rotation
    if h > usable_h + _EPS or w > usable_w + _EPS:
        return None

    best = None  # (row_height, cols_used, counts)
    max_cols = int((usable_w + item_gap) // (w + item_gap)) if (w + item_gap) > _EPS else 0
    max_cols = max(0, min(max_cols, n))
    for cols_used in range(max_cols, 0, -1):
        counts = _symmetric_stack_counts(cols_used, n)
        if counts is None:
            continue
        stack_h = max(counts)
        row_height = stack_h * h + (stack_h - 1) * item_gap
        if row_height > usable_h + _EPS:
            continue
        used_width = cols_used * w + (cols_used - 1) * item_gap
        if used_width > usable_w + _EPS:
            continue
        best = (row_height, cols_used, counts)
        break  # largest valid cols_used found (shortest row)
    if best is None:
        return None

    _row_height, cols_used, counts = best
    columns = []
    idx = 0
    for c in counts:
        items = [(units[idx + k], w, h, rotated) for k in range(c)]
        idx += c
        columns.append({"items": items, "width": w})
    used_width = cols_used * w + (cols_used - 1) * item_gap
    return {
        "left_arm": [],
        "right_arm": columns,
        "height": _row_height,
        "used_width": used_width,
    }


def _build_symmetrized_rows(units: list[Unit], usable_w: float, usable_h: float, item_gap: float, avg_unit_weight: float):
    """Pull out same-part-type groups (count >= 2) into their own
    mirror-symmetric rows (see _build_symmetric_group_row), largest groups
    first, then densely repack whatever's left (singletons and any group
    that couldn't be symmetrized) with the existing free packer. Returns the
    combined rows, or None if the leftover repack somehow fails (should not
    happen — it's a subset of a unit set that already fit before)."""
    counts: dict[str, int] = {}
    for u in units:
        counts[u.part.part_no] = counts.get(u.part.part_no, 0) + 1
    symmetry_part_nos = sorted(
        (no for no, c in counts.items() if c >= 2),
        key=lambda no: -counts[no],
    )
    if not symmetry_part_nos:
        return None

    remaining_units = list(units)
    symmetric_rows = []
    for part_no in symmetry_part_nos:
        group_units = [u for u in remaining_units if u.part.part_no == part_no]
        if len(group_units) < 2:
            continue
        row = _build_symmetric_group_row(group_units[0].part, group_units, usable_w, usable_h, item_gap)
        if row is None:
            continue
        symmetric_rows.append(row)
        remaining_units = [u for u in remaining_units if u.part.part_no != part_no]

    if not symmetric_rows:
        return None

    remainder_rows = []
    if remaining_units:
        # tan_half deliberately omitted (0, the default): usable_w/usable_h
        # here are already this rack's own, previously-established margin
        # (see _try_symmetrize_rack), and every remaining unit already
        # satisfied it as part of the original rack — see
        # _repack_items_compact's own docstring for why re-establishing a
        # fresh margin from just the remainder would be wrong.
        remainder_rows = _repack_items_compact(remaining_units, usable_w, usable_h, item_gap, avg_unit_weight)
        if remainder_rows is None:
            return None

    return symmetric_rows + remainder_rows


def _try_symmetrize_rack(
    rows, rack_l: float, rack_h: float, rack_b: float, edge_margin: float, item_gap: float,
    usable_w: float, usable_h: float, avg_unit_weight: float,
):
    """Purely cosmetic post-processing step, run after balance optimization
    but before the final row sort: look for part types appearing multiple
    times within this already rack-assigned, already-balanced set of units
    and, where possible, rearrange them into a mirrored, symmetric row
    instead of wherever they incidentally ended up. Never changes *which*
    units are on this rack (only their in-rack position), so it can never
    affect cross-rack invariants (strict list order, chargenweise batch
    contiguity) — those are untouched by construction, not just re-checked.
    It also never gets to keep a change that would need more space than this
    rack already had, or that would make the balance deviation worse than
    it already was — symmetry is an optional finishing touch here, not a
    hard requirement, so any collision with those simply keeps the
    original, asymmetric-but-already-valid rows.

    _build_symmetrized_rows assembles its candidate from *independent*
    pieces (one row per symmetric group, plus a separately-repacked
    remainder for whatever's left) — each piece is checked against usable_h
    on its own while being built, but nothing checks their *combined*
    total afterward, so pieces that each individually fit can still
    collectively overflow the rack's real height budget. That total is
    checked once here, across the whole candidate, before it's ever
    allowed to replace the original rows."""
    units = _flatten_rows_to_units(rows)
    candidate_rows = _build_symmetrized_rows(units, usable_w, usable_h, item_gap, avg_unit_weight)
    if candidate_rows is None:
        return rows

    candidate_total_height = (
        sum(r["height"] for r in candidate_rows) + item_gap * (len(candidate_rows) - 1)
        if candidate_rows else 0.0
    )
    if candidate_total_height > usable_h + _EPS:
        return rows

    original_rack = _finalize_rack(rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
    original_deviation = _deviation(*original_rack.left_right_weight())

    candidate_rack = _finalize_rack(candidate_rows, 0, rack_l, rack_h, rack_b, edge_margin, item_gap)
    candidate_deviation = _deviation(*candidate_rack.left_right_weight())
    if candidate_deviation > original_deviation + _EPS:
        return rows

    return candidate_rows


def _rack_fill_ratio(rows, usable_w: float, usable_h: float) -> float:
    """Share of the rack's usable footprint area actually covered by placed
    parts (not accounting for item_gap overhead, which is why this is a
    slight underestimate of true space efficiency — good enough as a
    consolidation trigger threshold)."""
    usable_area = usable_w * usable_h
    if usable_area <= _EPS:
        return 1.0
    used_area = sum(w * h for _unit, w, h, _rotated in _iter_row_items(rows))
    return used_area / usable_area


def _consolidate_underfilled_racks(
    all_rows: list, rack_l: float, rack_h: float, rack_b: float, edge_margin: float,
    usable_w: float, usable_h: float, item_gap: float, avg_unit_weight: float,
    adjacent_only: bool, tan_half: float = 0.0,
) -> list:
    """Final pass (frei/chargenweise only, where cross-rack reordering is
    already allowed at all — never for listenreihenfolge): repeatedly look
    for weakly-filled racks (below CONSOLIDATION_FILL_THRESHOLD usable-area
    coverage) whose combined contents fit into a single, denser rack, and
    merge them — freeing up a whole rack that would otherwise sit mostly
    empty (e.g. one rack holding only a single small part, another holding
    many light parts but little footprint).
    Repeated until no further merge is found; a chain of more than two
    racks consolidates naturally across successive passes, since a freshly
    merged rack is itself re-evaluated on the next pass.

    adjacent_only=True ('chargenweise'): only ever merges two racks that are
    immediately neighboring in the *current* sequence, replacing them
    in-place rather than appending the result elsewhere. Collapsing two
    consecutive slots into one can never separate or reorder anything that
    wasn't already adjacent, so this can never break a batch's contiguity
    across racks (once a batch is split, its continuation must be the very
    next rack — no other batch may end up interleaved). This trades away
    some consolidation opportunities (a distant pair of weak racks that
    isn't adjacent is left alone) for that guarantee — Requirement 1
    ("Modusregeln müssen höher priorisiert werden als jede Packing
    Optimierung") makes that the correct tradeoff.

    adjacent_only=False ('frei', which has no order/contiguity constraint at
    all): any pair of weak racks may be merged, prioritizing the weakest
    first; the merged rack is appended at the end of the sequence.

    A merge is accepted purely on whether it fits into one rack — minimal
    rack count outranks balance (Requirement 4), so a merge is never rejected
    for exceeding the balance tolerance. It's still worth a swap/rotation
    rebalancing pass on the merged result where that's free (never grows rack
    count): shifting a unit back out to "the next rack" doesn't apply here,
    there's no such next rack once every unit is already rack-assigned."""
    all_rows = list(all_rows)
    # Each successful merge strictly reduces len(all_rows) by one, so the
    # number of merges is hard-bounded by the starting count — but a merged
    # rack can itself still be "weak" (_rack_fill_ratio ignores item_gap
    # overhead, so it's only an approximation) and get re-selected for
    # another merge on the next pass, and every pass rescans from the start.
    # A long chain of many weak racks can therefore cost multiple full
    # rescans per merge, and each rescan's _repack_items_compact attempts
    # get more expensive as the merged racks grow — a generous but finite
    # cap keeps this pass's cost predictable regardless of how pathological
    # a particular dataset/angle combination turns out to be; consolidation
    # is a density optimization, never required for correctness, so simply
    # stopping early and keeping whatever's been consolidated so far is
    # always safe.
    max_merges = max(4 * len(all_rows), 50)
    merges_done = 0
    # A wall-clock deadline, checked before every single _repack_items_compact
    # attempt (not just after each merge): the *scan* for a candidate pair
    # can itself be expensive (up to O(n) attempts per pass, each on a
    # progressively larger merged unit set as consolidation proceeds), so
    # bounding only the number of successful merges isn't enough to bound
    # wall-clock time on its own — this is the actual hard guarantee against
    # ever hanging, regardless of dataset/angle. Consolidation is a density
    # optimization, never required for correctness, so stopping early and
    # keeping whatever's been consolidated so far is always safe.
    deadline = time.monotonic() + CONSOLIDATION_TIME_BUDGET_SECONDS

    if adjacent_only:
        while merges_done < max_merges:
            n = len(all_rows)
            merge_at = None
            for i in range(n - 1):
                if time.monotonic() >= deadline:
                    return all_rows
                if (
                    _rack_fill_ratio(all_rows[i], usable_w, usable_h) >= CONSOLIDATION_FILL_THRESHOLD
                    and _rack_fill_ratio(all_rows[i + 1], usable_w, usable_h) >= CONSOLIDATION_FILL_THRESHOLD
                ):
                    continue
                units = _flatten_rows_to_units(all_rows[i]) + _flatten_rows_to_units(all_rows[i + 1])
                repacked = _repack_items_compact(units, usable_w, usable_h, item_gap, avg_unit_weight, tan_half)
                if repacked is not None:
                    merge_at = (i, repacked)
                    break
            if merge_at is None:
                return all_rows
            i, repacked = merge_at
            merged_extra_lr, merged_extra_tb = _rack_tilt_margin(repacked, tan_half)
            repacked = _rebalance_rows(
                repacked, rack_l, rack_h, rack_b, edge_margin + merged_extra_tb, item_gap, usable_w - 2 * merged_extra_lr
            )
            all_rows = all_rows[:i] + [repacked] + all_rows[i + 2:]
            merges_done += 1
        return all_rows

    while merges_done < max_merges:
        n = len(all_rows)
        fill = [_rack_fill_ratio(r, usable_w, usable_h) for r in all_rows]
        weak_idx = sorted(
            (i for i in range(n) if fill[i] < CONSOLIDATION_FILL_THRESHOLD),
            key=lambda i: fill[i],
        )

        merge = None
        for pos_a in range(len(weak_idx)):
            for pos_b in range(pos_a + 1, len(weak_idx)):
                if time.monotonic() >= deadline:
                    return all_rows
                a, b = weak_idx[pos_a], weak_idx[pos_b]
                units = _flatten_rows_to_units(all_rows[a]) + _flatten_rows_to_units(all_rows[b])
                repacked = _repack_items_compact(units, usable_w, usable_h, item_gap, avg_unit_weight, tan_half)
                if repacked is not None:
                    merge = (a, b, repacked)
                    break
            if merge is not None:
                break

        if merge is None:
            return all_rows

        a, b, repacked = merge
        merged_extra_lr, merged_extra_tb = _rack_tilt_margin(repacked, tan_half)
        repacked = _rebalance_rows(
            repacked, rack_l, rack_h, rack_b, edge_margin + merged_extra_tb, item_gap, usable_w - 2 * merged_extra_lr
        )
        remaining = [all_rows[i] for i in range(n) if i not in (a, b)]
        remaining.append(repacked)
        all_rows = remaining
        merges_done += 1
    return all_rows


def pack_shelf(
    units: list[Unit],
    rack_l: float,
    rack_h: float,
    rack_b: float,
    edge_margin: float,
    item_gap: float,
    mode: str,
    order_key_fn=None,
    progress_callback=None,
    apply_symmetry: bool = True,
    tan_half: float = 0.0,
) -> list[Rack]:
    """progress_callback(fraction: float), fraction in [0, 1], called after
    each rack is finalized with the share of units placed so far — used as a
    proxy for packing progress since the algorithm itself has no natural
    percentage (variable row/rack counts, data-dependent rebalancing).

    edge_margin: the plain, configured usable-envelope margin along both
    axes — a tilt angle (tan_half > 0) never changes this value itself;
    instead each rack, once its own contents are known, gets its own
    additional per-direction clearance computed from them (see module
    docstring / _establish_rack_margin / _rack_tilt_margin) and added on
    top, individually, right where each step below needs it. Nothing in
    this function (or below it) treats tilt as a single, run-wide
    adjustment to `edge_margin` anymore.

    order_key_fn: must match the ordering `units` was actually built with
    (see BATCH_ORDER_FUNCS) for 'listenreihenfolge'/'chargenweise' — passed in
    explicitly by the caller rather than derived from `mode` here, since
    'chargenweise' tries several different unit orderings and each needs its
    own matching key for the order-preserving shift search.

    apply_symmetry: run the cosmetic post-processing symmetry pass (see
    _try_symmetrize_rack). Callers that only use the *rack count* of a
    pack_shelf() result to compare candidates (chargenweise's per-order
    search and its time/eval-budgeted local search) should pass False — the
    pass never changes rack count, so skipping it there is free, and doing
    it anyway for every throwaway candidate would eat into those budgets for
    no benefit. The one pack_shelf() call whose result is actually returned
    to the user should always leave it enabled (the default)."""
    usable_w = rack_l - 2 * edge_margin
    usable_h = rack_h - 2 * edge_margin

    total_units = len(units)
    avg_unit_weight = sum(u.part.weight for u in units) / total_units if total_units else 0.0
    supplier = (
        _FreeSupplier(units, usable_w, avg_unit_weight, usable_h, tan_half)
        if mode == "frei"
        else _StrictSupplier(units, usable_w, avg_unit_weight, usable_h, tan_half)
    )
    # Only 'frei' (via _FreeSupplier) may let the rescue mechanism search the
    # entire remaining pool for any still-fitting candidate — it has no
    # ordering constraint at all. 'chargenweise' and 'listenreihenfolge' both
    # consume a strictly-ordered queue (batch order / part-no order
    # respectively): a part's rack assignment must never depend on anything
    # but that fixed order, so their rescue may only ever consider the
    # queue's literal head (see _try_rescue_with_repack / _StrictSupplier).
    allow_reorder = mode == "frei"

    all_rows: list = []
    while supplier.has_remaining():
        rows = _build_rack_rows_for_new_rack(
            supplier, usable_w, usable_h, item_gap, avg_unit_weight, allow_reorder=allow_reorder, tan_half=tan_half
        )
        if not rows:
            raise RuntimeError(
                "Packing-Inkonsistenz: konnte kein Bauteil auf einem neuen, leeren "
                "Warenträger platzieren (jede Unit wurde vorab auf Passfähigkeit "
                "geprüft, sollte also immer platzierbar sein)."
            )
        if mode != "frei":
            flat_units = _flatten_rows_to_units(rows)
            repacked = _repack_items_compact(flat_units, usable_w, usable_h, item_gap, avg_unit_weight, tan_half)
            if repacked is not None:
                rows = repacked

        # This rack's own tilt margin (module docstring): established once,
        # from whatever _build_rack_rows/_repack_items_compact's internal
        # _establish_rack_margin call already locked in — recomputed here
        # from the rack's actual final contents (see _rack_tilt_margin) so
        # rebalancing/shifting validate column widths and row heights
        # against the *correct*, already-shrunk envelope, not the plain
        # nominal one (which would let a swap silently widen a row beyond
        # what this rack's own margin allows).
        extra_lr, extra_tb = _rack_tilt_margin(rows, tan_half)
        usable_w_rack = usable_w - 2 * extra_lr
        edge_margin_rack = edge_margin + extra_tb

        rows = _rebalance_rows(rows, rack_l, rack_h, rack_b, edge_margin_rack, item_gap, usable_w_rack)
        rows = _shift_excess_to_next_rack(
            rows, rack_l, rack_h, rack_b, edge_margin_rack, item_gap, usable_w_rack, supplier, order_key_fn
        )
        if not rows:
            # every unit on this "rack" got shifted out (pathological
            # single-dominant-item case) -> nothing to finalize, let the
            # outer loop simply try again with a fresh rack.
            continue

        all_rows.append(rows)
        if progress_callback is not None and total_units > 0:
            processed = total_units - supplier.remaining_count()
            progress_callback(processed / total_units)

    # Final consolidation pass: only 'frei'/'chargenweise' allow cross-rack
    # reordering at all, so only there can weakly-filled racks be merged
    # together after the fact ('listenreihenfolge' must keep each rack's
    # assignment exactly as Phase 1 strictly determined it).
    if mode != "listenreihenfolge":
        all_rows = _consolidate_underfilled_racks(
            all_rows, rack_l, rack_h, rack_b, edge_margin, usable_w, usable_h, item_gap, avg_unit_weight,
            adjacent_only=(mode == "chargenweise"), tan_half=tan_half,
        )

    racks: list[Rack] = []
    for rows in all_rows:
        extra_lr, extra_tb = _rack_tilt_margin(rows, tan_half)
        usable_w_rack = usable_w - 2 * extra_lr
        usable_h_rack = usable_h - 2 * extra_tb
        edge_margin_rack = edge_margin + extra_tb
        # Symmetry pass: purely cosmetic, intra-rack only (never moves a unit
        # to a different rack, never changes which units are on it, so this
        # rack's own already-established margin above is unaffected), so
        # it's safe for every mode including 'listenreihenfolge' — see
        # _try_symmetrize_rack.
        if apply_symmetry:
            rows = _try_symmetrize_rack(
                rows, rack_l, rack_h, rack_b, edge_margin_rack, item_gap, usable_w_rack, usable_h_rack, avg_unit_weight
            )
        rows = sorted(rows, key=_row_sort_key)
        racks.append(_finalize_rack(rows, len(racks), rack_l, rack_h, rack_b, edge_margin_rack, item_gap))
    return racks


def _tile_racks(racks: list[Rack], times: int) -> list[Rack]:
    """`times` independent copies of the same rack layout, re-indexed
    sequentially — a single machine's racks are entirely independent of
    every other machine's, so repeating them is always a valid (if not
    always optimal) solution for the pooled quantity. Used as a
    guaranteed-no-worse fallback for chargenweise's Maschinentrennung
    invariant (see compute_packing).

    Interleaved *by original rack slot* (copy-1-of-rack-0, copy-2-of-
    rack-0, ..., copy-1-of-rack-1, copy-2-of-rack-1, ...) rather than
    concatenated copy-by-copy: the single-machine solution being tiled
    already guarantees each part occupies a contiguous *range* of its own
    original rack indices (chargenweise's own batch-contiguity invariant,
    established recursively by the pack_shelf() call that produced
    `racks`) — interleaving turns each original rack index into an
    adjacent block of `times` new indices with no gaps between
    consecutive blocks, so a contiguous original range maps to a
    contiguous new range too. Concatenating copies end-to-end instead
    would place the same part at new indices spaced `len(racks)` apart
    (one occurrence per copy), which is never contiguous for any part
    appearing in every machine's identical solution — silently violating
    chargenweise's contiguity invariant despite the rack *count* still
    being correct, which is exactly why interleaving is required here and
    not just "either order also works"."""
    tiled: list[Rack] = []
    for rack in racks:
        for _ in range(times):
            tiled.append(Rack(index=len(tiled), rack_l=rack.rack_l, rack_h=rack.rack_h, rack_b=rack.rack_b, placed=list(rack.placed)))
    return tiled




def _pack_chargenweise_full(
    fitting_parts: list[Part], rack_l: float, rack_h: float, rack_b: float, edge_margin: float,
    item_gap: float, machines_per_day: int, pool_daily: bool, part_by_no: dict[str, Part],
    progress_callback=None, tan_half: float = 0.0,
) -> list[Rack]:
    """The complete 'chargenweise' pipeline, including the Maschinentrennung
    guaranteed-floor safety net — factored out of compute_packing so that
    *every* caller (the actual result, and the 0°-baseline comparison used
    by the tilt floor-guarantee below) gets the identical treatment. This
    matters: the tilt floor-guarantee compares the real (tan_half > 0)
    result against a fresh tan_half=0 baseline and falls back to
    re-packing the baseline's own groupings whenever the baseline needs
    fewer racks — if that baseline were computed with a *weaker* pipeline
    (no Maschinentrennung seeding/consolidation) than the real result, it
    would be an unfairly weak baseline, and the real result — now
    genuinely improved by the seeding/consolidation below — could beat it
    even though nothing is actually wrong, spuriously triggering the
    "tilt must never need fewer racks" fallback and making the result
    *worse* than the already-good pipeline produced. Using this same
    function for both keeps the comparison apples-to-apples.

    'ohne Maschinentrennung' is meant to pool the full day's quantity and
    bundle same-type parts across all machines — not quietly fall back to
    solving for one machine and repeating that exact small-scale grouping
    machines_per_day times. So the single-machine solution is computed
    first (only when there's more than one machine to pool at all) and its
    own winning batch order is fed into the pooled search as an extra
    candidate (see _pack_chargenweise's own extra_candidate_orders) —
    giving the pooled search a chance to reach at least that same
    grouping, but packed through this function's own full machinery
    (rescue, consolidation, rebalancing) at the true pooled scale, which
    can consolidate space across what would otherwise be separate
    per-machine chunks in a naive tiling.

    The single-machine result itself is kept as a guaranteed-floor safety
    net: Maschinentrennung must never need fewer racks/day than pooling
    the same quantity (every machine's racks are entirely independent of
    every other's, so tiling one machine's own solution machines_per_day
    times is always at least a valid, if not always optimal, solution for
    the pooled quantity — a guaranteed floor for how well the pooled
    search "should" do).

    KNOWN LIMITATION (measured during development, not just
    theoretical): seeding never makes the pooled
    result worse than before, and improves it in some cases, but does NOT
    reliably reduce how *often* this floor still wins outright — on a
    representative parameter/angle sweep it remained the deciding factor
    in roughly a third of chargenweise-ohne-Maschinentrennung runs, both
    before and after seeding was added (a larger local-search time budget
    was also tried and confirmed not to close this gap — the single-
    machine floor being compared against improves right along with the
    pooled search). This is entirely independent of tan_half/neigungswinkel
    — the condition above (`if pool_daily and machines_per_day > 1`) never
    looks at tan_half at all, so seeding and this same limitation apply
    identically at neigungswinkel_grad=0 as at any other angle; it was
    only ever found and characterized during the tilt-angle work, not
    caused or gated by it. When this floor does win, the fallback is the plain,
    literal tiling (_tile_racks): the same part-type combination can then
    show up identically machines_per_day times in the final result,
    instead of being genuinely bundled. A fix exists in principle
    (consolidating *across* the tiled sequence instead of using it as-is)
    but was prototyped and rejected: it could silently drop units and
    violate the batch-contiguity guarantee in some cases, because an
    already-valid rack's units are not always reproducible by a fresh
    repack (the greedy row-builder's outcome depends on the order items
    are offered in, and no single reordering strategy tried was guaranteed
    to replicate every valid arrangement). Given the choice, a known-
    correct fallback that is sometimes suboptimal was kept over an
    unverified "denser but occasionally broken" one — deliberately, not an
    oversight. All other guarantees (rack count vs. Maschinentrennung,
    strict/batch order, left-right balance) are unaffected by this."""
    single_machine_racks: list[Rack] | None = None
    extra_candidate_orders = None
    if pool_daily and machines_per_day > 1:
        single_machine_racks, single_machine_order = _pack_chargenweise(
            fitting_parts, rack_l, rack_h, rack_b, edge_margin, item_gap, machines_per_day,
            False, None, tan_half,
        )
        if single_machine_order is not None:
            extra_candidate_orders = [single_machine_order]

    racks, _ = _pack_chargenweise(
        fitting_parts, rack_l, rack_h, rack_b, edge_margin, item_gap, machines_per_day,
        pool_daily, progress_callback, tan_half, extra_candidate_orders,
    )

    if single_machine_racks is not None and len(single_machine_racks) * machines_per_day < len(racks):
        racks = _tile_racks(single_machine_racks, machines_per_day)

    return racks


def _repack_racks_under_tilt(
    racks_baseline: list[Rack], part_by_no: dict[str, Part], rack_l: float, rack_h: float, rack_b: float,
    edge_margin: float, item_gap: float, mode: str, tan_half: float,
) -> list[Rack]:
    """Guaranteed floor for the tilt-angle rack count: re-pack each of
    racks_baseline's own unit groupings (computed at tan_half=0) under
    tan_half instead, letting any rack whose contents no longer fit split
    into however many it now needs (via a fresh, self-contained pack_shelf
    call scoped to just that group's units). Each original rack contributes
    at least one rack to the result, so the total can never be fewer than
    len(racks_baseline) — used only as a fallback when the heuristic search
    under tilt (which has no such guarantee relative to the baseline) does
    worse than this floor, mirroring the Maschinentrennung tiling
    fallback's philosophy: a per-rack tilt margin can make one arrangement's
    *relative* cost shift against another's, and a greedy search can
    occasionally lock onto a worse one for that reason alone — this
    converts "should never need fewer racks with tilt than without" from an
    empirical, search-dependent property into a structural guarantee.

    order_key_fn is deliberately not threaded through here: 'chargenweise'
    permits *any* batch order, and 'listenreihenfolge' requires exactly
    part-no order — _part_sort_key satisfies both (a part-no-sorted queue is
    trivially a valid batch order too), so a single ordering choice is
    enough regardless of which of the two modes this is.

    For 'chargenweise'/'listenreihenfolge', consecutive baseline racks that
    share a common part type (i.e. baseline itself already split that
    type's batch across them, continuing on the very next rack — the one
    legitimate split case) are grouped into one combined re-pack instead of
    being re-packed independently: a finalized rack's own placed units are
    ordered by row/balance (_row_sort_key), not grouped by type, and
    processing each baseline rack as an independent, self-contained
    _StrictSupplier queue would let a type's own units — regrouped by
    part_no only *within* that one rack — end up needing a *different*
    split point once the enlarged tilt margin no longer matches baseline's
    own 0° split, scattering that type across non-adjacent output
    positions and silently breaking the batch-contiguity invariant. Since
    each such baseline rack was already close to space-limited at the
    larger, untilted margin (that's why it needed to split there in the
    first place), re-packing the combined group under the stricter tilted
    margin can be expected to still need at least as many racks as the
    group's own baseline count — 'frei' needs none of this (no contiguity
    requirement to protect), so its baseline racks stay independent, exactly
    as before."""
    order_key_fn = _part_sort_key if mode in ("chargenweise", "listenreihenfolge") else None
    runs: list[list[Rack]] = []
    for rack in racks_baseline:
        if runs and mode != "frei":
            prev_part_nos = {p.part_no for p in runs[-1][-1].placed}
            cur_part_nos = {p.part_no for p in rack.placed}
            if prev_part_nos & cur_part_nos:
                runs[-1].append(rack)
                continue
        runs.append([rack])

    result: list[Rack] = []
    for run in runs:
        # Sorted by part_no, NOT left as rack.placed's own physical
        # placement order — see the docstring above.
        units = sorted(
            (Unit(part=part_by_no[p.part_no], unit_index=0) for rack in run for p in rack.placed),
            key=lambda u: _part_sort_key(u.part),
        )
        sub_racks = pack_shelf(
            units, rack_l, rack_h, rack_b, edge_margin, item_gap, mode,
            order_key_fn=order_key_fn, tan_half=tan_half,
        )
        result.extend(sub_racks)
    return [
        Rack(index=i, rack_l=r.rack_l, rack_h=r.rack_h, rack_b=r.rack_b, placed=r.placed)
        for i, r in enumerate(result)
    ]


def _pack_chargenweise(
    fitting_parts: list[Part], rack_l: float, rack_h: float, rack_b: float, edge_margin: float,
    item_gap: float, machines_per_day: int, pool_daily: bool, progress_callback=None, tan_half: float = 0.0,
    extra_candidate_orders: list[list[Part]] | None = None,
) -> tuple[list[Rack], list[Part] | None]:
    """Batch order is explicitly free to choose for this mode, so a single
    fixed heuristic can't be assumed best — try several diverse candidate
    orderings (including plain 'part_no', which is itself a valid batch
    order) and keep whichever needs fewest racks. This is also what
    *guarantees* 'chargenweise' never needs more racks than
    'listenreihenfolge': the latter is pinned to exactly the 'part_no'
    candidate, which 'chargenweise' always considers too. Refine the best
    heuristic further with a time-boxed pairwise-swap local search (same
    spirit as the existing balance swap mechanism, applied to batch order
    instead of weight) — bounded by wall-clock time rather than batch count,
    so it always contributes at least a partial improvement pass even for a
    large, many-part-type list.

    extra_candidate_orders: additional pre-ordered part lists tried
    alongside the standard named candidates above — used by compute_packing
    to seed the full pooled (Maschinentrennung=False) search with whichever
    batch order won for a single machine's own quantity. That order, tried
    again at the full pooled scale with this function's own rescue/
    consolidation/local-search machinery (not just replicated as-is), very
    often matches or beats a naive N-times tiling of the single-machine
    result: tiling can only ever repeat the exact same small-scale grouping
    N times, never consolidate space across what would otherwise be
    separate per-machine chunks — see compute_packing's own comments on why
    this is what keeps that tiling a rare safety-net floor rather than the
    routine outcome.

    Returns (racks, the winning ordered_parts) — the order is exposed so a
    caller can itself use it as an extra_candidate_orders seed for another
    _pack_chargenweise call, as described above."""
    best_racks: list[Rack] | None = None
    best_ordered_parts: list[Part] | None = None
    named_candidates = [(name, BATCH_ORDER_FUNCS[name](fitting_parts)) for name in CHARGENWEISE_CANDIDATE_ORDERS]
    seeded_candidates = [(f"seed_{i}", ordered) for i, ordered in enumerate(extra_candidate_orders or [])]
    all_candidates = named_candidates + seeded_candidates
    n_candidates = len(all_candidates)
    for i, (order_name, ordered_parts) in enumerate(all_candidates):
        units = _expand_units_from_ordered_parts(ordered_parts, machines_per_day, pool_daily)
        if not units:
            continue
        candidate_racks = pack_shelf(
            units, rack_l, rack_h, rack_b, edge_margin, item_gap, "chargenweise",
            order_key_fn=_rank_key_fn(ordered_parts), apply_symmetry=False, tan_half=tan_half,
        )
        if best_racks is None or len(candidate_racks) < len(best_racks):
            best_racks = candidate_racks
            best_ordered_parts = ordered_parts
        if progress_callback is not None:
            progress_callback(0.1 + 0.7 * (i + 1) / n_candidates, f"Chargenweise: Variante '{order_name}' geprüft")

    if best_ordered_parts is not None:
        if progress_callback is not None:
            progress_callback(0.85, "Chargenweise: lokale Reihenfolge-Suche")
        refined_order, refined_count = _local_search_batch_swaps(
            best_ordered_parts, len(best_racks), machines_per_day, pool_daily,
            rack_l, rack_h, rack_b, edge_margin, item_gap, "chargenweise",
            CHARGENWEISE_LOCAL_SEARCH_MIN_EVAL_COUNT, CHARGENWEISE_LOCAL_SEARCH_SAFETY_TIME_BUDGET_SECONDS,
            tan_half,
        )
        final_ordered_parts = refined_order if refined_count < len(best_racks) else best_ordered_parts
        # One final pass with the symmetry post-processing enabled — every
        # search/evaluation call above deliberately skipped it (purely
        # cosmetic and rack-count-neutral, so it would only have wasted
        # the local-search time/eval budget — see pack_shelf).
        units = _expand_units_from_ordered_parts(final_ordered_parts, machines_per_day, pool_daily)
        best_racks = pack_shelf(
            units, rack_l, rack_h, rack_b, edge_margin, item_gap, "chargenweise",
            order_key_fn=_rank_key_fn(final_ordered_parts), tan_half=tan_half,
        )
        best_ordered_parts = final_ordered_parts

    return (best_racks if best_racks is not None else []), best_ordered_parts


def compute_packing(
    parts: list[Part],
    rack_l: float,
    rack_h: float,
    rack_b: float,
    item_gap: float,
    edge_margin: float,
    maschinen_pro_tag: int,
    reihenfolge_modus: str = "frei",
    maschinentrennung: bool = False,
    neigungswinkel_grad: float = 0.0,
    progress_callback=None,
) -> PackingResult:
    """
    item_gap: Mindestabstand zwischen zwei benachbarten Bauteilen (horizontal
      innerhalb einer Reihe und vertikal zwischen Reihen bzw. gestapelten
      Bauteilen innerhalb einer Spalte) — bleibt exakt der eingestellte Wert,
      auch bei aktivem Neigungswinkel (siehe neigungswinkel_grad).
    edge_margin: Mindestabstand zwischen einem Bauteil und dem äußeren Rand des
      Warenträgers; verkleinert die nutzbare Fläche in L, H und B symmetrisch
      — vor dem eigentlichen Packvorgang ggf. um den Neigungswinkel-Zuschlag
      vergrößert (siehe neigungswinkel_grad).
    reihenfolge_modus: 'frei' (Reihenfolge/Gruppierung komplett aufgelöst, beste
      Kombination gesucht — Standard), 'chargenweise' (gleiche Bauteile bleiben als
      Charge zusammen, Chargen-Reihenfolge frei), oder 'listenreihenfolge' (strikt
      Part-No.-aufsteigend für die Trägerzuordnung; innerhalb eines einmal
      zugeordneten Trägers wird trotzdem frei/dicht nachgepackt).
    maschinentrennung: False (Standard) = komplette Tagesmenge (Stückzahl je
      Maschine × maschinen_pro_tag) wird als eine gemeinsame Menge gepackt. True =
      es wird nur für eine einzelne Maschine gepackt (Ausfallsicherheit), das
      Ergebnis (Anzahl Warenträger) wird anschließend mit maschinen_pro_tag
      multipliziert.
    neigungswinkel_grad: gewünschter Neigungswinkel (Grad, Standard 0) beim
      Lackieren — die eigentliche Rechteckgeometrie/Darstellung bleibt
      unverändert, und der Abstand zwischen zwei Bauteilen bleibt ebenfalls
      unverändert bei item_gap (zwei gleich geneigte Nachbarteile ändern
      ihren gegenseitigen Abstand dadurch nicht). Stattdessen bekommt jeder
      Warenträger individuell, anhand seines eigenen Inhalts, einen
      zusätzlichen Sicherheitsabstand zum Trägerrand: links/rechts die
      größte Bauteil-Höhe dieses Trägers x tan(Winkel/2), oben/unten die
      größte Bauteil-Breite dieses Trägers x tan(Winkel/2) (exakte
      Sehnenformel für eine Drehung um den eigenen Bauteilmittelpunkt) —
      jeweils nur, wenn die betreffende Dimension über
      TILT_MARGIN_MIN_DIMENSION_MM liegt, sonst 0 für diesen Träger und
      diese Richtung. Kein künstlicher Deckel. Die genaue Mechanik (wie
      dieser Zuschlag ohne rückwirkendes Entfernen bereits platzierter
      Bauteile ermittelt wird) steht im Modul-Docstring.
    progress_callback: optionales callable(fraction: float, label: str), fraction
      in [0, 1], aufgerufen an Zwischenschritten (Vorverarbeitung ~0.1, dann
      proportional zur Anzahl bereits platzierter Bauteile bis ~0.9 — der
      Packing-/Balance-Algorithmus selbst hat keinen exakt planbaren
      Prozentverlauf, daher die Näherung über den Bauteil-Fortschritt).
    """
    if reihenfolge_modus not in REIHENFOLGE_MODI:
        raise ValueError(f"Unbekannter reihenfolge_modus: {reihenfolge_modus!r}")

    machines_per_day = maschinen_pro_tag
    pool_daily = not maschinentrennung

    issues: list[Issue] = []

    # Depth vs. usable rack B: non-fatal, independent of the L/H fit check,
    # and independent of the tilt margin (which only ever affects L/H).
    usable_b = rack_b - 2 * edge_margin
    for part in parts:
        if usable_b > _EPS and part.depth > usable_b:
            issues.append(
                Issue(
                    "warning",
                    f"Bauteil {part.part_no}: kleinste Seite ({part.depth:g} mm) "
                    f"passt nicht in die nutzbare Trägertiefe B ({usable_b:g} mm "
                    f"nach Abzug des Randabstands).",
                    part.part_no,
                )
            )

    tan_half = math.tan(math.radians(neigungswinkel_grad) / 2.0) if neigungswinkel_grad > _EPS else 0.0

    usable_l_nominal = rack_l - 2 * edge_margin
    usable_h_nominal = rack_h - 2 * edge_margin
    if usable_l_nominal <= _EPS or usable_h_nominal <= _EPS or usable_b <= _EPS:
        issues.append(
            Issue("error", "Der Randabstand ist zu groß für die angegebenen Trägermaße L/H/B.")
        )
        return PackingResult([], 0, issues, machines_per_day, pool_daily)

    # L×H fit exclusion (fatal for the affected part, rest continues) —
    # each part checked as the worst-case sole occupant of its own fresh
    # rack, at the exact same per-rack formula/threshold used everywhere
    # else (module docstring): a part that provably can't fit any rack on
    # its own, tilt margin included, is excluded up front.
    fitting_parts: list[Part] = []
    unfit_part_nos: list[str] = []
    for part in parts:
        if fits_rack(part.dim_a, part.dim_b, edge_margin, rack_l, rack_h, tan_half):
            fitting_parts.append(part)
        else:
            unfit_part_nos.append(part.part_no)
    if unfit_part_nos:
        issues.append(
            Issue(
                "error",
                "Folgende Bauteile passen auch mit Rotation nicht auf den Warenträger "
                f"und wurden ausgeschlossen: {', '.join(unfit_part_nos)}",
            )
        )

    if not fitting_parts:
        return PackingResult([], 0, issues, machines_per_day, pool_daily)

    part_by_no = {p.part_no: p for p in fitting_parts}

    if progress_callback is not None:
        progress_callback(0.1, "Vorverarbeitung abgeschlossen")

    if reihenfolge_modus == "chargenweise":
        racks = _pack_chargenweise_full(
            fitting_parts, rack_l, rack_h, rack_b, edge_margin, item_gap, machines_per_day, pool_daily,
            part_by_no, progress_callback, tan_half,
        )
    else:
        batch_order = "part_no"
        units = expand_units(fitting_parts, machines_per_day, pool_daily, batch_order)
        if not units:
            return PackingResult([], 0, issues, machines_per_day, pool_daily)

        order_key_fn = _part_sort_key if reihenfolge_modus == "listenreihenfolge" else None

        def _packing_progress(fraction: float) -> None:
            if progress_callback is not None:
                progress_callback(0.1 + 0.8 * fraction, "Packing-Durchlauf & Balance-Optimierung")

        racks = pack_shelf(
            units, rack_l, rack_h, rack_b, edge_margin, item_gap, reihenfolge_modus,
            order_key_fn=order_key_fn, progress_callback=_packing_progress, tan_half=tan_half,
        )

    # Neigungswinkel must never need fewer racks than the same run at 0°
    # FOR THE SAME SET OF PARTS: even though each rack's own tilt margin is
    # guaranteed by construction to never grow after the fact (module
    # docstring), a heuristic search can still, in principle, occasionally
    # do very slightly better with a smaller usable envelope purely by
    # chance of which greedy choice that happens to favor. Detected by
    # comparing against a fresh tan_half=0 run of the *same* fitting_parts
    # (deliberately not a completely fresh 0°-equivalent exclusion filter:
    # if the tilt angle newly excludes a part that fit at 0° — reported
    # separately via unfit_part_nos above — packing strictly *less*
    # material can legitimately finish in fewer racks than the full 0°
    # problem, which isn't the "search got unlucky" case this guarantee
    # exists to catch, and re-packing an excluded part under a margin it
    # doesn't fit would be impossible anyway). If the actual (tilt-angle)
    # result is worse than that same-parts baseline, fall back to
    # re-packing the baseline's own rack groupings under tilt instead
    # (_repack_racks_under_tilt) — a structural, never-worse-than-baseline
    # guarantee, the same way the Maschinentrennung tiling fallback works.
    if tan_half > _EPS:
        if reihenfolge_modus == "chargenweise":
            racks_baseline = _pack_chargenweise_full(
                fitting_parts, rack_l, rack_h, rack_b, edge_margin, item_gap, machines_per_day, pool_daily,
                part_by_no,
            )
        else:
            baseline_order_key_fn = _part_sort_key if reihenfolge_modus == "listenreihenfolge" else None
            baseline_units = expand_units(fitting_parts, machines_per_day, pool_daily, "part_no")
            racks_baseline = (
                pack_shelf(
                    baseline_units, rack_l, rack_h, rack_b, edge_margin, item_gap, reihenfolge_modus,
                    order_key_fn=baseline_order_key_fn,
                )
                if baseline_units else []
            )
        if len(racks_baseline) > len(racks):
            racks = _repack_racks_under_tilt(
                racks_baseline, part_by_no, rack_l, rack_h, rack_b, edge_margin, item_gap,
                reihenfolge_modus, tan_half,
            )

    # Neigungswinkel info message: report the per-rack range of tilt
    # margin actually applied across the final result (module docstring —
    # each rack computes its own, so unlike earlier models this is a range,
    # not a single constant).
    if tan_half > _EPS and racks:
        margins_per_rack = [rack_tilt_extra_margin(rack, neigungswinkel_grad) for rack in racks]
        max_lr = max((lr for lr, _tb in margins_per_rack), default=0.0)
        max_tb = max((tb for _lr, tb in margins_per_rack), default=0.0)
        if max_lr > _EPS or max_tb > _EPS:
            issues.append(
                Issue(
                    "info",
                    f"Neigungswinkel {neigungswinkel_grad:g}°: jeder Warenträger erhält individuell, "
                    f"anhand seines eigenen Inhalts, einen zusätzlichen Sicherheitsabstand zum "
                    f"Trägerrand (nur wenn dessen größte Bauteil-Höhe bzw. -Breite über "
                    f"{TILT_MARGIN_MIN_DIMENSION_MM:g} mm liegt) — über alle Warenträger höchstens "
                    f"{max_lr:.1f} mm links/rechts und {max_tb:.1f} mm oben/unten (zusätzlich zum "
                    f"eingestellten Randabstand von {edge_margin:g} mm).",
                )
            )

    for rack in racks:
        left, right = rack.left_right_weight()
        if _deviation(left, right) > BALANCE_TOLERANCE:
            issues.append(
                Issue(
                    "warning",
                    f"Warenträger {rack.index + 1}: Gewichtsungleichgewicht links/rechts "
                    f"({left:.1f} kg / {right:.1f} kg) überschreitet die {int(BALANCE_TOLERANCE * 100)}%-Toleranz.",
                )
            )

    # Every instance of a part type must hang in its one fixed orientation
    # (_fixed_rotation_for_part) — the only exception is a single unit that
    # provably didn't fit any other way (see _choose_orientation's
    # fallback). Detecting that after the fact, by comparing what actually
    # got placed against the rule, keeps the warning trustworthy regardless
    # of which internal code path (normal row, dominant/centered row,
    # symmetry pass, rescue, consolidation, ...) ended up placing a unit.
    deviated_part_nos: set[str] = set()
    for rack in racks:
        for placed in rack.placed:
            part = part_by_no.get(placed.part_no)
            if part is None:
                continue
            if placed.rotated != _fixed_rotation_for_part(part, usable_l_nominal, usable_h_nominal, tan_half):
                deviated_part_nos.add(placed.part_no)
    for part_no in sorted(deviated_part_nos, key=part_no_sort_key):
        issues.append(
            Issue(
                "warning",
                f"Bauteil {part_no} musste abweichend von der Standardausrichtung gedreht "
                "werden, da sonst keine Platzierung möglich war.",
                part_no,
            )
        )

    if progress_callback is not None:
        progress_callback(0.9, "Warenträger fertiggestellt")

    rack_count_display = len(racks) if pool_daily else len(racks) * machines_per_day

    return PackingResult(
        racks=racks,
        rack_count_display=rack_count_display,
        issues=issues,
        machines_per_day=machines_per_day,
        pooled_daily=pool_daily,
    )
