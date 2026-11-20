"""Tkinter GUI for the Warenträger packing tool."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import date
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from app.drawing import build_color_map, render_rack
from app.excel_io import export_results, read_parts, suggest_rack_lh
from app.models import RunParams, part_no_sort_key
from app.packing import compute_packing
from app.pptx_export import export_pptx


class NumericEntry(ttk.Entry):
    """An Entry that can show a grey placeholder value, used directly if the
    user leaves the field untouched."""

    def __init__(self, master, **kwargs):
        self.var = tk.StringVar()
        super().__init__(master, textvariable=self.var, **kwargs)
        self._is_placeholder = False
        self._placeholder_value: float | None = None
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)

    def set_placeholder(self, value: float | None) -> None:
        self._placeholder_value = value
        if value is not None and (not self.var.get() or self._is_placeholder):
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        if self._placeholder_value is not None:
            self._is_placeholder = True
            self.var.set(f"{self._placeholder_value:g}")
            self.config(foreground="grey")

    def _on_focus_in(self, _event) -> None:
        if self._is_placeholder:
            self.var.set("")
            self.config(foreground="black")
            self._is_placeholder = False

    def _on_focus_out(self, _event) -> None:
        if not self.var.get():
            self._show_placeholder()

    def get_value(self) -> float | None:
        text = self.var.get().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Warenträger-Packing-Tool")
        self.geometry("1250x800")

        self.parts = []
        self.excel_path: str | None = None
        self.result = None
        self.last_run_params: RunParams | None = None
        self.color_map: dict[str, str] = {}
        self.figure_cache: dict[int, object] = {}
        self.current_rack_idx = 0
        self.canvas_widget = None
        self._queue: queue.Queue = queue.Queue()

        self._build_widgets()

    # ------------------------------------------------------------------ UI

    def _build_widgets(self) -> None:
        input_frame = ttk.LabelFrame(self, text="Eingaben")
        input_frame.pack(side="top", fill="x", padx=8, pady=6)

        row = 0
        ttk.Label(input_frame, text="Träger-Länge L (mm)").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        self.rack_l_entry = NumericEntry(input_frame, width=12)
        self.rack_l_entry.grid(row=row, column=1, padx=4, pady=2)

        ttk.Label(input_frame, text="Träger-Höhe H (mm)").grid(row=row, column=2, sticky="w", padx=4, pady=2)
        self.rack_h_entry = NumericEntry(input_frame, width=12)
        self.rack_h_entry.grid(row=row, column=3, padx=4, pady=2)

        ttk.Label(input_frame, text="Träger-Tiefe B (mm)").grid(row=row, column=4, sticky="w", padx=4, pady=2)
        self.rack_b_entry = NumericEntry(input_frame, width=12)
        self.rack_b_entry.grid(row=row, column=5, padx=4, pady=2)

        row += 1
        ttk.Label(input_frame, text="Abstand zwischen Bauteilen (mm)").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        self.item_gap_entry = NumericEntry(input_frame, width=12)
        self.item_gap_entry.var.set("100")
        self.item_gap_entry.grid(row=row, column=1, padx=4, pady=2)

        ttk.Label(input_frame, text="Abstand zum Trägerrand (mm)").grid(row=row, column=2, sticky="w", padx=4, pady=2)
        self.edge_margin_entry = NumericEntry(input_frame, width=12)
        self.edge_margin_entry.var.set("0")
        self.edge_margin_entry.grid(row=row, column=3, padx=4, pady=2)

        ttk.Label(input_frame, text="Maschinen/Charge pro Tag").grid(row=row, column=4, sticky="w", padx=4, pady=2)
        self.machines_entry = NumericEntry(input_frame, width=12)
        self.machines_entry.var.set("3")
        self.machines_entry.grid(row=row, column=5, padx=4, pady=2)

        row += 1
        ttk.Label(input_frame, text="Gewünschte Neigung (Grad)").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        self.neigungswinkel_entry = NumericEntry(input_frame, width=12)
        self.neigungswinkel_entry.var.set("0")
        self.neigungswinkel_entry.grid(row=row, column=1, padx=4, pady=2)

        row += 1
        self.pick_file_btn = ttk.Button(input_frame, text="Teileliste (Excel) wählen…", command=self.on_pick_file)
        self.pick_file_btn.grid(row=row, column=0, sticky="w", padx=4, pady=6)
        help_btn = ttk.Button(input_frame, text="?", width=2, command=self._show_parts_list_help)
        help_btn.grid(row=row, column=1, sticky="w", pady=6)
        self.file_label = ttk.Label(input_frame, text="Keine Datei geladen.")
        self.file_label.grid(row=row, column=2, columnspan=4, sticky="w", padx=4, pady=6)

        row += 1
        reihenfolge_frame = ttk.LabelFrame(input_frame, text="Reihenfolge")
        reihenfolge_frame.grid(row=row, column=0, columnspan=4, sticky="w", padx=4, pady=4)
        order_help_btn = ttk.Button(input_frame, text="?", width=2, command=self._show_order_help)
        order_help_btn.grid(row=row, column=4, sticky="w", pady=4)
        self.reihenfolge_var = tk.StringVar(value="frei")
        ttk.Radiobutton(
            reihenfolge_frame, text="Freie Optimierung", variable=self.reihenfolge_var, value="frei"
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            reihenfolge_frame,
            text="Chargenweise, freie Reihenfolge",
            variable=self.reihenfolge_var,
            value="chargenweise",
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            reihenfolge_frame,
            text="Strikte Listenreihenfolge",
            variable=self.reihenfolge_var,
            value="listenreihenfolge",
        ).pack(side="left", padx=4)

        row += 1
        self.maschinentrennung_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            input_frame, text="Maschinentrennung", variable=self.maschinentrennung_var
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=2)

        row += 1
        self.berechnen_btn = ttk.Button(input_frame, text="Berechnen", command=self.on_berechnen)
        self.berechnen_btn.grid(row=row, column=0, sticky="w", padx=4, pady=6)
        algorithm_help_btn = ttk.Button(input_frame, text="?", width=2, command=self._show_algorithm_help)
        algorithm_help_btn.grid(row=row, column=1, sticky="w", pady=6)

        self.status_var = tk.StringVar(value="Bereit.")
        ttk.Label(input_frame, textvariable=self.status_var).grid(row=row, column=2, columnspan=2, sticky="w")

        self.progress = ttk.Progressbar(input_frame, mode="determinate", maximum=100, length=200)
        self.progress.grid(row=row, column=4, columnspan=2, sticky="w", padx=4)

        # Floated via place() (independent of the grid above) so it sits in
        # the frame's own top-right corner without reserving a grid row and
        # pushing the rest of "Eingaben" down. Added last so it stacks above
        # the grid content instead of being hidden behind it.
        general_help_btn = ttk.Button(input_frame, text="?", width=2, command=self._show_general_help)
        general_help_btn.place(relx=1.0, x=-6, y=4, anchor="ne")

        # ---- results area ----
        results_frame = ttk.Frame(self)
        results_frame.pack(side="top", fill="both", expand=True, padx=8, pady=6)

        self.summary_var = tk.StringVar(value="")
        ttk.Label(results_frame, textvariable=self.summary_var, font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w"
        )

        issues_frame = ttk.LabelFrame(results_frame, text="Warnungen / Fehler")
        issues_frame.pack(side="top", fill="x", pady=4)
        self.issues_text = ScrolledText(issues_frame, height=5, state="disabled", wrap="word")
        self.issues_text.pack(fill="x")

        pager_frame = ttk.Frame(results_frame)
        pager_frame.pack(side="top", fill="x", pady=4)
        self.prev_btn = ttk.Button(pager_frame, text="< Zurück", command=self.on_prev, state="disabled")
        self.prev_btn.pack(side="left")
        self.page_label = ttk.Label(pager_frame, text="Kein Ergebnis")
        self.page_label.pack(side="left", padx=8)
        self.balance_label = ttk.Label(pager_frame, text="")
        self.balance_label.pack(side="left", padx=8)
        self.next_btn = ttk.Button(pager_frame, text="Weiter >", command=self.on_next, state="disabled")
        self.next_btn.pack(side="left")
        self.export_btn = ttk.Button(pager_frame, text="Ergebnis exportieren…", command=self.on_export)
        self.export_btn.pack(side="right")

        body_frame = ttk.Frame(results_frame)
        body_frame.pack(side="top", fill="both", expand=True)

        self.canvas_container = ttk.Frame(body_frame)
        self.canvas_container.pack(side="left", fill="both", expand=True)

        table_frame = ttk.Frame(body_frame, width=470)
        table_frame.pack(side="right", fill="y")
        table_frame.pack_propagate(False)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        tree_scroll_y = ttk.Scrollbar(table_frame, orient="vertical")
        tree_scroll_x = ttk.Scrollbar(table_frame, orient="horizontal")
        self.tree = ttk.Treeview(
            table_frame,
            columns=("part_no", "count", "unit_weight", "total_weight", "dim_l", "dim_h", "dim_b"),
            show="headings",
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set,
        )
        tree_scroll_y.config(command=self.tree.yview)
        tree_scroll_x.config(command=self.tree.xview)
        self.tree.heading("part_no", text="Part No.")
        self.tree.heading("count", text="Anzahl")
        self.tree.heading("unit_weight", text="kg/Stück")
        self.tree.heading("total_weight", text="kg gesamt")
        self.tree.heading("dim_l", text="L (mm)")
        self.tree.heading("dim_h", text="H (mm)")
        self.tree.heading("dim_b", text="B (mm)")
        for col in ("part_no", "count", "unit_weight", "total_weight", "dim_l", "dim_h", "dim_b"):
            self.tree.column(col, width=58, anchor="center", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")

    # -------------------------------------------------------------- events

    def _show_general_help(self) -> None:
        messagebox.showinfo(
            "Über dieses Tool",
            "Dieses Tool berechnet für eine gegebene Teileliste, wie Bauteile "
            "möglichst platzsparend und mit vertretbarem Gewichtsausgleich auf "
            "Warenträger für eine Lackieranlage verteilt werden können — "
            "inklusive 2D-Zeichnung und Stückliste je Warenträger, statt die "
            "Trägerbelegung von Hand durchrechnen zu müssen.\n\n"
            "Grundablauf:\n\n"
            "1. Teileliste (Excel) laden — L/H werden dabei als Vorschlag "
            "automatisch befüllt, wo möglich.\n\n"
            "2. Trägermaße, Abstände, gewünschte Neigung beim Lackieren, "
            "Maschinen/Charge pro Tag, Reihenfolge-Modus und Maschinentrennung "
            "eingeben (eigene '?'-Buttons erklären die Teileliste bzw. "
            "Reihenfolge/Maschinentrennung im Detail).\n\n"
            "3. 'Berechnen' klicken — das Ergebnis zeigt die benötigte Anzahl "
            "Warenträger pro Tag, das Gesamtgewicht und den schwersten "
            "Warenträger, dazu je Warenträger eine Zeichnung, Balance-Angabe "
            "und Stückliste zum Durchblättern.\n\n"
            "4. Ergebnis als Excel- und PowerPoint-Datei exportieren (Dateiname "
            "mit Datum und Modus-Kürzel), für Weiterverarbeitung bzw. "
            "Präsentation.",
        )

    def _show_algorithm_help(self) -> None:
        messagebox.showinfo(
            "Wie rechnet der Algorithmus?",
            "Grundprinzip\n\n"
            "Bauteile werden in Reihen von oben nach unten platziert, jede "
            "Reihe mittig auf dem Warenträger ausgerichtet. Innerhalb einer "
            "Reihe werden Bauteile in Spalten gruppiert, damit kleinere Teile "
            "die Resthöhe über kürzeren Teilen mitnutzen.\n\n"
            "Rangfolge der Ziele\n\n"
            "Der Algorithmus verfolgt seine Ziele in fester Reihenfolge, ein "
            "späteres Ziel darf ein früheres nie verschlechtern:\n\n"
            "1. So wenige Warenträger wie möglich. Bei 'Strikte "
            "Listenreihenfolge' und 'Chargenweise' bleibt die vorgegebene "
            "Reihenfolge dabei immer unangetastet, auch wenn eine andere "
            "Anordnung die Fläche noch besser ausnutzen würde. Bevor ein "
            "Warenträger als voll gilt, wird zusätzlich geprüft, ob durch "
            "dichteres Nachpacken noch ein weiteres Bauteil hineinpasst. "
            "Schwach gefüllte Warenträger werden am Ende, wo möglich, "
            "zusammengelegt (bei 'Frei' und 'Chargenweise').\n\n"
            "2. Gleichbleibende Ausrichtung je Bauteiltyp. Jeder Bauteiltyp "
            "hängt über den gesamten Lauf hinweg immer identisch: "
            "rechteckige, nicht quadratische Bauteile stehen standardmäßig "
            "senkrecht (lange Seite nach oben), für einzelne Bauteile gelten "
            "hinterlegte Ausnahmen. Nur wenn diese feste Ausrichtung auf "
            "einem Warenträger nachweislich gar nicht passt, wird "
            "ausnahmsweise gedreht, das wird dann als Warnung ausgegeben.\n\n"
            "3. Gewichtsbalance. Weicht das Gewicht zwischen linker und "
            "rechter Trägerhälfte zu stark voneinander ab (mehr als 25 "
            "Prozent), werden Bauteile getauscht oder notfalls auf den "
            "nächsten Warenträger verschoben. Die feste Ausrichtung je "
            "Bauteiltyp bleibt dabei unangetastet.\n\n"
            "4. Symmetrie. Wo möglich, werden gleiche Bauteile symmetrisch "
            "um die Mittelachse angeordnet, wenn das ohne Platzverlust "
            "möglich ist.\n\n"
            "Zusätzlicher Abstand bei Neigung\n\n"
            "Die eingestellte Neigung ändert nie Form oder Größe eines "
            "Bauteils. Da alle Bauteile gleich geneigt werden, bleibt ihr "
            "Abstand zueinander unverändert, zusätzlichen Platz braucht "
            "ein geneigtes Bauteil nur gegenüber dem starren "
            "Trägerrand.\n\n"
            "Jeder Warenträger erhält deshalb individuell, anhand seines "
            "eigenen Inhalts, einen Zusatzabstand zum Rand: links/rechts "
            "abhängig von der größten Bauteilhöhe, oben/unten von der "
            "größten Bauteilbreite auf diesem Warenträger, jeweils nur "
            "wenn diese Dimension 500 mm übersteigt.\n\n"
            "Damit dieser Randabstand nie nachträglich wächst und bereits "
            "platzierte Bauteile wieder entfernt werden müssten, wird bei "
            "'Frei' und 'Chargenweise' das größte Bauteil bevorzugt "
            "zuerst einsortiert. Bei 'Strikte Listenreihenfolge' kann der "
            "Randabstand noch wachsen, dann wandert aber ein neues "
            "Bauteil auf den nächsten Warenträger, statt ein bereits "
            "platziertes zu entfernen.\n\n"
            "Kurz gesagt\n\n"
            "Zuerst so wenige Warenträger wie möglich, danach immer gleich "
            "ausgerichtete Bauteile, dann so ausgeglichen und zuletzt so "
            "symmetrisch wie möglich, ohne die vorherigen Ziele zu "
            "verschlechtern. Eine eingestellte Neigung vergrößert dabei "
            "ausschließlich den Randabstand, individuell je Warenträger, "
            "nie den Abstand zwischen den Bauteilen selbst.",
        )

    def _show_parts_list_help(self) -> None:
        messagebox.showinfo(
            "Aufbau der Teileliste (Excel)",
            "Die Teileliste muss nicht pixelgenau gleich aufgebaut sein wie das "
            "Beispiel, folgendes muss aber stimmen:\n\n"
            "1. Spaltenreihenfolge: Direkt rechts neben der Spalte \"Part No.\" "
            "müssen in dieser Reihenfolge stehen: L, B, H, Gewicht, "
            "Rack-Beispielbreite, Rack-Beispielhöhe, Stückzahl je Maschine.\n\n"
            "2. Zeilenversatz: Die Datenzeilen müssen genau 3 Zeilen unterhalb "
            "der Zelle \"Part No.\" beginnen (Gruppenüberschrift, "
            "Unterüberschrift, eine Leerzeile, dann die Daten).\n\n"
            "3. Erstes Tabellenblatt: Es wird nur das erste Tabellenblatt der "
            "Excel-Datei eingelesen, weitere Blätter werden ignoriert.\n\n"
            "Die genaue Zeilenposition der Tabelle im Blatt sowie zusätzliche "
            "Inhalte außerhalb dieses Bereichs spielen keine Rolle.",
        )

    def _show_order_help(self) -> None:
        messagebox.showinfo(
            "Reihenfolge & Maschinentrennung",
            "Reihenfolge-Modus (bestimmt, wie frei Bauteile den Warenträgern "
            "zugeordnet werden dürfen):\n\n"
            "• Freie Optimierung: Reihenfolge und Gruppierung der Bauteile "
            "werden komplett aufgelöst, es wird die platzsparendste "
            "Kombination gesucht. Liefert in der Regel die wenigsten "
            "Warenträger.\n\n"
            "• Chargenweise, freie Reihenfolge: Gleiche Bauteile bleiben als "
            "Charge zusammen, die Reihenfolge der Chargen selbst ist aber "
            "frei wählbar. Guter Kompromiss, wenn Bauteile in der Fertigung "
            "als zusammenhängende Chargen anfallen sollen.\n\n"
            "• Strikte Listenreihenfolge: Die Warenträger werden strikt in "
            "der Reihenfolge der Part-Nummern aus der Teileliste befüllt "
            "(aufsteigend). Innerhalb eines einmal zugeordneten "
            "Warenträgers wird trotzdem möglichst platzsparend nachgepackt. "
            "Meist die meisten Warenträger, dafür am besten nachvollziehbar.\n\n"
            "Maschinentrennung:\n\n"
            "• Nicht aktiviert (Standard): Die komplette Tagesmenge "
            "(Stückzahl je Maschine × Maschinen/Chargen pro Tag) wird "
            "gemeinsam als eine Menge auf Warenträger gepackt.\n\n"
            "• Aktiviert: Es wird nur für eine einzelne Maschine gepackt, "
            "das Ergebnis (Anzahl Warenträger) wird anschließend mit der "
            "Anzahl Maschinen/Chargen pro Tag multipliziert. Sinnvoll, wenn "
            "jede Maschine unabhängig und ausfallsicher beliefert werden "
            "soll, statt Bauteile maschinenübergreifend zu mischen.",
        )

    def on_pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Teileliste wählen", filetypes=[("Excel-Dateien", "*.xlsx")]
        )
        if not path:
            return

        def _load():
            parts, issues = read_parts(path)
            suggested_l, suggested_h = suggest_rack_lh(path)
            return parts, issues, suggested_l, suggested_h

        def _on_loaded(payload):
            parts, issues, suggested_l, suggested_h = payload
            self.excel_path = path
            self.parts = parts
            self.file_label.config(text=os.path.basename(path))
            self._show_issues(issues)
            self.rack_l_entry.set_placeholder(suggested_l)
            self.rack_h_entry.set_placeholder(suggested_h)
            self._clear_results()
            self.status_var.set(f"{len(parts)} Teile geladen.")

        self._run_background(_load, _on_loaded, "Lese Teileliste ein…", error_title="Fehler beim Einlesen")

    def _validate_inputs(self) -> dict | None:
        if not self.parts:
            messagebox.showerror("Fehlende Daten", "Bitte zuerst eine Excel-Teileliste laden.")
            return None

        rack_l = self.rack_l_entry.get_value()
        rack_h = self.rack_h_entry.get_value()
        rack_b = self.rack_b_entry.get_value()
        item_gap = self.item_gap_entry.get_value()
        edge_margin = self.edge_margin_entry.get_value()
        machines_raw = self.machines_entry.get_value()
        neigungswinkel = self.neigungswinkel_entry.get_value()

        for name, value in (("Länge L", rack_l), ("Höhe H", rack_h), ("Tiefe B", rack_b)):
            if value is None or value <= 0:
                messagebox.showerror("Ungültige Eingabe", f"'{name}' muss eine positive Zahl sein.")
                return None
        for name, value in (("Abstand zwischen Bauteilen", item_gap), ("Abstand zum Trägerrand", edge_margin)):
            if value is None or value < 0:
                messagebox.showerror("Ungültige Eingabe", f"'{name}' darf nicht negativ oder leer sein.")
                return None
        if neigungswinkel is None or neigungswinkel < 0 or neigungswinkel >= 90:
            messagebox.showerror("Ungültige Eingabe", "'Gewünschte Neigung' muss zwischen 0 und 90 Grad liegen.")
            return None
        if machines_raw is None or machines_raw <= 0 or int(machines_raw) != machines_raw:
            messagebox.showerror("Ungültige Eingabe", "'Maschinen/Charge pro Tag' muss eine positive ganze Zahl sein.")
            return None
        if edge_margin * 2 >= rack_l or edge_margin * 2 >= rack_h or edge_margin * 2 >= rack_b:
            messagebox.showerror(
                "Ungültige Eingabe",
                "Der Abstand zum Trägerrand ist zu groß für die angegebenen Trägermaße L/H/B.",
            )
            return None

        return dict(
            parts=self.parts,
            rack_l=rack_l,
            rack_h=rack_h,
            rack_b=rack_b,
            item_gap=item_gap,
            edge_margin=edge_margin,
            maschinen_pro_tag=int(machines_raw),
            reihenfolge_modus=self.reihenfolge_var.get(),
            maschinentrennung=self.maschinentrennung_var.get(),
            neigungswinkel_grad=neigungswinkel,
        )

    def on_berechnen(self) -> None:
        params = self._validate_inputs()
        if params is None:
            return
        self.last_run_params = RunParams(
            rack_l=params["rack_l"],
            rack_h=params["rack_h"],
            rack_b=params["rack_b"],
            item_gap=params["item_gap"],
            edge_margin=params["edge_margin"],
            maschinen_pro_tag=params["maschinen_pro_tag"],
            reihenfolge_modus=params["reihenfolge_modus"],
            maschinentrennung=params["maschinentrennung"],
            parts_file=self.excel_path or "",
            neigungswinkel_grad=params["neigungswinkel_grad"],
        )
        self._set_busy(True)
        thread = threading.Thread(target=self._worker, args=(params,), daemon=True)
        thread.start()
        self.after(100, self._poll_queue)

    def _worker(self, params: dict) -> None:
        def report(fraction: float, label: str) -> None:
            self._queue.put(("progress", fraction, label))

        try:
            result = compute_packing(**params, progress_callback=report)
            color_map = build_color_map(p.part_no for p in params["parts"])
            first_fig = None
            if result.racks:
                report(0.95, "Zeichnung erstellen")
                first_fig = render_rack(result.racks[0], color_map)
            report(1.0, "Fertig")
            self._queue.put(("done", result, color_map, first_fig))
        except Exception as exc:  # surface as a message, never crash the app
            self._queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        finished = False
        while True:
            try:
                msg = self._queue.get_nowait()
            except queue.Empty:
                break
            msg_type = msg[0]
            if msg_type == "progress":
                _, fraction, label = msg
                self.progress["value"] = max(0.0, min(100.0, fraction * 100))
                self.status_var.set(f"Berechnung läuft… ({label})")
            elif msg_type == "error":
                self._set_busy(False, success=False)
                messagebox.showerror("Fehler bei der Berechnung", msg[1])
                finished = True
            elif msg_type == "done":
                _, result, color_map, first_fig = msg
                self._set_busy(False, success=True)
                self._show_result(result, color_map, first_fig)
                finished = True
        if not finished:
            self.after(100, self._poll_queue)

    def _set_busy(self, busy: bool, success: bool | None = None) -> None:
        if busy:
            self.progress.config(mode="determinate")
            self.progress["value"] = 0
            self.status_var.set("Berechnung läuft…")
            self._set_action_buttons_enabled(False)
        else:
            self._set_action_buttons_enabled(True)
            if success:
                self.progress["value"] = 100
                self.status_var.set("Fertig.")
            else:
                self.progress["value"] = 0
                self.status_var.set("Fehler bei der Berechnung.")

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.berechnen_btn.config(state=state)
        self.pick_file_btn.config(state=state)
        self.export_btn.config(state=state)

    def _run_background(self, worker_fn, on_success, busy_text: str, error_title: str = "Fehler") -> None:
        """Run worker_fn() (no args) in a background thread while showing an
        indeterminate progress bar + busy_text and disabling the main action
        buttons — used for one-shot tasks without a natural step-by-step
        percentage (file loading, export), as opposed to _worker/_poll_queue
        which drive the determinate, staged progress for a Berechnen run."""
        q: queue.Queue = queue.Queue()

        def _bg() -> None:
            try:
                value = worker_fn()
                q.put(("done", value))
            except Exception as exc:  # surface as a message, never crash the app
                q.put(("error", str(exc)))

        self.progress.config(mode="indeterminate")
        self.progress.start(10)
        self.status_var.set(busy_text)
        self._set_action_buttons_enabled(False)

        def _poll() -> None:
            try:
                msg_type, payload = q.get_nowait()
            except queue.Empty:
                self.after(100, _poll)
                return
            self.progress.stop()
            self.progress.config(mode="determinate")
            self.progress["value"] = 0
            self._set_action_buttons_enabled(True)
            if msg_type == "error":
                self.status_var.set("Fehler.")
                messagebox.showerror(error_title, payload)
                return
            self.status_var.set("Bereit.")
            on_success(payload)

        threading.Thread(target=_bg, daemon=True).start()
        self.after(100, _poll)

    # -------------------------------------------------------------- output

    def _show_issues(self, issues) -> None:
        self.issues_text.config(state="normal")
        self.issues_text.delete("1.0", "end")
        for issue in issues:
            if issue.severity == "error":
                prefix = "FEHLER"
            elif issue.severity == "info":
                prefix = "Info"
            else:
                prefix = "Warnung"
            self.issues_text.insert("end", f"[{prefix}] {issue.message}\n")
        if not issues:
            self.issues_text.insert("end", "Keine Warnungen.\n")
        self.issues_text.config(state="disabled")

    def _clear_results(self) -> None:
        self.result = None
        self.figure_cache = {}
        self.current_rack_idx = 0
        self.summary_var.set("")
        self.page_label.config(text="Kein Ergebnis")
        self.balance_label.config(text="")
        self.prev_btn.config(state="disabled")
        self.next_btn.config(state="disabled")
        if self.canvas_widget is not None:
            self.canvas_widget.get_tk_widget().destroy()
            self.canvas_widget = None
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _show_result(self, result, color_map=None, first_fig=None) -> None:
        self.result = result
        self.color_map = color_map if color_map is not None else build_color_map(p.part_no for p in self.parts)
        self.figure_cache = {0: first_fig} if first_fig is not None else {}
        self._show_issues(result.issues)

        if not result.racks:
            self._clear_results_keep_issues()
            self.status_var.set("Keine Warenträger — alle Bauteile ausgeschlossen oder keine Daten.")
            return

        total_weight_per_day = result.total_weight_per_day()
        note = ""
        if not result.pooled_daily:
            note = (
                f" (Anzeige zeigt {len(result.racks)} eindeutige Belegungen je Maschine, "
                f"× {result.machines_per_day} Maschinen/Tag = {result.rack_count_display} Warenträger/Tag)"
            )
        heaviest = result.heaviest_rack()
        heaviest_note = ""
        if heaviest is not None:
            heaviest_note = f" | Schwerster Warenträger: Nr. {heaviest.index + 1} mit {heaviest.total_weight:.1f} kg"
        self.summary_var.set(
            f"Benötigte Warenträger/Tag: {result.rack_count_display}{note} | "
            f"Gesamtgewicht/Tag: {total_weight_per_day:.1f} kg{heaviest_note}"
        )
        self.status_var.set("Fertig.")
        self.current_rack_idx = 0
        self._render_page(0)

    def _clear_results_keep_issues(self) -> None:
        self.page_label.config(text="Kein Ergebnis")
        self.balance_label.config(text="")
        self.prev_btn.config(state="disabled")
        self.next_btn.config(state="disabled")
        if self.canvas_widget is not None:
            self.canvas_widget.get_tk_widget().destroy()
            self.canvas_widget = None
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.summary_var.set("Benötigte Warenträger/Tag: 0")

    def _render_page(self, idx: int) -> None:
        if not self.result or not self.result.racks:
            return
        idx = max(0, min(idx, len(self.result.racks) - 1))
        self.current_rack_idx = idx
        rack = self.result.racks[idx]

        fig = self.figure_cache.get(idx)
        if fig is None:
            fig = render_rack(rack, self.color_map)
            self.figure_cache[idx] = fig

        if self.canvas_widget is not None:
            self.canvas_widget.get_tk_widget().destroy()
        self.canvas_widget = FigureCanvasTkAgg(fig, master=self.canvas_container)
        self.canvas_widget.draw()
        self.canvas_widget.get_tk_widget().pack(fill="both", expand=True)

        for item in self.tree.get_children():
            self.tree.delete(item)
        weight_by_part = {p.part_no: p.weight for p in rack.placed}
        dims_by_part: dict[str, tuple[float, float, float]] = {}
        for p in rack.placed:
            dims_by_part.setdefault(p.part_no, (p.w, p.h, p.depth))
        part_counts = rack.part_counts()
        for part_no in sorted(part_counts, key=part_no_sort_key):
            count = part_counts[part_no]
            unit_weight = weight_by_part.get(part_no, 0.0)
            dim_w, dim_h, dim_depth = dims_by_part.get(part_no, (0.0, 0.0, 0.0))
            self.tree.insert(
                "",
                "end",
                values=(
                    part_no,
                    count,
                    f"{unit_weight:.2f}",
                    f"{unit_weight * count:.2f}",
                    f"{dim_w:.0f}",
                    f"{dim_h:.0f}",
                    f"{dim_depth:.0f}",
                ),
            )
        self._autosize_tree_columns()

        self.page_label.config(text=f"Warenträger {idx + 1} / {len(self.result.racks)}")
        left, right = rack.left_right_weight()
        total = left + right
        if total > 1e-9:
            self.balance_label.config(text=f"Balance: {left / total * 100:.0f}% / {right / total * 100:.0f}%")
        else:
            self.balance_label.config(text="Balance: -")
        self.prev_btn.config(state="normal" if idx > 0 else "disabled")
        self.next_btn.config(state="normal" if idx < len(self.result.racks) - 1 else "disabled")

    def _autosize_tree_columns(self) -> None:
        """Size each column to its actual content (header vs. widest cell),
        so narrow columns (counts, dims) stay compact and no column is
        clipped by a one-size-fits-all default width. Horizontal/vertical
        scrollbars remain as a safety net for unusually wide content."""
        font = tkfont.nametofont("TkDefaultFont")
        for col in self.tree["columns"]:
            header_text = self.tree.heading(col)["text"]
            widest = font.measure(header_text)
            for item in self.tree.get_children():
                cell_text = str(self.tree.set(item, col))
                widest = max(widest, font.measure(cell_text))
            width = min(max(widest + 18, 45), 160)
            self.tree.column(col, width=width)

    def on_prev(self) -> None:
        self._render_page(self.current_rack_idx - 1)

    def on_next(self) -> None:
        self._render_page(self.current_rack_idx + 1)

    def on_export(self) -> None:
        if not self.result or not self.result.racks:
            messagebox.showinfo("Export", "Kein Ergebnis zum Exportieren vorhanden.")
            return
        folder = filedialog.askdirectory(title="Zielordner für den Export wählen")
        if not folder:
            return

        result = self.result
        color_map = self.color_map
        run_params = self.last_run_params
        figures = dict(self.figure_cache)  # local copy: safe to extend from the background thread

        date_str = date.today().strftime("%Y%m%d")
        abbrev = run_params.mode_abbreviation if run_params is not None else "X"
        xlsx_name = f"{date_str}-Warenträger-{abbrev}.xlsx"
        pptx_name = f"{date_str}-Zeichnungen-{abbrev}.pptx"

        def _export():
            for idx, rack in enumerate(result.racks):
                if idx not in figures:
                    figures[idx] = render_rack(rack, color_map)
            xlsx_path = os.path.join(folder, xlsx_name)
            png_dir = os.path.join(folder, "zeichnungen")
            export_results(result, xlsx_path, png_dir, run_params, figures)
            pptx_path = os.path.join(folder, pptx_name)
            export_pptx(result, pptx_path, color_map, run_params)
            return figures

        def _on_exported(rendered_figures):
            self.figure_cache.update(rendered_figures)
            messagebox.showinfo(
                "Export",
                f"Ergebnis exportiert nach:\n{folder}\n\n"
                f"- {xlsx_name} (Kennzahlen + Zuordnungstabelle)\n"
                "- zeichnungen\\ (PNG je Warenträger)\n"
                f"- {pptx_name} (eine Folie je Warenträger)",
            )

        self._run_background(_export, _on_exported, "Exportiere Ergebnis…", error_title="Fehler beim Export")
