"""Panel-Export tab: many statistics of many files in one Excel table."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pandas as pd

from gating import list_gate_set_names
from panel_export import (
    DEFAULT_STATS as PANEL_DEFAULT_STATS,
    POP_ALL as PANEL_POP_ALL,
    REF_ALL as PANEL_REF_ALL,
    REF_PARENT as PANEL_REF_PARENT,
    STAT_LABELS as PANEL_STAT_LABELS,
    PanelEntry,
    compute_panel,
    load_panel,
    panel_definition_table,
    save_panel,
    write_panel_xlsx,
)


class PanelTabMixin:
    """Building the panel definition and computing/exporting the result table."""

    PANEL_STAT_HELP = {
        "n": "Anzahl (Ereignisse der Population)",
        "pct_ref": "% Bezug (Anteil am Eltern-Gate bzw. gewählten Bezug)",
        "pct_total": "% aller Events",
        "median": "MFI Median (robust, üblicher MFI-Wert)",
        "mean": "Mean (arithmetisch, empfindlich für Ausreißer)",
        "geomean": "geom. Mean (nur Werte > 0 gehen ein)",
        "cv": "rCV % (robuste Streuung, 0,75·IQR/Median)",
    }

    def _build_panel_tab(self, parent):
        self.panel_entries: list[PanelEntry] = []
        self.panel_result: pd.DataFrame | None = None
        self.panel_info: pd.DataFrame | None = None
        self.panel_marker_names: dict[str, str] = {}
        self._panel_channels: list[str] = []

        ttk.Label(
            parent,
            text="Mehrere Messgrößen (Population × Marker × Statistik) für ALLE Dateien in einem Rutsch berechnen und "
            "als eine Tabelle exportieren: eine Zeile pro Probe, eine Spalte pro Messgröße (Excel mit Spalten-Heatmap). "
            "Die Statistik wird immer auf genau der gewählten Population berechnet: 'CD4+' → MFI aller CD4+ Zellen; "
            "'CD4+CD107a+' → nur die CD107a+ Zellen. '% Bezug' nutzt standardmäßig das Eltern-Gate (Gate-Set ohne "
            "seinen letzten Schritt), d.h. das gezeichnete Gate ist die Schwelle.",
            foreground="#555", wraplength=1150, justify="left",
        ).pack(anchor="w", padx=6, pady=(6, 2))

        # ---- 1. definition ----
        define = ttk.LabelFrame(parent, text="1. Messgrößen definieren")
        define.pack(fill="x", padx=4, pady=4)

        left = ttk.Frame(define)
        left.grid(row=0, column=0, sticky="nw", padx=4, pady=4)
        ttk.Label(left, text="Population:").grid(row=0, column=0, sticky="w")
        self.panel_pop_var = tk.StringVar()
        self.panel_pop_combo = ttk.Combobox(left, textvariable=self.panel_pop_var, state="readonly", width=30)
        self.panel_pop_combo.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(left, text="Bezug für %:").grid(row=1, column=0, sticky="w")
        self.panel_ref_var = tk.StringVar(value=PANEL_REF_PARENT)
        self.panel_ref_combo = ttk.Combobox(left, textvariable=self.panel_ref_var, state="readonly", width=30)
        self.panel_ref_combo.grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(left, text="Schwelle (optional):").grid(row=2, column=0, sticky="w")
        self.panel_thr_var = tk.StringVar(value="")
        ttk.Entry(left, textvariable=self.panel_thr_var, width=12).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(
            left,
            text="Leer lassen = das Gate ist die Grenze (Standard). Mit Schwelle kommen zusätzlich "
            "'% > Schwelle' und die Statistiken nur der Zellen über der Schwelle (Rohwert des Kanals) hinzu.",
            foreground="#555", wraplength=320, justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        marker_frame = ttk.Frame(define)
        marker_frame.grid(row=0, column=1, sticky="nw", padx=8, pady=4)
        ttk.Label(marker_frame, text="Marker (Mehrfachauswahl mit Strg/Shift;\nkeiner = nur Populations-Werte):").pack(
            anchor="w"
        )
        lb_frame = ttk.Frame(marker_frame)
        lb_frame.pack(fill="both", expand=True)
        self.panel_marker_listbox = tk.Listbox(lb_frame, selectmode="extended", exportselection=False, height=9, width=32)
        lb_scroll = ttk.Scrollbar(lb_frame, orient="vertical", command=self.panel_marker_listbox.yview)
        self.panel_marker_listbox.configure(yscrollcommand=lb_scroll.set)
        self.panel_marker_listbox.pack(side="left", fill="both", expand=True)
        lb_scroll.pack(side="right", fill="y")

        stats_frame = ttk.Frame(define)
        stats_frame.grid(row=0, column=2, sticky="nw", padx=8, pady=4)
        ttk.Label(stats_frame, text="Statistiken:").pack(anchor="w")
        self.panel_stat_vars: dict[str, tk.BooleanVar] = {}
        for key in PANEL_STAT_LABELS:
            var = tk.BooleanVar(value=key in PANEL_DEFAULT_STATS)
            self.panel_stat_vars[key] = var
            ttk.Checkbutton(stats_frame, text=self.PANEL_STAT_HELP[key], variable=var).pack(anchor="w")

        btn_frame = ttk.Frame(define)
        btn_frame.grid(row=0, column=3, sticky="nw", padx=8, pady=4)
        ttk.Button(btn_frame, text="Zum Panel hinzufügen ↓", command=self._panel_add).pack(fill="x", pady=2)
        ttk.Button(
            btn_frame, text="Statistiken auf markierte Zeilen anwenden", command=self._panel_apply_stats
        ).pack(fill="x", pady=2)
        ttk.Label(
            btn_frame, text="(pro Zeile wird ein Marker angelegt;\nmarkierte Zeilen erben die Häkchen)",
            foreground="#555", justify="left",
        ).pack(anchor="w", pady=(0, 4))

        # ---- 2. panel ----
        panel_frame = ttk.LabelFrame(parent, text="2. Panel (Reihenfolge = Spaltenreihenfolge im Export)")
        panel_frame.pack(fill="x", padx=4, pady=4)
        cols = ("population", "marker", "reference", "threshold", "stats")
        heads = ("Population", "Marker", "Bezug für %", "Schwelle", "Statistiken")
        widths = (200, 200, 190, 80, 380)
        self.panel_tree = ttk.Treeview(panel_frame, columns=cols, show="headings", height=7, selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.panel_tree.heading(c, text=h)
            self.panel_tree.column(c, width=w, anchor="w")
        self.panel_tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        self.panel_tree.bind("<<TreeviewSelect>>", self._panel_on_select)
        self.panel_tree.bind("<Delete>", lambda e: self._panel_remove())
        pt_scroll = ttk.Scrollbar(panel_frame, orient="vertical", command=self.panel_tree.yview)
        self.panel_tree.configure(yscrollcommand=pt_scroll.set)
        pt_scroll.pack(side="left", fill="y", pady=4)
        side = ttk.Frame(panel_frame)
        side.pack(side="left", fill="y", padx=6, pady=4)
        ttk.Button(side, text="▲ nach oben", command=lambda: self._panel_move(-1)).pack(fill="x", pady=1)
        ttk.Button(side, text="▼ nach unten", command=lambda: self._panel_move(1)).pack(fill="x", pady=1)
        ttk.Button(side, text="Markierte entfernen", command=self._panel_remove).pack(fill="x", pady=1)
        ttk.Button(side, text="Alle entfernen", command=self._panel_clear).pack(fill="x", pady=1)
        ttk.Separator(side, orient="horizontal").pack(fill="x", pady=6)
        ttk.Button(side, text="Panel laden...", command=self._panel_load).pack(fill="x", pady=1)
        ttk.Button(side, text="Panel speichern...", command=self._panel_save).pack(fill="x", pady=1)

        # ---- 3. compute / export ----
        run_frame = ttk.LabelFrame(parent, text="3. Berechnen & exportieren")
        run_frame.pack(fill="both", expand=True, padx=4, pady=4)
        run_bar = ttk.Frame(run_frame)
        run_bar.pack(fill="x", padx=4, pady=4)
        ttk.Button(run_bar, text="Für alle Dateien berechnen", command=self._panel_compute).pack(side="left")
        ttk.Button(run_bar, text="Exportieren (Excel / CSV)...", command=self._panel_export).pack(side="left", padx=6)
        self.panel_heatmap_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(run_bar, text="Spalten-Heatmap in Excel", variable=self.panel_heatmap_var).pack(
            side="left", padx=6
        )
        self.panel_status_var = tk.StringVar(value="")
        ttk.Label(run_bar, textvariable=self.panel_status_var, foreground="#555").pack(side="left", padx=10)

        res_frame = ttk.Frame(run_frame)
        res_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.panel_result_tree = ttk.Treeview(res_frame, show="headings", height=8)
        rx = ttk.Scrollbar(res_frame, orient="horizontal", command=self.panel_result_tree.xview)
        ry = ttk.Scrollbar(res_frame, orient="vertical", command=self.panel_result_tree.yview)
        self.panel_result_tree.configure(xscrollcommand=rx.set, yscrollcommand=ry.set)
        self.panel_result_tree.grid(row=0, column=0, sticky="nsew")
        ry.grid(row=0, column=1, sticky="ns")
        rx.grid(row=1, column=0, sticky="ew")
        res_frame.rowconfigure(0, weight=1)
        res_frame.columnconfigure(0, weight=1)

        self._refresh_panel_gate_combos()

    def _panel_gate_names(self) -> list[str]:
        return list_gate_set_names(self.existing_gate_sets)

    def _refresh_panel_gate_combos(self):
        names = self._panel_gate_names()
        pops = names + [PANEL_POP_ALL]
        self.panel_pop_combo["values"] = pops
        if self.panel_pop_var.get() not in pops:
            self.panel_pop_var.set(pops[0])
        refs = [PANEL_REF_PARENT, PANEL_REF_ALL] + names
        self.panel_ref_combo["values"] = refs
        if self.panel_ref_var.get() not in refs:
            self.panel_ref_var.set(PANEL_REF_PARENT)

    def _panel_marker_display(self, channel: str | None) -> str:
        if not channel:
            return "— (nur Population)"
        marker = self.panel_marker_names.get(channel)
        return f"{channel} ({marker})" if marker else channel

    def _refresh_panel_channels(self, channels: list[str], marker_names: dict[str, str]):
        self.panel_marker_names.update(marker_names)
        if channels == self._panel_channels:
            return
        selected = {self._panel_channels[i] for i in self.panel_marker_listbox.curselection()
                    if i < len(self._panel_channels)}
        self._panel_channels = list(channels)
        self.panel_marker_listbox.delete(0, "end")
        for i, ch in enumerate(channels):
            self.panel_marker_listbox.insert("end", self._panel_marker_display(ch))
            if ch in selected:
                self.panel_marker_listbox.selection_set(i)

    def _panel_checked_stats(self) -> list[str]:
        return [k for k, v in self.panel_stat_vars.items() if v.get()]

    def _refresh_panel_tree(self, select: list[int] | None = None):
        self.panel_tree.delete(*self.panel_tree.get_children())
        for i, e in enumerate(self.panel_entries):
            self.panel_tree.insert("", "end", iid=str(i), values=(
                e.population,
                self._panel_marker_display(e.marker),
                e.reference,
                "" if e.threshold is None else f"{e.threshold:g}",
                e.stats_text(),
            ))
        if select:
            self.panel_tree.selection_set([str(i) for i in select if 0 <= i < len(self.panel_entries)])

    def _panel_selected_indices(self) -> list[int]:
        return sorted(int(iid) for iid in self.panel_tree.selection())

    def _panel_add(self):
        pop = self.panel_pop_var.get()
        if not pop:
            messagebox.showwarning("Keine Population", "Bitte zuerst eine Population (gespeichertes Gate-Set) wählen.")
            return
        stats = self._panel_checked_stats()
        if not stats:
            messagebox.showwarning("Keine Statistik", "Bitte mindestens eine Statistik anhaken.")
            return
        thr_text = self.panel_thr_var.get().strip()
        threshold = self._parse_optional_float(thr_text)
        if thr_text and threshold is None:
            messagebox.showwarning("Ungültige Schwelle", "Die Schwelle muss eine Zahl sein (oder leer).")
            return
        markers = [self._panel_channels[i] for i in self.panel_marker_listbox.curselection()]
        if not markers:
            if not any(s in ("n", "pct_ref", "pct_total") for s in stats):
                messagebox.showwarning(
                    "Kein Marker gewählt",
                    "Ohne Marker gibt es nur Populations-Werte (Anzahl / %). Bitte einen Marker auswählen "
                    "oder Anzahl / % anhaken."
                )
                return
            markers = [None]
            threshold = None
        new = []
        for m in markers:
            entry = PanelEntry(population=pop, marker=m, reference=self.panel_ref_var.get(),
                               stats=list(stats), threshold=threshold)
            if entry not in self.panel_entries:
                self.panel_entries.append(entry)
                new.append(len(self.panel_entries) - 1)
        self._refresh_panel_tree(select=new)
        self._clear_panel_result()
        if not new:
            self.panel_status_var.set("Diese Messgröße(n) sind bereits im Panel.")

    def _panel_on_select(self, _event=None):
        idx = self._panel_selected_indices()
        if len(idx) != 1:
            return
        e = self.panel_entries[idx[0]]
        for k, var in self.panel_stat_vars.items():
            var.set(k in e.stats)

    def _panel_apply_stats(self):
        idx = self._panel_selected_indices()
        stats = self._panel_checked_stats()
        if not idx:
            messagebox.showinfo("Nichts markiert", "Bitte im Panel die Zeile(n) markieren, die geändert werden sollen.")
            return
        if not stats:
            messagebox.showwarning("Keine Statistik", "Bitte mindestens eine Statistik anhaken.")
            return
        for i in idx:
            self.panel_entries[i].stats = list(stats)
        self._refresh_panel_tree(select=idx)
        self._clear_panel_result()

    def _panel_move(self, direction: int):
        idx = self._panel_selected_indices()
        if not idx:
            return
        entries = self.panel_entries
        order = idx if direction < 0 else list(reversed(idx))
        if (direction < 0 and idx[0] == 0) or (direction > 0 and idx[-1] == len(entries) - 1):
            return
        for i in order:
            j = i + direction
            entries[i], entries[j] = entries[j], entries[i]
        self._refresh_panel_tree(select=[i + direction for i in idx])
        self._clear_panel_result()

    def _panel_remove(self):
        idx = set(self._panel_selected_indices())
        if not idx:
            return
        self.panel_entries = [e for i, e in enumerate(self.panel_entries) if i not in idx]
        self._refresh_panel_tree()
        self._clear_panel_result()

    def _panel_clear(self):
        if self.panel_entries and messagebox.askyesno("Panel leeren", "Alle Zeilen aus dem Panel entfernen?"):
            self.panel_entries = []
            self._refresh_panel_tree()
            self._clear_panel_result()

    def _panel_save(self):
        if not self.panel_entries:
            messagebox.showwarning("Leeres Panel", "Das Panel enthält noch keine Messgrößen.")
            return
        os.makedirs(self.panels_dir, exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Panel als Vorlage speichern", defaultextension=".json", filetypes=[("Panel-Vorlage", "*.json")],
            initialdir=self.panels_dir, initialfile="panel.json",
        )
        if not path:
            return
        save_panel(self.panel_entries, path)
        self.panel_status_var.set(f"Panel gespeichert: {path}")

    def _panel_load(self):
        path = filedialog.askopenfilename(
            title="Panel-Vorlage laden", filetypes=[("Panel-Vorlage", "*.json")],
            initialdir=self.panels_dir if os.path.isdir(self.panels_dir) else self.input_dir,
        )
        if not path:
            return
        try:
            entries = load_panel(path)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Laden fehlgeschlagen", f"Panel konnte nicht gelesen werden:\n{exc}")
            return
        self.panel_entries = entries
        self._refresh_panel_tree()
        self._clear_panel_result()
        known = set(self._panel_gate_names()) | {PANEL_POP_ALL, PANEL_REF_PARENT, PANEL_REF_ALL}
        missing = sorted({e.population for e in entries if e.population not in known}
                         | {e.reference for e in entries if e.reference not in known})
        msg = f"{len(entries)} Messgröße(n) geladen."
        if missing:
            messagebox.showwarning(
                "Gate-Sets fehlen",
                "Diese Populationen/Bezüge gibt es in diesem Experiment nicht als gespeichertes Gate-Set — "
                "ihre Spalten bleiben leer, bis Gate-Sets mit genau diesem Namen gespeichert sind:\n\n"
                + "\n".join(missing)
            )
        self.panel_status_var.set(msg)

    def _clear_panel_result(self):
        if not hasattr(self, "panel_result_tree"):
            return
        self.panel_result = None
        self.panel_info = None
        self.panel_result_tree.delete(*self.panel_result_tree.get_children())
        self.panel_result_tree["columns"] = ()

    def _panel_compute(self) -> bool:
        if not self.panel_entries:
            messagebox.showwarning("Leeres Panel", "Bitte zuerst Messgrößen zum Panel hinzufügen.")
            return False
        if not self.fcs_paths:
            messagebox.showwarning("Keine Dateien", "Es ist kein Experiment mit .fcs-Dateien geladen.")
            return False

        total = len(self.fcs_paths)

        def files():
            for path in self.fcs_paths:
                fcs = self._get_data(path)
                self.panel_marker_names.update(fcs.marker_names or {})
                yield fcs.filename, fcs.df

        def progress(i, filename):
            self.panel_status_var.set(f"Berechne {i + 1}/{total}: {filename} ...")
            self.update_idletasks()

        self.config(cursor="watch")
        self.update_idletasks()
        try:
            wide, info, warnings = compute_panel(
                files(), self.existing_gate_sets, self.panel_entries,
                marker_labels=self.panel_marker_names, progress=progress,
            )
        except Exception as exc:
            messagebox.showerror("Fehler bei der Berechnung", str(exc))
            self.panel_status_var.set("Berechnung fehlgeschlagen.")
            return False
        finally:
            self.config(cursor="")

        self.panel_result, self.panel_info = wide, info
        self._show_panel_result(wide)
        status = f"{len(wide)} Datei(en) × {len(wide.columns) - 1} Spalten berechnet."
        if warnings:
            status += f"  {len(warnings)} Hinweis(e) — leere Zellen = nicht berechenbar."
            shown = "\n".join(warnings[:25]) + (f"\n… und {len(warnings) - 25} weitere" if len(warnings) > 25 else "")
            messagebox.showwarning("Hinweise zur Berechnung", shown)
        self.panel_status_var.set(status)
        return True

    def _show_panel_result(self, wide: pd.DataFrame):
        tree = self.panel_result_tree
        tree.delete(*tree.get_children())
        col_ids = [f"c{i}" for i in range(len(wide.columns))]
        tree["columns"] = col_ids
        for cid, name in zip(col_ids, wide.columns):
            tree.heading(cid, text=name)
            tree.column(cid, width=240 if cid == "c0" else 150, anchor="w" if cid == "c0" else "e", stretch=False)
        for _, row in wide.iterrows():
            vals = []
            for v in row.tolist():
                if isinstance(v, float):
                    v = "" if np.isnan(v) else f"{v:.4g}"
                vals.append(v)
            tree.insert("", "end", values=vals)

    def _panel_export(self):
        if self.panel_result is None and not self._panel_compute():
            return
        path = filedialog.asksaveasfilename(
            title="Panel-Export speichern", defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")],
            initialdir=self._ensure_exports_dir(), initialfile="panel_export.xlsx",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                self.panel_result.to_csv(path, index=False, encoding="utf-8-sig")
            else:
                definition = panel_definition_table(self.panel_entries, self.panel_marker_names)
                write_panel_xlsx(path, self.panel_result, self.panel_info, definition,
                                 heatmap=self.panel_heatmap_var.get())
        except ImportError:
            messagebox.showerror(
                "openpyxl fehlt",
                "Für den Excel-Export wird das Paket 'openpyxl' benötigt. start.bat installiert es beim nächsten "
                "Start automatisch nach. Alternativ jetzt als .csv speichern."
            )
            return
        except PermissionError:
            messagebox.showerror("Datei gesperrt", f"Die Datei ist vermutlich noch in Excel geöffnet:\n{path}")
            return
        messagebox.showinfo("Exportiert", f"Panel-Export gespeichert unter:\n{path}")
