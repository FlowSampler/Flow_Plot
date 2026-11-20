"""Histogramm-Overlay tab: histograms of several files on top of each other."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from gating import apply_named_gate_set, node_mask
from histogram_overlay import (
    COLOR_SCHEMES as OVERLAY_COLOR_SCHEMES,
    MODES as OVERLAY_MODES,
    NORMALIZATIONS as OVERLAY_NORMALIZATIONS,
    SCHEME_FILE,
    SCHEME_SINGLE,
    DisplayTransform,
    OverlaySeries,
    compute_overlay,
    default_color,
    draw_overlay,
    scheme_colors,
)
from kinetics import compute_ratio
from transforms import TRANSFORM_NAMES
from gui.constants import (
    DEFAULT_RATIO_PAIR,
    GATE_SOURCE_LIVE,
    GATE_SOURCE_NONE,
    OVERLAY_LINESTYLES,
    DEFAULT_LINESTYLE,
)


class OverlayTabMixin:
    """Builds and draws the overlay tab (file list, colors/line styles, CSV export)."""

    def _build_overlay_tab(self, parent):
        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=4, pady=4)
        redraw = lambda *a: self._schedule_overlay_redraw()  # noqa: E731

        ttk.Label(controls, text="Quelle:").grid(row=0, column=0, sticky="w")
        self.ov_mode_var = tk.StringVar(value="single")
        mode_frame = ttk.Frame(controls)
        mode_frame.grid(row=0, column=1, columnspan=3, sticky="w")
        for text, value in (("Einzelkanal", "single"), ("Ratio (Kanal A / Kanal B)", "ratio")):
            ttk.Radiobutton(
                mode_frame, text=text, variable=self.ov_mode_var, value=value, command=self._update_overlay_mode
            ).pack(side="left", padx=(0, 8))

        ttk.Label(controls, text="Kanal A:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ov_channel_a_var = tk.StringVar(value=DEFAULT_RATIO_PAIR[0])
        self.ov_channel_a_combo = ttk.Combobox(controls, textvariable=self.ov_channel_a_var, state="readonly", width=16)
        self.ov_channel_a_combo.grid(row=1, column=1, padx=4, pady=(6, 0))
        self.ov_channel_a_combo.bind("<<ComboboxSelected>>", redraw)

        self.ov_channel_b_label = ttk.Label(controls, text="Kanal B (Nenner):")
        self.ov_channel_b_label.grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_channel_b_var = tk.StringVar(value=DEFAULT_RATIO_PAIR[1])
        self.ov_channel_b_combo = ttk.Combobox(controls, textvariable=self.ov_channel_b_var, state="readonly", width=16)
        self.ov_channel_b_combo.grid(row=1, column=3, padx=4, pady=(6, 0))
        self.ov_channel_b_combo.bind("<<ComboboxSelected>>", redraw)

        ov_min_label = ttk.Label(controls, text="Min. Signal:")
        ov_min_label.grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_min_signal_var = tk.StringVar(value="1.0")
        ov_min_entry = ttk.Entry(controls, textvariable=self.ov_min_signal_var, width=6)
        ov_min_entry.grid(row=1, column=5, padx=4, pady=(6, 0))
        self.ov_ratio_widgets = [self.ov_channel_b_label, self.ov_channel_b_combo, ov_min_label, ov_min_entry]

        ttk.Label(controls, text="Population:").grid(row=1, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_gate_source_var = tk.StringVar(value=GATE_SOURCE_LIVE)
        self.ov_gate_source_combo = ttk.Combobox(
            controls, textvariable=self.ov_gate_source_var, state="readonly", width=30
        )
        self.ov_gate_source_combo.grid(row=1, column=7, columnspan=3, padx=4, pady=(6, 0), sticky="w")
        self.ov_gate_source_combo.bind("<<ComboboxSelected>>", redraw)

        ttk.Label(controls, text="Darstellung:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.ov_display_var = tk.StringVar(value=OVERLAY_MODES[0])
        display_combo = ttk.Combobox(
            controls, textvariable=self.ov_display_var, state="readonly", values=OVERLAY_MODES, width=16
        )
        display_combo.grid(row=2, column=1, padx=4, pady=(6, 0))
        display_combo.bind("<<ComboboxSelected>>", lambda e: self._update_overlay_visibility())

        ttk.Label(controls, text="Normierung:").grid(row=2, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_norm_var = tk.StringVar(value=OVERLAY_NORMALIZATIONS[0])
        norm_combo = ttk.Combobox(
            controls, textvariable=self.ov_norm_var, state="readonly", values=OVERLAY_NORMALIZATIONS, width=20
        )
        norm_combo.grid(row=2, column=3, padx=4, pady=(6, 0))
        norm_combo.bind("<<ComboboxSelected>>", redraw)

        # Offset Y: only for "Offset"/"Staggered"; Versatz X: only for "Staggered"
        ov_offset_label = ttk.Label(controls, text="Offset Y (%):")
        ov_offset_label.grid(row=2, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_offset_var = tk.StringVar(value="60")
        ov_offset_entry = ttk.Entry(controls, textvariable=self.ov_offset_var, width=6)
        ov_offset_entry.grid(row=2, column=5, padx=4, pady=(6, 0))
        self.ov_offset_widgets = [ov_offset_label, ov_offset_entry]

        ov_stagger_label = ttk.Label(controls, text="Versatz X (%):")
        ov_stagger_label.grid(row=2, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_stagger_var = tk.StringVar(value="5")
        ov_stagger_entry = ttk.Entry(controls, textvariable=self.ov_stagger_var, width=6)
        ov_stagger_entry.grid(row=2, column=7, padx=4, pady=(6, 0), sticky="w")
        self.ov_stagger_widgets = [ov_stagger_label, ov_stagger_entry]

        ttk.Label(controls, text="X-Transform:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.ov_transform_var = tk.StringVar(value="Logicle")
        tr_combo = ttk.Combobox(
            controls, textvariable=self.ov_transform_var, state="readonly", values=TRANSFORM_NAMES, width=10
        )
        tr_combo.grid(row=3, column=1, padx=4, pady=(6, 0))
        tr_combo.bind("<<ComboboxSelected>>", redraw)

        ttk.Label(controls, text="Bins:").grid(row=3, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_bins_var = tk.StringVar(value="256")
        ttk.Entry(controls, textvariable=self.ov_bins_var, width=6).grid(row=3, column=3, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(controls, text="Glättung (Bins):").grid(row=3, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_smooth_var = tk.StringVar(value="1.5")
        ttk.Entry(controls, textvariable=self.ov_smooth_var, width=6).grid(row=3, column=5, padx=4, pady=(6, 0))

        ov_alpha_label = ttk.Label(controls, text="Deckkraft Füllung:")
        ov_alpha_label.grid(row=3, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_alpha_var = tk.StringVar(value="0.35")
        ov_alpha_entry = ttk.Entry(controls, textvariable=self.ov_alpha_var, width=6)
        ov_alpha_entry.grid(row=3, column=7, padx=4, pady=(6, 0), sticky="w")
        self.ov_alpha_widgets = [ov_alpha_label, ov_alpha_entry]  # only when "Gefüllt"

        check_row = ttk.Frame(controls)
        check_row.grid(row=4, column=0, columnspan=10, sticky="w", pady=(6, 0))
        self.ov_fill_var = tk.BooleanVar(value=True)
        self.ov_reverse_var = tk.BooleanVar(value=False)
        self.ov_stats_var = tk.BooleanVar(value=True)
        for text, var in (
            ("Gefüllt", self.ov_fill_var),
            ("Reihenfolge umkehren", self.ov_reverse_var),
            ("n / Median in Legende", self.ov_stats_var),
        ):
            ttk.Checkbutton(check_row, text=text, variable=var, command=redraw).pack(side="left", padx=(0, 12))
        self.ov_fill_var.trace_add("write", lambda *a: self._update_overlay_visibility(redraw=False))

        ttk.Label(check_row, text="X-Achse Min:").pack(side="left", padx=(12, 0))
        self.ov_x_min_var = tk.StringVar(value="")
        ttk.Entry(check_row, textvariable=self.ov_x_min_var, width=8).pack(side="left", padx=(4, 8))
        ttk.Label(check_row, text="Max:").pack(side="left")
        self.ov_x_max_var = tk.StringVar(value="")
        ttk.Entry(check_row, textvariable=self.ov_x_max_var, width=8).pack(side="left", padx=(4, 8))
        ttk.Button(check_row, text="Ausreißer abschneiden (0.5–99.5 %)", command=self._overlay_autofit_x).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(check_row, text="Auto", command=self._overlay_reset_x).pack(side="left")

        for v in (
            self.ov_min_signal_var, self.ov_offset_var, self.ov_stagger_var, self.ov_bins_var,
            self.ov_smooth_var, self.ov_alpha_var, self.ov_x_min_var, self.ov_x_max_var,
        ):
            v.trace_add("write", redraw)

        color_row = ttk.Frame(controls)
        color_row.grid(row=5, column=0, columnspan=10, sticky="w", pady=(6, 0))
        ttk.Label(color_row, text="Farbschema:").pack(side="left")
        self.ov_scheme_var = tk.StringVar(value=SCHEME_FILE)
        scheme_combo = ttk.Combobox(
            color_row, textvariable=self.ov_scheme_var, state="readonly", values=OVERLAY_COLOR_SCHEMES, width=24
        )
        scheme_combo.pack(side="left", padx=(4, 8))
        scheme_combo.bind("<<ComboboxSelected>>", lambda e: self._on_overlay_scheme_changed())
        self.ov_single_color_btn = tk.Button(
            color_row, text="Einheitsfarbe wählen…", command=self._overlay_pick_single_color,
            bg=self.ov_single_color, fg="white", activebackground=self.ov_single_color, relief="groove",
        )
        self.ov_single_color_btn.pack(side="left", padx=(0, 16))
        self.ov_scheme_var.trace_add("write", lambda *a: self._update_overlay_visibility(redraw=False))

        ttk.Label(
            controls,
            text="Overlay = übereinander · Offset = vertikal versetzt · Staggered = vertikal + horizontal versetzt · "
            "Getrennt = eigenes Panel je Datei. Die erste Datei liegt vorne/unten. \"Live\" wendet den aktuell "
            "angezeigten Knoten des Gate-Baums auf JEDE Datei an. Farben/Linien: Dateien markieren und unter 'Stil der markierten Dateien' setzen (Schema "
            "\"Dateifarben\"), eine Einheitsfarbe oder ein Farbverlauf von der ersten zur letzten Kurve.",
            foreground="#555", wraplength=1000,
        ).grid(row=6, column=0, columnspan=10, sticky="w", pady=(4, 0))

        body = ttk.PanedWindow(parent, orient="horizontal")
        body.pack(fill="both", expand=True, padx=4, pady=4)

        files_frame = ttk.Frame(body, width=260)
        body.add(files_frame, weight=0)
        ttk.Label(files_frame, text="Dateien im Overlay", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(
            files_frame,
            text="Strg/Shift-Klick = Mehrfachauswahl\nFarbe/Linie unten gilt für alle markierten Dateien",
            foreground="#555", justify="left"
        ).pack(anchor="w")
        list_container = ttk.Frame(files_frame)
        list_container.pack(fill="both", expand=True, pady=4)
        self.ov_file_listbox = tk.Listbox(list_container, selectmode="extended", exportselection=False, width=36)
        ov_vsb = ttk.Scrollbar(list_container, orient="vertical", command=self.ov_file_listbox.yview)
        self.ov_file_listbox.configure(yscrollcommand=ov_vsb.set)
        self.ov_file_listbox.pack(side="left", fill="both", expand=True)
        ov_vsb.pack(side="right", fill="y")
        self.ov_file_listbox.bind("<<ListboxSelect>>", self._on_overlay_files_selected)
        self.ov_file_listbox.bind("<Double-Button-1>", self._overlay_pick_color)

        # Style of the MARKED files: color + line style, so curves can be
        # told apart by more than color (e.g. same color = same condition,
        # dashed = second parameter / timepoint).
        style_box = ttk.LabelFrame(files_frame, text="Stil der markierten Dateien")
        style_box.pack(fill="x", pady=(0, 4))
        self.ov_style_color_btn = tk.Button(
            style_box, text="Farbe…", command=self._overlay_pick_color_for_selected, relief="groove", width=8,
        )
        self.ov_style_color_btn.grid(row=0, column=0, padx=4, pady=4, sticky="w")
        ttk.Label(style_box, text="Linie:").grid(row=0, column=1, sticky="w")
        self.ov_linestyle_var = tk.StringVar(value=DEFAULT_LINESTYLE)
        ls_combo = ttk.Combobox(
            style_box, textvariable=self.ov_linestyle_var, state="readonly",
            values=list(OVERLAY_LINESTYLES), width=18,
        )
        ls_combo.grid(row=0, column=2, padx=4, pady=4, sticky="w")
        ls_combo.bind("<<ComboboxSelected>>", lambda e: self._overlay_set_linestyle_for_selected())

        sel_btns = ttk.Frame(files_frame)
        sel_btns.pack(fill="x")
        ttk.Button(sel_btns, text="Alle", command=lambda: self._overlay_select(lambda p: True)).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(sel_btns, text="Keine", command=lambda: self._overlay_select(lambda p: False)).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(
            sel_btns, text="Aktuelle", command=lambda: self._overlay_select(lambda p: p == self.current_path)
        ).pack(side="left", expand=True, fill="x")

        filter_row = ttk.Frame(files_frame)
        filter_row.pack(fill="x", pady=(4, 0))
        ttk.Label(filter_row, text="Name enthält:").pack(side="left")
        self.ov_filter_var = tk.StringVar(value="")
        ttk.Entry(filter_row, textvariable=self.ov_filter_var, width=12).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(filter_row, text="Auswählen", command=self._overlay_select_by_filter).pack(side="left")
        ttk.Button(files_frame, text="Farben & Linien zurücksetzen", command=self._overlay_reset_colors).pack(
            fill="x", pady=(4, 0)
        )

        plot_frame = ttk.Frame(body)
        body.add(plot_frame, weight=1)
        self.ov_figure = Figure(figsize=(8, 6))
        self.ov_canvas = FigureCanvasTkAgg(self.ov_figure, master=plot_frame)
        self.ov_canvas.get_tk_widget().pack(fill="both", expand=True)
        ov_toolbar_row = ttk.Frame(plot_frame)
        ov_toolbar_row.pack(fill="x")
        ov_toolbar = NavigationToolbar2Tk(self.ov_canvas, ov_toolbar_row, pack_toolbar=False)
        ov_toolbar.update()
        ov_toolbar.pack(side="left")
        ttk.Button(
            ov_toolbar_row, text="Plot in Zwischenablage kopieren",
            command=lambda: self._copy_to_clipboard(self.ov_figure, self.ov_status_var)
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            ov_toolbar_row, text="Plot exportieren...",
            command=lambda: self._open_plot_export(self.ov_figure, self.ov_canvas, "histogramm", self.ov_status_var)
        ).pack(side="left", padx=(4, 0))
        ttk.Button(ov_toolbar_row, text="Histogramm-Daten als CSV", command=self._export_overlay_csv).pack(
            side="left", padx=(4, 0)
        )

        self.ov_status_var = tk.StringVar(value="Dateien links auswählen.")
        ttk.Label(plot_frame, textvariable=self.ov_status_var, anchor="w").pack(fill="x", padx=4, pady=(0, 4))

        self._update_overlay_mode(redraw=False)
        self._refresh_overlay_file_list(select_paths=set())

    def _update_overlay_mode(self, redraw: bool = True):
        self._set_shown(self.ov_ratio_widgets, self.ov_mode_var.get() == "ratio")
        self._update_overlay_visibility(redraw=redraw)

    def _update_overlay_visibility(self, redraw: bool = True):
        """Only show the overlay settings the current choices actually use."""
        display = self.ov_display_var.get()
        self._set_shown(self.ov_offset_widgets, display in ("Offset", "Staggered"))
        self._set_shown(self.ov_stagger_widgets, display == "Staggered")
        self._set_shown(self.ov_alpha_widgets, self.ov_fill_var.get())
        if self.ov_scheme_var.get() == SCHEME_SINGLE:
            self.ov_single_color_btn.pack(side="left", padx=(0, 16))
        else:
            self.ov_single_color_btn.pack_forget()
        if redraw:
            self._schedule_overlay_redraw()

    def _overlay_color(self, path: str) -> str:
        if path in self.ov_colors:
            return self.ov_colors[path]
        return default_color(self.fcs_paths.index(path) if path in self.fcs_paths else 0)

    def _overlay_selected_paths(self) -> list[str]:
        return [self.fcs_paths[i] for i in self.ov_file_listbox.curselection() if i < len(self.fcs_paths)]

    def _refresh_overlay_file_list(self, select_paths: set[str] | None = None):
        """Rebuild the overlay file list (keeps the current selection unless
        select_paths is given). Each row is tinted in that file's curve color."""
        if select_paths is None:
            select_paths = set(self._overlay_selected_paths())
        lb = self.ov_file_listbox
        lb.delete(0, "end")
        for i, p in enumerate(self.fcs_paths):
            marker = OVERLAY_LINESTYLES[self._overlay_linestyle_name(p)][1]
            lb.insert("end", os.path.relpath(p, self.input_dir) + (f"   [{marker}]" if marker else ""))
            color = self._overlay_color(p)
            lb.itemconfig(i, foreground=color, selectbackground=color, selectforeground="white")
            if p in select_paths:
                lb.selection_set(i)

    def _overlay_select(self, predicate):
        self._refresh_overlay_file_list(select_paths={p for p in self.fcs_paths if predicate(p)})
        self._schedule_overlay_redraw()

    def _overlay_select_by_filter(self):
        needle = self.ov_filter_var.get().strip().lower()
        if not needle:
            return
        self._overlay_select(lambda p: needle in os.path.basename(p).lower())

    def _overlay_pick_color(self, event):
        idx = self.ov_file_listbox.nearest(event.y)
        if idx < 0 or idx >= len(self.fcs_paths):
            return
        from tkinter import colorchooser

        path = self.fcs_paths[idx]
        _, hex_color = colorchooser.askcolor(
            color=self._overlay_color(path), title=f"Farbe für {os.path.basename(path)}", parent=self
        )
        if not hex_color:
            return
        self.ov_colors[path] = hex_color
        self.ov_scheme_var.set(SCHEME_FILE)  # a per-file pick only shows under the per-file scheme
        selected = set(self._overlay_selected_paths()) | {path}
        self._refresh_overlay_file_list(select_paths=selected)
        self._schedule_overlay_redraw()

    def _overlay_linestyle_name(self, path: str) -> str:
        name = self.ov_linestyles.get(path, DEFAULT_LINESTYLE)
        return name if name in OVERLAY_LINESTYLES else DEFAULT_LINESTYLE

    def _on_overlay_files_selected(self, _event=None):
        """Show the style of the (first) marked file in the style box, then redraw."""
        paths = self._overlay_selected_paths()
        if paths:
            self.ov_linestyle_var.set(self._overlay_linestyle_name(paths[0]))
            color = self._overlay_color(paths[0])
            self.ov_style_color_btn.configure(bg=color, activebackground=color, fg="white")
        self._schedule_overlay_redraw()

    def _overlay_pick_color_for_selected(self):
        paths = self._overlay_selected_paths()
        if not paths:
            messagebox.showinfo("Keine Datei markiert", "Bitte zuerst eine oder mehrere Dateien in der Liste markieren.")
            return
        from tkinter import colorchooser

        title = f"Farbe für {os.path.basename(paths[0])}" if len(paths) == 1 else f"Farbe für {len(paths)} Dateien"
        _, hex_color = colorchooser.askcolor(color=self._overlay_color(paths[0]), title=title, parent=self)
        if not hex_color:
            return
        for p in paths:
            self.ov_colors[p] = hex_color
        self.ov_style_color_btn.configure(bg=hex_color, activebackground=hex_color, fg="white")
        self.ov_scheme_var.set(SCHEME_FILE)  # per-file colors only show under the per-file scheme
        self._refresh_overlay_file_list(select_paths=set(paths))
        self._schedule_overlay_redraw()

    def _overlay_set_linestyle_for_selected(self):
        paths = self._overlay_selected_paths()
        if not paths:
            messagebox.showinfo("Keine Datei markiert", "Bitte zuerst eine oder mehrere Dateien in der Liste markieren.")
            return
        name = self.ov_linestyle_var.get()
        for p in paths:
            if name == DEFAULT_LINESTYLE:
                self.ov_linestyles.pop(p, None)
            else:
                self.ov_linestyles[p] = name
        self._refresh_overlay_file_list(select_paths=set(paths))
        self._schedule_overlay_redraw()

    def _overlay_reset_colors(self):
        self.ov_colors.clear()
        self.ov_linestyles.clear()
        self.ov_linestyle_var.set(DEFAULT_LINESTYLE)
        self.ov_scheme_var.set(SCHEME_FILE)
        self._refresh_overlay_file_list()
        self._schedule_overlay_redraw()

    def _on_overlay_scheme_changed(self):
        # Back to per-file colors: restore the list tint (a gradient/single
        # color scheme re-tints the selected rows on every redraw instead).
        self._refresh_overlay_file_list()
        self._schedule_overlay_redraw()

    def _overlay_pick_single_color(self):
        from tkinter import colorchooser

        _, hex_color = colorchooser.askcolor(color=self.ov_single_color, title="Einheitsfarbe für alle Kurven", parent=self)
        if not hex_color:
            return
        self.ov_single_color = hex_color
        self.ov_single_color_btn.configure(bg=hex_color, activebackground=hex_color)
        self.ov_scheme_var.set(SCHEME_SINGLE)
        self._on_overlay_scheme_changed()

    def _schedule_overlay_redraw(self, *_):
        """Debounced: typing into an entry field fires once per keystroke,
        and every redraw re-histograms all selected files."""
        if self._ov_redraw_job is not None:
            self.after_cancel(self._ov_redraw_job)
        self._ov_redraw_job = self.after(250, self._redraw_overlay)

    def _overlay_population_mask(self, df, filename: str, source: str) -> tuple[np.ndarray, str]:
        if source == GATE_SOURCE_LIVE:
            base = self._base_mask_for(df, filename)
            return node_mask(df, self.gate_tree, self.current_node_id, base_mask=base), \
                self.gate_tree[self.current_node_id].name
        if source == GATE_SOURCE_NONE:
            return np.ones(len(df), dtype=bool), "ungegated"
        mask, applied = apply_named_gate_set(df, filename, self.existing_gate_sets, source)
        return mask, (applied[0] if applied else f"{source} (kein Treffer)")

    def _overlay_collect_series(self) -> tuple[list[OverlaySeries], str, list[str]]:
        """Per selected file: gated population -> finite values of the chosen
        channel/ratio. Returns (series, x_label, skipped filenames)."""
        source = self.ov_gate_source_var.get()
        mode = self.ov_mode_var.get()
        ch_a, ch_b = self.ov_channel_a_var.get(), self.ov_channel_b_var.get()
        min_signal = self._parse_optional_float(self.ov_min_signal_var.get())
        min_signal = 1.0 if min_signal is None else min_signal
        x_label = ch_a if mode == "single" else f"Ratio {ch_a} / {ch_b}"

        paths = self._overlay_selected_paths()
        if self.ov_reverse_var.get():
            paths = paths[::-1]
        series, skipped, series_paths = [], [], []
        for path in paths:
            fcs = self._get_data(path)
            df = fcs.df
            needed = [ch_a] if mode == "single" else [ch_a, ch_b]
            if any(c not in df.columns for c in needed):
                skipped.append(fcs.filename)
                continue
            mask, _ = self._overlay_population_mask(df, fcs.filename, source)
            gated = df[mask]
            if mode == "single":
                values = gated[ch_a].to_numpy(dtype=float)
            else:
                values = compute_ratio(gated, ch_a, ch_b, min_signal=min_signal).to_numpy(dtype=float)
            values = values[np.isfinite(values)]
            if not len(values):
                skipped.append(fcs.filename)
                continue
            series.append(OverlaySeries(
                label=os.path.splitext(fcs.filename)[0], values=values,
                color=self._overlay_color(path), n_gated=int(mask.sum()),
                linestyle=OVERLAY_LINESTYLES[self._overlay_linestyle_name(path)][0],
            ))
            series_paths.append(path)

        # Scheme colors are assigned by position among the PLOTTED curves, so a
        # gradient always spans first -> last curve; the file list rows are
        # tinted to match so it stays a legend.
        colors = scheme_colors(len(series), self.ov_scheme_var.get(), self.ov_single_color)
        if colors is not None:
            for s, c, p in zip(series, colors, series_paths):
                s.color = c
                i = self.fcs_paths.index(p)
                self.ov_file_listbox.itemconfig(i, foreground=c, selectbackground=c, selectforeground="white")
        return series, x_label, skipped

    def _overlay_display_transform(self, series: list[OverlaySeries]) -> DisplayTransform:
        channel = self.ov_channel_a_var.get() if self.ov_mode_var.get() == "single" else None
        return self._make_display_transform(self.ov_transform_var.get(), channel, [s.values for s in series])

    def _redraw_overlay(self):
        self._ov_redraw_job = None
        if not hasattr(self, "ov_figure") or self._tab_redraw.get(self.notebook.select()) != self._redraw_overlay:
            return
        series, x_label, skipped = self._overlay_collect_series()

        def num(var, default, lo=None, hi=None):
            v = self._parse_optional_float(var.get())
            v = default if v is None else v
            if lo is not None:
                v = max(lo, v)
            if hi is not None:
                v = min(hi, v)
            return v

        x_lo = self._parse_optional_float(self.ov_x_min_var.get())
        x_hi = self._parse_optional_float(self.ov_x_max_var.get())
        if x_lo is not None and x_hi is not None and x_lo >= x_hi:
            x_lo = x_hi = None

        try:
            tr = self._overlay_display_transform(series)
            result = compute_overlay(
                series, tr, n_bins=int(num(self.ov_bins_var, 256, 4, 4096)), lo=x_lo, hi=x_hi,
                normalization=self.ov_norm_var.get(), smooth_sigma=num(self.ov_smooth_var, 0.0, 0.0),
            )
            pop = self.ov_gate_source_var.get()
            if pop == GATE_SOURCE_LIVE:
                pop = f"Live: {self.gate_tree[self.current_node_id].name}"
            x_limits = None
            if len(result.centers_raw):
                x_limits = (
                    x_lo if x_lo is not None else float(result.centers_raw[0]),
                    x_hi if x_hi is not None else float(result.centers_raw[-1]),
                )
            draw_overlay(
                self.ov_figure, result, tr, self.ov_display_var.get(), x_label,
                title=f"Population: {pop}",
                offset_frac=num(self.ov_offset_var, 60, 0) / 100.0,
                stagger_frac=num(self.ov_stagger_var, 5, 0) / 100.0,
                fill=self.ov_fill_var.get(), alpha=num(self.ov_alpha_var, 0.35, 0.0, 1.0),
                legend_stats=self.ov_stats_var.get(), x_limits=x_limits,
            )
            try:
                self.ov_figure.tight_layout()
            except Exception:
                pass
        except Exception as exc:
            self.ov_status_var.set(f"Fehler beim Zeichnen: {exc}")
            return
        self.ov_canvas.draw_idle()
        self._ov_last_result = (result, x_label) if series else None

        msg = f"{len(series)} Datei(en) im Overlay."
        n_out = sum(result.n_out_of_range)
        if n_out:
            msg += f"  {n_out} Events außerhalb der X-Grenzen nicht gezählt."
        if skipped:
            msg += f"  Übersprungen (Kanal fehlt / keine Events): {', '.join(skipped)}"
        self.ov_status_var.set(msg)

    def _overlay_autofit_x(self):
        series, _, _ = self._overlay_collect_series()
        if not series:
            return
        pooled = np.concatenate([s.values for s in series])
        lo, hi = np.percentile(pooled, [0.5, 99.5])
        self.ov_x_min_var.set(f"{lo:.4g}")
        self.ov_x_max_var.set(f"{hi:.4g}")

    def _overlay_reset_x(self):
        self.ov_x_min_var.set("")
        self.ov_x_max_var.set("")

    def _export_overlay_csv(self):
        if self._ov_last_result is None:
            messagebox.showwarning("Keine Daten", "Bitte zuerst Dateien für das Overlay auswählen.")
            return
        result, x_label = self._ov_last_result
        rows = []
        for s, y, n_out in zip(result.series, result.curves, result.n_out_of_range):
            rows.append(pd.DataFrame({
                "filename": s.label, "channel": x_label, "bin_center": result.centers_raw,
                result.y_label: y, "n_events": len(s.values), "n_out_of_range": n_out,
            }))
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="histogram_overlay.csv",
            initialdir=self._ensure_exports_dir(),
        )
        if not path:
            return
        pd.concat(rows, ignore_index=True).to_csv(path, index=False)
        self.ov_status_var.set(f"Histogramm-Daten gespeichert: {path}")
