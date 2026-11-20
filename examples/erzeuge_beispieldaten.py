"""Erzeugt eine vollstaendig erfundene Beispiel-Teileliste (beispiel_teileliste.xlsx).

Die Daten stammen aus keinem realen Projekt. Sie sind mit festem Zufalls-Seed
erzeugt und damit reproduzierbar. Das Layout entspricht dem, was
app/excel_io.py erwartet:

  Zeile 4:  Ueberschriften ("Part No." im Anker, Gruppentitel)
  Zeile 5:  Unterueberschriften (L, B, H, Gewicht, Traeger W/H, Stueckzahl)
  Zeile 6:  leer
  ab Zeile 7: Daten (Part No., L, B, H, Gewicht, Traeger W, Traeger H, Stueckzahl je Maschine)
"""
from __future__ import annotations

import random
from pathlib import Path

import openpyxl

SEED = 20260101
RACK_W_EXAMPLE = 3000   # nur ein Vorschlagswert fuer die GUI
RACK_H_EXAMPLE = 2000


def build_parts(rng: random.Random) -> list[tuple]:
    parts: list[tuple] = []

    def add(l, b, h, weight, qty):
        parts.append((l, b, h, round(weight, 1), qty))

    # lange, schlanke Teile (Stangen, Profile)
    for _ in range(5):
        l = rng.choice([1600, 1800, 2200, 2600])
        b = rng.choice([80, 120, 160])
        h = rng.choice([80, 120, 160])
        add(l, b, h, l * b * h / 1.2e6, rng.choice([1, 2, 2, 4]))
    # flache Platten und Bleche
    for _ in range(8):
        l = rng.choice([600, 800, 1000, 1200, 1500])
        b = rng.choice([400, 500, 700, 900])
        h = rng.choice([10, 15, 20, 30])
        add(l, b, h, l * b * h / 1.0e6 * 2.2, rng.choice([1, 2, 4, 6]))
    # kastenartige Teile
    for _ in range(8):
        l = rng.choice([400, 500, 600, 700, 800])
        b = rng.choice([300, 400, 500])
        h = rng.choice([200, 300, 400, 500])
        add(l, b, h, l * b * h / 4.0e6, rng.choice([1, 2, 3, 6, 8]))
    # Kleinteile in groesserer Stueckzahl
    for _ in range(7):
        l = rng.choice([120, 150, 200, 250, 300])
        b = rng.choice([80, 100, 150])
        h = rng.choice([80, 100, 150, 200])
        add(l, b, h, max(0.5, l * b * h / 6.0e6), rng.choice([8, 10, 12, 16]))
    # ein schweres, breites Einzelteil (prueft die Behandlung dominanter Teile)
    add(2400, 900, 400, 210.0, 1)
    # ein sehr grosser Rahmen
    add(2800, 1000, 120, 95.0, 1)
    return parts


def main() -> None:
    rng = random.Random(SEED)
    parts = build_parts(rng)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tabelle1"
    ws["A1"] = "Beispiel Teileliste"
    ws["B1"] = "erfundene Daten, kein realer Auftrag"

    ws["A4"] = "Part No."
    ws["B4"] = "Size approx mm"
    ws["E4"] = "Weight kg"
    ws["F4"] = "Rack size example"
    ws["H4"] = "Number"
    ws["B5"], ws["C5"], ws["D5"] = "L", "B", "H"
    ws["E5"] = "each part"
    ws["F5"], ws["G5"] = "W", "H"
    ws["H5"] = "each machine"

    for i, (l, b, h, weight, qty) in enumerate(parts, start=1):
        row = 6 + i
        ws.cell(row=row, column=1, value=i)
        ws.cell(row=row, column=2, value=l)
        ws.cell(row=row, column=3, value=b)
        ws.cell(row=row, column=4, value=h)
        ws.cell(row=row, column=5, value=weight)
        ws.cell(row=row, column=6, value=RACK_W_EXAMPLE)
        ws.cell(row=row, column=7, value=RACK_H_EXAMPLE)
        ws.cell(row=row, column=8, value=qty)

    out = Path(__file__).with_name("beispiel_teileliste.xlsx")
    wb.save(out)
    print(f"{len(parts)} Bauteiltypen geschrieben nach {out}")


if __name__ == "__main__":
    main()
