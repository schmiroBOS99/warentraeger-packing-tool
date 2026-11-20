"""Kompakter Smoke- und Invariantentest.

Aufruf (aus dem Projektordner):  python tests/test_smoke.py
oder mit pytest:                 pytest -q tests

Geprueft wird fuer alle 3 Reihenfolge-Modi x 2 Maschinentrennung-Einstellungen
x 2 Neigungswinkel (0 und 3 Grad) auf der erfundenen Beispiel-Teileliste:
  1. Es gehen keine Bauteile verloren oder entstehen doppelt.
  2. Alle Bauteile liegen innerhalb des Traegers (inklusive Randabstand).
  3. Keine zwei Bauteile ueberlappen, der Mindestabstand wird eingehalten.
  4. Bei 'listenreihenfolge' steigt die Part No. ueber die Traeger nicht an.
Zusaetzlich laufen Excel- und PowerPoint-Export einmal komplett durch.
"""
from __future__ import annotations

import itertools
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.drawing import build_color_map  # noqa: E402
from app.excel_io import export_results, read_parts  # noqa: E402
from app.models import RunParams, part_no_sort_key  # noqa: E402
from app.packing import compute_packing  # noqa: E402
from app.pptx_export import export_pptx  # noqa: E402

EXAMPLE = ROOT / "examples" / "beispiel_teileliste.xlsx"
RACK_L, RACK_H, RACK_B = 3000.0, 2000.0, 1000.0
ITEM_GAP, EDGE_MARGIN, MACHINES = 100.0, 50.0, 3
EPS = 1e-6

MODES = ["frei", "chargenweise", "listenreihenfolge"]


def _run(parts, modus, trennung, winkel):
    return compute_packing(
        parts, RACK_L, RACK_H, RACK_B, ITEM_GAP, EDGE_MARGIN, MACHINES,
        reihenfolge_modus=modus, maschinentrennung=trennung, neigungswinkel_grad=winkel,
    )


def _check_geometry(result):
    for rack in result.racks:
        for p in rack.placed:
            assert p.x >= EDGE_MARGIN - EPS and p.y >= EDGE_MARGIN - EPS, f"Rand verletzt: Rack {rack.index}, Teil {p.part_no}"
            assert p.x + p.w <= RACK_L - EDGE_MARGIN + EPS, f"Rand rechts: Rack {rack.index}, Teil {p.part_no}"
            assert p.y + p.h <= RACK_H - EDGE_MARGIN + EPS, f"Rand oben: Rack {rack.index}, Teil {p.part_no}"
        for a, b in itertools.combinations(rack.placed, 2):
            dx = max(a.x - (b.x + b.w), b.x - (a.x + a.w))
            dy = max(a.y - (b.y + b.h), b.y - (a.y + a.h))
            assert max(dx, dy) >= ITEM_GAP - EPS, (
                f"Abstand/Ueberlappung: Rack {rack.index}, Teile {a.part_no} und {b.part_no}, Abstand {max(dx, dy):.1f}"
            )


def _check_conservation(parts, result, trennung):
    expected: dict[str, int] = {}
    factor = 1 if trennung else MACHINES
    for p in parts:
        expected[p.part_no] = p.qty_per_machine * factor
    found: dict[str, int] = {}
    for rack in result.racks:
        for part_no, n in rack.part_counts().items():
            found[part_no] = found.get(part_no, 0) + n
    assert found == expected, "Bauteilanzahl stimmt nicht mit der Eingabe ueberein"


def _check_list_order(result):
    previous_max = None
    for rack in result.racks:
        numbers = [part_no_sort_key(p.part_no) for p in rack.placed]
        if not numbers:
            continue
        if previous_max is not None:
            assert min(numbers) >= previous_max, f"Listenreihenfolge verletzt bei Rack {rack.index}"
        previous_max = max(numbers)


def test_all_modes():
    parts, issues = read_parts(EXAMPLE)
    assert parts and not [i for i in issues if i.severity == "error"]
    for modus, trennung, winkel in itertools.product(MODES, (False, True), (0.0, 3.0)):
        result = _run(parts, modus, trennung, winkel)
        label = f"{modus}/{'MT' if trennung else 'ohneMT'}/{winkel:g}Grad"
        assert result.racks, label
        try:
            _check_conservation(parts, result, trennung)
            _check_geometry(result)
            if modus == "listenreihenfolge":
                _check_list_order(result)
        except AssertionError as exc:
            raise AssertionError(f"{label}: {exc}") from exc


def test_exports():
    parts, _ = read_parts(EXAMPLE)
    result = _run(parts, "frei", False, 3.0)
    params = RunParams(
        rack_l=RACK_L, rack_h=RACK_H, rack_b=RACK_B, item_gap=ITEM_GAP, edge_margin=EDGE_MARGIN,
        maschinen_pro_tag=MACHINES, reihenfolge_modus="frei", maschinentrennung=False,
        parts_file=EXAMPLE.name, neigungswinkel_grad=3.0,
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        export_results(result, tmp_path / "ergebnis.xlsx", tmp_path / "png", params)
        color_map = build_color_map(p.part_no for p in parts)
        export_pptx(result, tmp_path / "zeichnungen.pptx", color_map, params)
        assert (tmp_path / "ergebnis.xlsx").stat().st_size > 0
        assert (tmp_path / "zeichnungen.pptx").stat().st_size > 0


if __name__ == "__main__":
    test_all_modes()
    print("test_all_modes: OK")
    test_exports()
    print("test_exports: OK")
