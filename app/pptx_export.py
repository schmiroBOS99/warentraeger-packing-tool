"""PowerPoint export of the per-rack layouts as native, individually
selectable/movable/resizable shapes (one slide per Warenträger) — deliberately
not a picture of the matplotlib preview, so the result stays editable in
PowerPoint itself."""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.models import PackingResult, Rack, RunParams
from app.packing import rack_tilt_extra_margin

_SLIDE_W_IN = 13.333
_SLIDE_H_IN = 7.5
_MARGIN_IN = 0.5
_TOP_IN = 1.7  # leaves room below the title placeholder, even for a wrapped 2-line title
_BORDER_COLOR = RGBColor(0x00, 0x00, 0x00)
_PART_LINE_COLOR = RGBColor(0x33, 0x33, 0x33)
_AXIS_COLOR = RGBColor(0x44, 0x44, 0x44)
_MARGIN_GUIDE_COLOR = RGBColor(0x99, 0x99, 0x99)
_DEFAULT_FILL = "#cccccc"

_TITLE_FONT_PT = 20.0
_SUBTITLE_FONT_PT = 13.0
_SUBTITLE_COLOR = RGBColor(0x55, 0x55, 0x55)

# Cover/overview slide.
_COVER_TITLE_FONT_PT = 26.0
_COVER_HEADER_FONT_PT = 14.0
_COVER_BODY_FONT_PT = 12.0
_COVER_TABLE_FONT_PT = 9.0
_COVER_MAX_WARNINGS_SHOWN = 6
_COVER_CONTENT_TOP_IN = 1.6
_COVER_LEFT_COL_WIDTH_IN = 5.6
_COVER_COL_GAP_IN = 0.3
_COVER_TABLE_ROW_H_IN = 0.24

_MIN_FONT_PT = 6.0
_MAX_FONT_PT = 16.0
_CHAR_WIDTH_FACTOR = 0.55  # rough average glyph width as a fraction of font size
_LINE_HEIGHT_FACTOR = 1.25
_FONT_STEP_PT = 0.5

# Axis scale along the border's bottom (L) and left (H) edges.
_AXIS_NICE_STEPS = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000]
_AXIS_TARGET_TICKS = 8
_AXIS_TICK_LEN_IN = 0.07
_AXIS_FONT_PT = 7.0
_AXIS_LEFT_RESERVE_IN = 0.5  # space left of the border for the H-axis tick labels
_AXIS_BOTTOM_RESERVE_IN = 0.3  # space below the border for the L-axis tick labels
_AXIS_LABEL_W_L_IN = 0.6
_AXIS_LABEL_H_L_IN = 0.16
_AXIS_LABEL_W_H_IN = 0.42
_AXIS_LABEL_H_H_IN = 0.16

# External ("outside the rectangle") dimension labels for parts too small to
# hold readable text themselves.
_EXT_LEADER_LEN_IN = 0.12
_EXT_LABEL_W_IN = 0.95
_EXT_LABEL_LINE_H_IN = 0.14
_EXT_FONT_PT = 7.0
_EXT_STAGGER_IN = 0.04
_EXT_STAGGER_ATTEMPTS = 8

# Dimension-line annotations (technical-drawing style: a line with small
# perpendicular end ticks and a centered mm value) showing the tilt-enlarged
# edge margin / the regular item gap directly in the drawing, replacing the
# old textual "effektiv"-summary line under the title.
_DIM_LINE_COLOR = RGBColor(0xC0, 0x00, 0x00)
_DIM_TICK_LEN_IN = 0.06
_DIM_FONT_PT = 8.0
_DIM_LABEL_W_IN = 0.6
_DIM_LABEL_H_IN = 0.16
_DIM_MIN_LEN_IN = 0.12  # shorter than this on the slide, a label wouldn't read cleanly -- skip rather than clutter


def _hex_to_rgbcolor(hex_str: str) -> RGBColor:
    hex_str = hex_str.lstrip("#")
    return RGBColor(int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))


def _lines_fit(lines: list[str], font_pt: float, box_w_pt: float, box_h_pt: float) -> bool:
    line_h = font_pt * _LINE_HEIGHT_FACTOR
    if line_h * len(lines) > box_h_pt * 0.92:
        return False
    widest = max(len(line) for line in lines)
    if widest * font_pt * _CHAR_WIDTH_FACTOR > box_w_pt * 0.92:
        return False
    return True


def _best_fitting_size(lines: list[str], box_w_in: float, box_h_in: float) -> float | None:
    """Largest font size (down to _MIN_FONT_PT) at which `lines` fit inside a
    box_w_in x box_h_in area, or None if not even _MIN_FONT_PT fits."""
    box_w_pt = box_w_in * 72
    box_h_pt = box_h_in * 72
    size = max(_MIN_FONT_PT, min(_MAX_FONT_PT, min(box_w_pt, box_h_pt) * 0.42))
    while size >= _MIN_FONT_PT:
        if _lines_fit(lines, size, box_w_pt, box_h_pt):
            return size
        size -= _FONT_STEP_PT
    return None


def _set_label(text_frame, lines: list[str], font_pt: float, align) -> None:
    text_frame.word_wrap = False
    text_frame.auto_size = MSO_AUTO_SIZE.NONE
    text_frame.margin_left = text_frame.margin_right = text_frame.margin_top = text_frame.margin_bottom = 0
    text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    for i, line in enumerate(lines):
        p = text_frame.paragraphs[0] if i == 0 else text_frame.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        run.font.size = Pt(font_pt)
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)


def _boxes_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2


def _place_external_label(
    slide, anchor_x_in: float, anchor_y_in: float, lines: list[str],
    right_edge_limit_in: float, placed_boxes: list,
) -> None:
    """A small text label connected to (anchor_x_in, anchor_y_in) — the edge
    of a part rectangle — by a thin leader line, like a dimension callout in
    a technical drawing. Used when a part's own rectangle is too small to
    hold its label. Prefers sitting to the right of the part, flips to the
    left if that would run past the slide's own margin, and staggers
    downward against any already-placed external label on this slide so
    several small nearby parts don't overlap each other."""
    label_h_in = _EXT_LABEL_LINE_H_IN * len(lines)

    if anchor_x_in + _EXT_LEADER_LEN_IN + _EXT_LABEL_W_IN <= right_edge_limit_in:
        leader_x2_in = anchor_x_in + _EXT_LEADER_LEN_IN
        label_left_in = leader_x2_in
        align = PP_ALIGN.LEFT
    else:
        leader_x2_in = anchor_x_in - _EXT_LEADER_LEN_IN
        label_left_in = leader_x2_in - _EXT_LABEL_W_IN
        align = PP_ALIGN.RIGHT

    label_top_in = anchor_y_in - label_h_in / 2
    candidate = (label_left_in, label_top_in, label_left_in + _EXT_LABEL_W_IN, label_top_in + label_h_in)
    for _attempt in range(_EXT_STAGGER_ATTEMPTS):
        if not any(_boxes_overlap(candidate, b) for b in placed_boxes):
            break
        label_top_in += label_h_in + _EXT_STAGGER_IN
        candidate = (label_left_in, label_top_in, label_left_in + _EXT_LABEL_W_IN, label_top_in + label_h_in)
    placed_boxes.append(candidate)

    leader_y2_in = label_top_in + label_h_in / 2
    connector = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(anchor_x_in), Inches(anchor_y_in), Inches(leader_x2_in), Inches(leader_y2_in)
    )
    connector.line.color.rgb = _PART_LINE_COLOR
    connector.line.width = Pt(0.5)

    box = slide.shapes.add_textbox(Inches(label_left_in), Inches(label_top_in), Inches(_EXT_LABEL_W_IN), Inches(label_h_in))
    _set_label(box.text_frame, lines, _EXT_FONT_PT, align)


def _lock_aspect_ratio(shape) -> None:
    """Sets noChangeAspect="1" on the shape so dragging a corner handle in
    PowerPoint keeps its own width/height ratio — deliberately the *only*
    lock applied: a full size lock (noResize) isn't reliably enforceable in
    a normal, unprotected .pptx anyway, and shapes should otherwise stay
    fully movable/resizable, not grouped, so every part remains individually
    editable."""
    cNvSpPr = shape._element.nvSpPr.cNvSpPr
    spLocks = cNvSpPr.find(qn("a:spLocks"))
    if spLocks is None:
        spLocks = cNvSpPr.makeelement(qn("a:spLocks"), {})
        cNvSpPr.insert(0, spLocks)
    spLocks.set("noChangeAspect", "1")


def _add_part_shape(
    slide, placed, offset_x_in: float, offset_y_in: float, scale: float, rack_h: float,
    color_map: dict[str, str], right_edge_limit_in: float, placed_boxes: list,
) -> None:
    box_w_in = placed.w * scale
    box_h_in = placed.h * scale
    left_in = offset_x_in + placed.x * scale
    # mm-space y grows upward from the rack's bottom edge; slide-space y grows
    # downward from the top, so the part's slide-top is the rack's top-edge
    # distance to the part's own top edge.
    top_in = offset_y_in + (rack_h - placed.y - placed.h) * scale

    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left_in), Inches(top_in), Inches(box_w_in), Inches(box_h_in)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _hex_to_rgbcolor(color_map.get(placed.part_no, _DEFAULT_FILL))
    shape.line.color.rgb = _PART_LINE_COLOR
    shape.line.width = Pt(0.75)
    shape.shadow.inherit = False
    _lock_aspect_ratio(shape)

    part_no = placed.part_no
    dims_text = f"{placed.w:g} x {placed.h:g} mm"
    anchor = (left_in + box_w_in, top_in + box_h_in / 2)

    two_line_size = _best_fitting_size([part_no, dims_text], box_w_in, box_h_in)
    if two_line_size is not None:
        _set_label(shape.text_frame, [part_no, dims_text], two_line_size, PP_ALIGN.CENTER)
        return

    single_size = _best_fitting_size([part_no], box_w_in, box_h_in)
    if single_size is not None:
        _set_label(shape.text_frame, [part_no], single_size, PP_ALIGN.CENTER)
        _place_external_label(slide, anchor[0], anchor[1], [dims_text], right_edge_limit_in, placed_boxes)
    else:
        _place_external_label(slide, anchor[0], anchor[1], [part_no, dims_text], right_edge_limit_in, placed_boxes)


def _add_rack_border(slide, offset_x_in: float, offset_y_in: float, w_in: float, h_in: float) -> None:
    border = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(offset_x_in), Inches(offset_y_in), Inches(w_in), Inches(h_in))
    border.fill.background()
    border.line.color.rgb = _BORDER_COLOR
    border.line.width = Pt(1.5)
    border.shadow.inherit = False


def _add_margin_guide(slide, border_x_in: float, border_y_in: float, drawing_w_in: float, drawing_h_in: float, scale: float, edge_margin: float) -> None:
    """Dashed inner rectangle marking the usable area after the edge margin
    is subtracted from the full rack outline — the region parts may actually
    be placed in."""
    if edge_margin <= 1e-9:
        return
    inset_in = edge_margin * scale
    w_in = drawing_w_in - 2 * inset_in
    h_in = drawing_h_in - 2 * inset_in
    if w_in <= 1e-6 or h_in <= 1e-6:
        return
    guide = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(border_x_in + inset_in), Inches(border_y_in + inset_in), Inches(w_in), Inches(h_in)
    )
    guide.fill.background()
    guide.line.color.rgb = _MARGIN_GUIDE_COLOR
    guide.line.width = Pt(1.0)
    guide.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    guide.shadow.inherit = False


def _pick_axis_step(length_mm: float) -> float:
    if length_mm <= 0:
        return _AXIS_NICE_STEPS[0]
    raw_step = length_mm / _AXIS_TARGET_TICKS
    for step in _AXIS_NICE_STEPS:
        if step >= raw_step:
            return step
    return _AXIS_NICE_STEPS[-1]


def _axis_ticks(length_mm: float, step: float) -> list[float]:
    ticks = []
    v = 0.0
    while v < length_mm - 1e-6:
        ticks.append(v)
        v += step
    if not ticks or abs(ticks[-1] - length_mm) > 1e-6:
        ticks.append(length_mm)
    return ticks


def _add_l_axis(slide, border_x_in: float, border_y_in: float, drawing_w_in: float, drawing_h_in: float, scale: float, rack_l: float) -> None:
    bottom_y_in = border_y_in + drawing_h_in
    for tv in _axis_ticks(rack_l, _pick_axis_step(rack_l)):
        x_in = border_x_in + tv * scale
        conn = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, Inches(x_in), Inches(bottom_y_in), Inches(x_in), Inches(bottom_y_in + _AXIS_TICK_LEN_IN)
        )
        conn.line.color.rgb = _AXIS_COLOR
        conn.line.width = Pt(0.75)

        label_left_in = x_in - _AXIS_LABEL_W_L_IN / 2
        label_top_in = bottom_y_in + _AXIS_TICK_LEN_IN
        box = slide.shapes.add_textbox(Inches(label_left_in), Inches(label_top_in), Inches(_AXIS_LABEL_W_L_IN), Inches(_AXIS_LABEL_H_L_IN))
        _set_label(box.text_frame, [f"{tv:g}"], _AXIS_FONT_PT, PP_ALIGN.CENTER)


def _add_h_axis(slide, border_x_in: float, border_y_in: float, scale: float, rack_h: float) -> None:
    for tv in _axis_ticks(rack_h, _pick_axis_step(rack_h)):
        y_in = border_y_in + (rack_h - tv) * scale
        conn = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, Inches(border_x_in - _AXIS_TICK_LEN_IN), Inches(y_in), Inches(border_x_in), Inches(y_in)
        )
        conn.line.color.rgb = _AXIS_COLOR
        conn.line.width = Pt(0.75)

        label_left_in = border_x_in - _AXIS_TICK_LEN_IN - _AXIS_LABEL_W_H_IN
        label_top_in = y_in - _AXIS_LABEL_H_H_IN / 2
        box = slide.shapes.add_textbox(Inches(label_left_in), Inches(label_top_in), Inches(_AXIS_LABEL_W_H_IN), Inches(_AXIS_LABEL_H_H_IN))
        _set_label(box.text_frame, [f"{tv:g}"], _AXIS_FONT_PT, PP_ALIGN.RIGHT)


def _set_title(slide, text: str, subtitle: str | list[str] | None = None) -> None:
    title_shape = slide.shapes.title
    tf = title_shape.text_frame
    tf.text = text
    tf.paragraphs[0].runs[0].font.size = Pt(_TITLE_FONT_PT)
    subtitle_lines = [subtitle] if isinstance(subtitle, str) else (subtitle or [])
    for line in subtitle_lines:
        if not line:
            continue
        p = tf.add_paragraph()
        run = p.add_run()
        run.text = line
        run.font.size = Pt(_SUBTITLE_FONT_PT)
        run.font.color.rgb = _SUBTITLE_COLOR


def _balance_text(rack: Rack) -> str:
    left, right = rack.left_right_weight()
    total = left + right
    if total <= 1e-9:
        return "Balance: -"
    left_pct = left / total * 100
    right_pct = right / total * 100
    return f"Balance: {left_pct:.0f}% / {right_pct:.0f}% (links/rechts)"


def _zusatzabstand_text(rack: Rack, neigungswinkel_grad: float) -> str | None:
    """'Zusatzabstand: links/rechts X mm, oben/unten Y mm' — the tilt
    margin actually computed and applied for THIS specific rack (see
    rack_tilt_extra_margin), not a run-wide range. None at
    neigungswinkel_grad <= 0, matching the existing dimension-line
    annotations' own gating (see _render_rack_slide)."""
    if neigungswinkel_grad <= 1e-9:
        return None
    extra_lr, extra_tb = rack_tilt_extra_margin(rack, neigungswinkel_grad)
    return f"Zusatzabstand: links/rechts {extra_lr:.1f} mm, oben/unten {extra_tb:.1f} mm"


def _mm_to_slide_x(mm_x: float, border_x_in: float, scale: float) -> float:
    return border_x_in + mm_x * scale


def _mm_to_slide_y(mm_y: float, border_y_in: float, scale: float, rack_h: float) -> float:
    return border_y_in + (rack_h - mm_y) * scale


def _add_dimension_line_h(slide, x1_in: float, x2_in: float, y_in: float, value_mm: float) -> None:
    """A short horizontal technical-drawing-style dimension line (main line
    plus small perpendicular end ticks) spanning x1_in..x2_in at y_in, with
    a centered mm value label above it."""
    lo, hi = min(x1_in, x2_in), max(x1_in, x2_in)
    if hi - lo < _DIM_MIN_LEN_IN:
        return
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(lo), Inches(y_in), Inches(hi), Inches(y_in))
    conn.line.color.rgb = _DIM_LINE_COLOR
    conn.line.width = Pt(1.0)
    for x_in in (lo, hi):
        tick = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, Inches(x_in), Inches(y_in - _DIM_TICK_LEN_IN / 2), Inches(x_in), Inches(y_in + _DIM_TICK_LEN_IN / 2)
        )
        tick.line.color.rgb = _DIM_LINE_COLOR
        tick.line.width = Pt(1.0)
    label_left_in = (lo + hi) / 2 - _DIM_LABEL_W_IN / 2
    label_top_in = y_in - _DIM_LABEL_H_IN - 0.02
    box = slide.shapes.add_textbox(Inches(label_left_in), Inches(label_top_in), Inches(_DIM_LABEL_W_IN), Inches(_DIM_LABEL_H_IN))
    _set_label(box.text_frame, [f"{value_mm:.0f} mm"], _DIM_FONT_PT, PP_ALIGN.CENTER)
    for run in box.text_frame.paragraphs[0].runs:
        run.font.color.rgb = _DIM_LINE_COLOR


def _add_dimension_line_v(slide, y1_in: float, y2_in: float, x_in: float, value_mm: float) -> None:
    """Vertical counterpart to _add_dimension_line_h."""
    lo, hi = min(y1_in, y2_in), max(y1_in, y2_in)
    if hi - lo < _DIM_MIN_LEN_IN:
        return
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x_in), Inches(lo), Inches(x_in), Inches(hi))
    conn.line.color.rgb = _DIM_LINE_COLOR
    conn.line.width = Pt(1.0)
    for y_in in (lo, hi):
        tick = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, Inches(x_in - _DIM_TICK_LEN_IN / 2), Inches(y_in), Inches(x_in + _DIM_TICK_LEN_IN / 2), Inches(y_in)
        )
        tick.line.color.rgb = _DIM_LINE_COLOR
        tick.line.width = Pt(1.0)
    label_left_in = x_in + _DIM_TICK_LEN_IN / 2 + 0.03
    label_top_in = (lo + hi) / 2 - _DIM_LABEL_H_IN / 2
    box = slide.shapes.add_textbox(Inches(label_left_in), Inches(label_top_in), Inches(_DIM_LABEL_W_IN), Inches(_DIM_LABEL_H_IN))
    _set_label(box.text_frame, [f"{value_mm:.0f} mm"], _DIM_FONT_PT, PP_ALIGN.LEFT)
    for run in box.text_frame.paragraphs[0].runs:
        run.font.color.rgb = _DIM_LINE_COLOR


def _add_edge_margin_dimensions(slide, rack: Rack, border_x_in: float, border_y_in: float, scale: float) -> None:
    """Dimension lines at all four rack edges (left, right, top, bottom),
    each showing the actual clearance from that edge to its own nearest
    bordering part (see Rack.all_edge_margin_details) — oriented
    horizontally for the left/right edges, vertically for top/bottom.
    Previously only the single widest edge got a line (whichever direction
    happened to have the most clearance), which could leave a genuinely
    tilt-enlarged edge undrawn if another edge's plain, unenlarged margin
    happened to measure larger due to ordinary centering slack; drawing
    all four makes every direction's real clearance visible regardless."""
    for side, part, value_mm in rack.all_edge_margin_details():
        if side in ("left", "right"):
            y_in = _mm_to_slide_y(part.y + part.h / 2, border_y_in, scale, rack.rack_h)
            if side == "left":
                x1_in = _mm_to_slide_x(0, border_x_in, scale)
                x2_in = _mm_to_slide_x(value_mm, border_x_in, scale)
            else:
                x1_in = _mm_to_slide_x(rack.rack_l - value_mm, border_x_in, scale)
                x2_in = _mm_to_slide_x(rack.rack_l, border_x_in, scale)
            _add_dimension_line_h(slide, x1_in, x2_in, y_in, value_mm)
        else:
            x_in = _mm_to_slide_x(part.x + part.w / 2, border_x_in, scale)
            if side == "bottom":
                y1_in = _mm_to_slide_y(0, border_y_in, scale, rack.rack_h)
                y2_in = _mm_to_slide_y(value_mm, border_y_in, scale, rack.rack_h)
            else:
                y1_in = _mm_to_slide_y(rack.rack_h - value_mm, border_y_in, scale, rack.rack_h)
                y2_in = _mm_to_slide_y(rack.rack_h, border_y_in, scale, rack.rack_h)
            _add_dimension_line_v(slide, y1_in, y2_in, x_in, value_mm)


def _add_item_gap_dimension(slide, rack: Rack, border_x_in: float, border_y_in: float, scale: float) -> None:
    """Dimension line between the single most closely-spaced neighboring
    pair on this rack (see Rack.tightest_gap_detail), showing the "regular"
    item gap as a visual counterpoint to the enlarged edge margin — skipped
    entirely if the gap is too small on the slide to label cleanly (see
    _DIM_MIN_LEN_IN in _add_dimension_line_h/_v), so it never overloads the
    drawing."""
    detail = rack.tightest_gap_detail()
    if detail is None:
        return
    a, b, gap_mm, direction = detail
    if gap_mm <= 1e-6:
        return
    if direction == "h":
        overlap_lo = max(a.y, b.y)
        overlap_hi = min(a.y + a.h, b.y + b.h)
        y_in = _mm_to_slide_y((overlap_lo + overlap_hi) / 2, border_y_in, scale, rack.rack_h)
        x1_in = _mm_to_slide_x(a.x + a.w, border_x_in, scale)
        x2_in = _mm_to_slide_x(b.x, border_x_in, scale)
        _add_dimension_line_h(slide, x1_in, x2_in, y_in, gap_mm)
    else:
        overlap_lo = max(a.x, b.x)
        overlap_hi = min(a.x + a.w, b.x + b.w)
        x_in = _mm_to_slide_x((overlap_lo + overlap_hi) / 2, border_x_in, scale)
        y1_in = _mm_to_slide_y(a.y + a.h, border_y_in, scale, rack.rack_h)
        y2_in = _mm_to_slide_y(b.y, border_y_in, scale, rack.rack_h)
        _add_dimension_line_v(slide, y1_in, y2_in, x_in, gap_mm)


def _render_rack_slide(
    prs: Presentation, rack: Rack, color_map: dict[str, str], max_w_in: float, max_h_in: float, edge_margin: float,
    neigungswinkel_grad: float = 0.0,
) -> None:
    title_only_layout = prs.slide_layouts[5]
    slide = prs.slides.add_slide(title_only_layout)
    subtitle_lines = [_balance_text(rack)]
    zusatzabstand_text = _zusatzabstand_text(rack, neigungswinkel_grad)
    if zusatzabstand_text is not None:
        subtitle_lines.append(zusatzabstand_text)
    _set_title(
        slide,
        f"Warenträger {rack.index + 1} — {len(rack.placed)} Bauteile, {rack.total_weight:.1f} kg "
        f"— Trägermaß {rack.rack_l:g} x {rack.rack_h:g} mm",
        subtitle_lines,
    )

    if rack.rack_l <= 0 or rack.rack_h <= 0:
        return

    avail_w_in = max_w_in - _AXIS_LEFT_RESERVE_IN
    avail_h_in = max_h_in - _AXIS_BOTTOM_RESERVE_IN
    scale = min(avail_w_in / rack.rack_l, avail_h_in / rack.rack_h)
    drawing_w_in = rack.rack_l * scale
    drawing_h_in = rack.rack_h * scale

    content_w_in = drawing_w_in + _AXIS_LEFT_RESERVE_IN
    content_h_in = drawing_h_in + _AXIS_BOTTOM_RESERVE_IN
    content_offset_x_in = _MARGIN_IN + (max_w_in - content_w_in) / 2
    content_offset_y_in = _TOP_IN + (max_h_in - content_h_in) / 2
    border_x_in = content_offset_x_in + _AXIS_LEFT_RESERVE_IN
    border_y_in = content_offset_y_in

    _add_rack_border(slide, border_x_in, border_y_in, drawing_w_in, drawing_h_in)
    _add_margin_guide(slide, border_x_in, border_y_in, drawing_w_in, drawing_h_in, scale, edge_margin)
    _add_l_axis(slide, border_x_in, border_y_in, drawing_w_in, drawing_h_in, scale, rack.rack_l)
    _add_h_axis(slide, border_x_in, border_y_in, scale, rack.rack_h)

    right_edge_limit_in = _SLIDE_W_IN - _MARGIN_IN
    placed_boxes: list = []
    for placed in rack.placed:
        _add_part_shape(slide, placed, border_x_in, border_y_in, scale, rack.rack_h, color_map, right_edge_limit_in, placed_boxes)

    if neigungswinkel_grad > 1e-9:
        _add_edge_margin_dimensions(slide, rack, border_x_in, border_y_in, scale)
        _add_item_gap_dimension(slide, rack, border_x_in, border_y_in, scale)


def _add_text_block(slide, left_in: float, top_in: float, width_in: float, height_in: float, lines: list[tuple[str, float, bool]]):
    """A word-wrapping textbox for freeform paragraphs of (text, font_pt,
    bold) — used for the cover slide's parameter/result summary, unlike the
    fixed-size, non-wrapping labels used elsewhere in this module."""
    box = slide.shapes.add_textbox(Inches(left_in), Inches(top_in), Inches(width_in), Inches(height_in))
    tf = box.text_frame
    tf.word_wrap = True
    for i, (text, font_pt, bold) in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = text
        run.font.size = Pt(font_pt)
        run.font.bold = bold
    return box


def _set_cell_text(cell, text: str, font_pt: float, bold: bool = False) -> None:
    cell.text = text
    cell.margin_left = cell.margin_right = Pt(3)
    cell.margin_top = cell.margin_bottom = Pt(1)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    for p in cell.text_frame.paragraphs:
        for run in p.runs:
            run.font.size = Pt(font_pt)
            run.font.bold = bold


def _add_parts_table(slide, rows_data: list[tuple], left_in: float, top_in: float, width_in: float, row_h_in: float) -> float:
    """Part No./Anzahl/Gewicht/L/H/B table for exactly `rows_data` (already
    the caller's chosen page of rows — see _rows_per_table_page /
    _render_table_continuation_slide for how the full, sorted overview is
    paginated across as many slides as needed). Returns the table's actual
    height in inches."""
    headers = ["Part No.", "Anzahl", "kg/Stk", "kg ges.", "L (mm)", "H (mm)", "B (mm)"]
    n_rows = len(rows_data) + 1
    table_h_in = row_h_in * n_rows

    graphic_frame = slide.shapes.add_table(n_rows, len(headers), Inches(left_in), Inches(top_in), Inches(width_in), Inches(table_h_in))
    table = graphic_frame.table
    for c, header in enumerate(headers):
        _set_cell_text(table.cell(0, c), header, _COVER_TABLE_FONT_PT, bold=True)
    for r, (part_no, count, unit_w, total_w, dim_l, dim_h, dim_b) in enumerate(rows_data, start=1):
        _set_cell_text(table.cell(r, 0), part_no, _COVER_TABLE_FONT_PT)
        _set_cell_text(table.cell(r, 1), str(count), _COVER_TABLE_FONT_PT)
        _set_cell_text(table.cell(r, 2), f"{unit_w:.2f}", _COVER_TABLE_FONT_PT)
        _set_cell_text(table.cell(r, 3), f"{total_w:.2f}", _COVER_TABLE_FONT_PT)
        _set_cell_text(table.cell(r, 4), f"{dim_l:.0f}", _COVER_TABLE_FONT_PT)
        _set_cell_text(table.cell(r, 5), f"{dim_h:.0f}", _COVER_TABLE_FONT_PT)
        _set_cell_text(table.cell(r, 6), f"{dim_b:.0f}", _COVER_TABLE_FONT_PT)

    return table_h_in


def _rows_per_table_page(max_height_in: float) -> int:
    return max(1, int(max_height_in / _COVER_TABLE_ROW_H_IN) - 1)  # -1 reserves the header row


def _render_table_continuation_slide(prs: Presentation, rows_data: list[tuple], page_num: int, total_pages: int) -> None:
    """A table-only slide continuing the cover slide's part overview — used
    whenever the full, part-No.-sorted table doesn't fit on the cover slide
    alone (Requirement: every part type must appear, never truncated)."""
    title_only_layout = prs.slide_layouts[5]
    slide = prs.slides.add_slide(title_only_layout)
    _set_title(slide, f"Bauteilübersicht (Fortsetzung {page_num}/{total_pages})")
    max_width_in = _SLIDE_W_IN - 2 * _MARGIN_IN
    _add_parts_table(slide, rows_data, _MARGIN_IN, _TOP_IN, max_width_in, _COVER_TABLE_ROW_H_IN)


def _render_cover_slide(prs: Presentation, run_params: RunParams | None, result: PackingResult) -> None:
    """First slide: all input parameters (mirroring the Excel export's own
    'Parameter' sheet), the parts-list file used, a result overview, and the
    start of the full, Part-No.-sorted per-part-type table — continued onto
    as many further slides as needed (see _render_table_continuation_slide)
    so no part type is ever left out."""
    title_only_layout = prs.slide_layouts[5]
    slide = prs.slides.add_slide(title_only_layout)
    _set_title(slide, "Warenträger-Packing — Übersicht")
    slide.shapes.title.text_frame.paragraphs[0].runs[0].font.size = Pt(_COVER_TITLE_FONT_PT)

    left_col_left_in = _MARGIN_IN
    right_col_left_in = left_col_left_in + _COVER_LEFT_COL_WIDTH_IN + _COVER_COL_GAP_IN
    right_col_width_in = (_SLIDE_W_IN - _MARGIN_IN) - right_col_left_in
    content_height_in = _SLIDE_H_IN - _MARGIN_IN - _COVER_CONTENT_TOP_IN

    lines: list[tuple[str, float, bool]] = [("Parameter", _COVER_HEADER_FONT_PT, True)]
    if run_params is not None:
        lines += [
            (f"Teileliste: {run_params.parts_file}", _COVER_BODY_FONT_PT, False),
            (f"Trägerlänge L: {run_params.rack_l:g} mm", _COVER_BODY_FONT_PT, False),
            (f"Trägerhöhe H: {run_params.rack_h:g} mm", _COVER_BODY_FONT_PT, False),
            (f"Trägertiefe B: {run_params.rack_b:g} mm", _COVER_BODY_FONT_PT, False),
            (f"Abstand zwischen Bauteilen: {run_params.item_gap:g} mm", _COVER_BODY_FONT_PT, False),
            (f"Abstand zum Trägerrand: {run_params.edge_margin:g} mm", _COVER_BODY_FONT_PT, False),
            (f"Gewünschte Neigung: {run_params.neigungswinkel_grad:g}°", _COVER_BODY_FONT_PT, False),
            (f"Maschinen/Chargen pro Tag: {run_params.maschinen_pro_tag}", _COVER_BODY_FONT_PT, False),
            (f"Reihenfolge-Modus: {run_params.reihenfolge_label}", _COVER_BODY_FONT_PT, False),
            (f"Maschinentrennung: {'Ja' if run_params.maschinentrennung else 'Nein'}", _COVER_BODY_FONT_PT, False),
        ]
    else:
        lines.append(("(keine Parameter verfügbar)", _COVER_BODY_FONT_PT, False))

    lines.append(("", _COVER_BODY_FONT_PT, False))
    lines.append(("Ergebnis", _COVER_HEADER_FONT_PT, True))
    lines.append((f"Benötigte Warenträger/Tag: {result.rack_count_display}", _COVER_BODY_FONT_PT, False))
    lines.append((f"Gesamtgewicht/Tag: {result.total_weight_per_day():.1f} kg", _COVER_BODY_FONT_PT, False))
    heaviest = result.heaviest_rack()
    if heaviest is not None:
        lines.append((f"Schwerster Warenträger: Nr. {heaviest.index + 1} mit {heaviest.total_weight:.1f} kg", _COVER_BODY_FONT_PT, False))
    lines.append((f"Anzahl Warnungen/Fehler: {len(result.issues)}", _COVER_BODY_FONT_PT, False))
    if result.issues:
        shown = result.issues[:_COVER_MAX_WARNINGS_SHOWN]
        for issue in shown:
            lines.append((f"– {issue.message}", _COVER_BODY_FONT_PT - 1, False))
        remaining_issues = len(result.issues) - len(shown)
        if remaining_issues > 0:
            lines.append((f"… und {remaining_issues} weitere", _COVER_BODY_FONT_PT - 1, False))

    _add_text_block(slide, left_col_left_in, _COVER_CONTENT_TOP_IN, _COVER_LEFT_COL_WIDTH_IN, content_height_in, lines)

    parts_summary = result.aggregate_parts()  # already sorted ascending by part_no
    if not parts_summary:
        return

    rows_per_cover = _rows_per_table_page(content_height_in)
    cover_rows = parts_summary[:rows_per_cover]
    remaining_rows = parts_summary[rows_per_cover:]
    _add_parts_table(slide, cover_rows, right_col_left_in, _COVER_CONTENT_TOP_IN, right_col_width_in, _COVER_TABLE_ROW_H_IN)

    if remaining_rows:
        continuation_content_h_in = _SLIDE_H_IN - _MARGIN_IN - _TOP_IN
        rows_per_continuation = _rows_per_table_page(continuation_content_h_in)
        pages = [remaining_rows[i:i + rows_per_continuation] for i in range(0, len(remaining_rows), rows_per_continuation)]
        for page_num, page_rows in enumerate(pages, start=1):
            _render_table_continuation_slide(prs, page_rows, page_num, len(pages))


def export_pptx(result: PackingResult, pptx_path: str | Path, color_map: dict[str, str], run_params: RunParams | None = None) -> None:
    """Write a cover/overview slide followed by one slide per rack, each part
    as its own native autoshape (with the placed/rotated L x H dimensions as
    a second text line, or as an external dimension callout for parts too
    small to hold both lines) instead of a single embedded picture — so
    every part stays individually clickable, movable and resizable in
    PowerPoint. The rack outline is drawn as a simple unfilled border
    rectangle with a lightweight L/H tick-mark scale along its bottom and
    left edges, plus a dashed inner guide marking the usable area after the
    edge margin."""
    prs = Presentation()
    prs.slide_width = Inches(_SLIDE_W_IN)
    prs.slide_height = Inches(_SLIDE_H_IN)

    _render_cover_slide(prs, run_params, result)

    max_w_in = _SLIDE_W_IN - 2 * _MARGIN_IN
    max_h_in = _SLIDE_H_IN - _TOP_IN - _MARGIN_IN
    edge_margin = run_params.edge_margin if run_params is not None else 0.0
    neigungswinkel_grad = run_params.neigungswinkel_grad if run_params is not None else 0.0

    for rack in result.racks:
        _render_rack_slide(prs, rack, color_map, max_w_in, max_h_in, edge_margin, neigungswinkel_grad)

    prs.save(pptx_path)
