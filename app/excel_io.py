"""Excel import of the parts list and export of packing results."""
from __future__ import annotations

from pathlib import Path

import openpyxl

from app.models import Issue, Part, PackingResult, RunParams, part_no_sort_key

# Column offsets relative to the "Part No." anchor cell (0 = same column as anchor).
COL_PART_NO = 0
COL_L = 1
COL_B = 2
COL_H = 3
COL_WEIGHT = 4
COL_RACK_W = 5
COL_RACK_H = 6
COL_QTY_PER_MACHINE = 7


def _find_anchor(ws) -> tuple[int, int] | None:
    """Find the (row, col) of the cell containing 'Part No.' (case-insensitive)."""
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.strip().lower().startswith("part no"):
                return cell.row, cell.column
    return None


def _to_positive_float(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return f


def read_parts(path: str | Path) -> tuple[list[Part], list[Issue]]:
    """Read the parts list from an Excel file with the fixed layout described in the README.

    Header is located by anchoring on a "Part No." cell; data starts three rows
    below the anchor (anchor row = group header, +1 sub-header, +2 blank, +3 data).
    Malformed rows are skipped and reported as warnings rather than raising.
    """
    path = Path(path)
    issues: list[Issue] = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    anchor = _find_anchor(ws)
    if anchor is None:
        issues.append(Issue("error", "Konnte Spalte 'Part No.' in der Excel-Datei nicht finden."))
        return [], issues

    anchor_row, anchor_col = anchor
    data_start_row = anchor_row + 3

    parts: list[Part] = []
    seen_part_nos: set[str] = set()
    max_row = ws.max_row or data_start_row

    for row_idx in range(data_start_row, max_row + 1):
        def cell(offset: int):
            return ws.cell(row=row_idx, column=anchor_col + offset).value

        part_no_raw = cell(COL_PART_NO)
        row_values = [cell(i) for i in range(COL_PART_NO, COL_QTY_PER_MACHINE + 1)]
        if all(v is None for v in row_values):
            continue  # fully blank row, not an error

        if part_no_raw is None:
            issues.append(Issue("warning", f"Zeile {row_idx}: Part No. fehlt, Zeile übersprungen."))
            continue
        part_no = str(part_no_raw).strip()

        l = _to_positive_float(cell(COL_L))
        b = _to_positive_float(cell(COL_B))
        h = _to_positive_float(cell(COL_H))
        weight_raw = cell(COL_WEIGHT)
        try:
            weight = float(weight_raw) if weight_raw is not None else None
        except (TypeError, ValueError):
            weight = None
        qty_raw = cell(COL_QTY_PER_MACHINE)
        try:
            qty = int(qty_raw) if qty_raw is not None else None
        except (TypeError, ValueError):
            qty = None

        if l is None or b is None or h is None:
            issues.append(
                Issue("warning", f"Zeile {row_idx} (Part No. {part_no}): fehlende oder ungültige Maße L/B/H, Zeile übersprungen.", part_no)
            )
            continue
        if weight is None or weight < 0:
            issues.append(
                Issue("warning", f"Zeile {row_idx} (Part No. {part_no}): fehlendes oder ungültiges Gewicht, Zeile übersprungen.", part_no)
            )
            continue
        if qty is None or qty < 0:
            issues.append(
                Issue("warning", f"Zeile {row_idx} (Part No. {part_no}): fehlende oder ungültige Stückzahl je Maschine, Zeile übersprungen.", part_no)
            )
            continue
        if qty == 0:
            continue  # nothing to place, not an error

        if part_no in seen_part_nos:
            issues.append(Issue("warning", f"Zeile {row_idx}: Part No. {part_no} kommt mehrfach vor.", part_no))
        seen_part_nos.add(part_no)

        parts.append(Part(part_no=part_no, l=l, b=b, h=h, weight=weight, qty_per_machine=qty))

    if not parts:
        issues.append(Issue("error", "Keine gültigen Teiledaten in der Excel-Datei gefunden."))

    return parts, issues


def suggest_rack_lh(path: str | Path) -> tuple[float | None, float | None]:
    """Suggest rack L/H from the 'Rack size example' columns, if present."""
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    anchor = _find_anchor(ws)
    if anchor is None:
        return None, None
    anchor_row, anchor_col = anchor
    data_start_row = anchor_row + 3
    max_row = ws.max_row or data_start_row

    w_values: list[float] = []
    h_values: list[float] = []
    for row_idx in range(data_start_row, max_row + 1):
        w = ws.cell(row=row_idx, column=anchor_col + COL_RACK_W).value
        h = ws.cell(row=row_idx, column=anchor_col + COL_RACK_H).value
        if isinstance(w, (int, float)) and w > 0:
            w_values.append(float(w))
        if isinstance(h, (int, float)) and h > 0:
            h_values.append(float(h))

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        s = sorted(values)
        n = len(s)
        mid = n // 2
        if n % 2:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2

    return _median(w_values), _median(h_values)


def _safe_sheet_title(title: str) -> str:
    for ch in r":\/?*[]":
        title = title.replace(ch, "_")
    return title[:31]


def _write_params_sheet(ws, run_params: RunParams | None, result: PackingResult) -> None:
    """Full run parameters + result overview — kept as its own sheet (rather
    than folded into 'Zusammenfassung') so a result stays traceable later
    without having to remember which settings produced it, mirroring the
    PowerPoint cover slide."""
    ws.append(["Parameter", "Wert"])
    if run_params is not None:
        ws.append(["Teileliste", run_params.parts_file])
        ws.append(["Trägerlänge L (mm)", run_params.rack_l])
        ws.append(["Trägerhöhe H (mm)", run_params.rack_h])
        ws.append(["Trägertiefe B (mm)", run_params.rack_b])
        ws.append(["Abstand zwischen Bauteilen (mm)", run_params.item_gap])
        ws.append(["Abstand zum Trägerrand (mm)", run_params.edge_margin])
        ws.append(["Gewünschte Neigung (Grad)", run_params.neigungswinkel_grad])
        ws.append(["Maschinen/Chargen pro Tag", run_params.maschinen_pro_tag])
        ws.append(["Reihenfolge-Modus", run_params.reihenfolge_label])
        ws.append(["Maschinentrennung", "Ja" if run_params.maschinentrennung else "Nein"])
    ws.append([])
    ws.append(["Ergebnisübersicht", ""])
    ws.append(["Benötigte Warenträger/Tag", result.rack_count_display])
    ws.append(["Gesamtgewicht/Tag (kg)", round(result.total_weight_per_day(), 2)])
    heaviest = result.heaviest_rack()
    if heaviest is not None:
        ws.append(["Schwerster Warenträger", f"Nr. {heaviest.index + 1} ({heaviest.total_weight:.1f} kg)"])
    ws.append(["Anzahl Warnungen/Fehler", len(result.issues)])
    if result.issues:
        ws.append([])
        ws.append(["Warnungen / Fehler"])
        for issue in result.issues:
            ws.append([issue.severity, issue.message])


def export_results(
    result: PackingResult, xlsx_path: str | Path, png_dir: str | Path,
    run_params: RunParams | None = None, figures: dict[int, "object"] | None = None,
) -> None:
    """Write a results workbook (parameters + summary + one sheet per rack)
    and, if figures are provided, save one PNG per rack into png_dir."""
    xlsx_path = Path(xlsx_path)
    png_dir = Path(png_dir)
    png_dir.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    params_ws = wb.active
    params_ws.title = "Parameter"
    _write_params_sheet(params_ws, run_params, result)

    summary = wb.create_sheet(title="Zusammenfassung")
    summary.append(["Warenträger", "Gewicht (kg)", "Anzahl Bauteile"])
    for rack in result.racks:
        summary.append([rack.index + 1, round(rack.total_weight, 2), len(rack.placed)])
    summary.append([])
    summary.append(["Anzahl Warenträger (angezeigt)", result.rack_count_display])
    summary.append(["Maschinen/Chargen pro Tag", result.machines_per_day])
    summary.append(["Tagesgesamtmenge gepoolt", "Ja" if result.pooled_daily else "Nein"])
    if result.issues:
        summary.append([])
        summary.append(["Warnungen / Fehler"])
        for issue in result.issues:
            summary.append([issue.severity, issue.message])

    for rack in result.racks:
        sheet_title = _safe_sheet_title(f"Rack {rack.index + 1:03d}")
        ws = wb.create_sheet(title=sheet_title)
        ws.append(["Part No.", "Anzahl", "Gewicht je Stück (kg)", "Gewicht gesamt (kg)", "L (mm)", "H (mm)", "B (mm)"])
        counts = rack.part_counts()
        weight_by_part: dict[str, float] = {}
        dims_by_part: dict[str, tuple[float, float, float]] = {}
        for p in rack.placed:
            weight_by_part[p.part_no] = p.weight
            dims_by_part.setdefault(p.part_no, (p.w, p.h, p.depth))
        for part_no in sorted(counts, key=part_no_sort_key):
            count = counts[part_no]
            unit_weight = weight_by_part.get(part_no, 0.0)
            dim_w, dim_h, dim_depth = dims_by_part.get(part_no, (0.0, 0.0, 0.0))
            ws.append(
                [part_no, count, round(unit_weight, 2), round(unit_weight * count, 2), round(dim_w, 1), round(dim_h, 1), round(dim_depth, 1)]
            )

        if figures and rack.index in figures:
            png_path = png_dir / f"rack_{rack.index + 1:03d}.png"
            figures[rack.index].savefig(png_path, dpi=150, bbox_inches="tight")

    wb.save(xlsx_path)
