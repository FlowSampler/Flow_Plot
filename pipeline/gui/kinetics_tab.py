"""Kinetik tab: Ca2+ kinetics (ratio over time), ridgelines, exports."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from histogram_overlay import (
    EDGE_COLORS as OVERLAY_EDGE_COLORS,
    NORMALIZATIONS as OVERLAY_NORMALIZATIONS,
    SCHEME_SINGLE,
)
from kinetics import (
    KIN_DISPLAY_MEDIAN,
    KIN_DISPLAY_RIDGE,
    KIN_DISPLAYS,
    KIN_EVENT_DISPLAYS,
    RIDGE_SCHEME_STIM,
    bin_kinetics,
    compute_ratio,
    draw_kinetics,
    draw_kinetics_ridgeline,
    event_values,
    kinetics_y_label,
    parse_baseline_duration_s,
    ridge_color_schemes,
)
from transforms import TRANSFORM_NAMES
from gui.constants import DEFAULT_RATIO_PAIR, GATE_SOURCE_LIVE


class KineticsTabMixin:
    """Builds and draws the Kinetik tab, plus its CSV/PNG exports."""

    def _build_kinetics_tab(self, parent):
        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=4, pady=4)

        ttk.Label(controls, text="Quelle:").grid(row=0, column=0, sticky="w")
        self.kin_mode_var = tk.StringVar(value="ratio")
        mode_frame = ttk.Frame(controls)
        mode_frame.grid(row=0, column=1, columnspan=3, sticky="w")
        ttk.Radiobutton(
            mode_frame, text="Einzelkanal", variable=self.kin_mode_var, value="single", command=self._update_kin_mode
        ).pack(side="left")
        ttk.Radiobutton(
            mode_frame, text="Ratio (Kanal A / Kanal B)", variable=self.kin_mode_var, value="ratio",
            command=self._update_kin_mode
        ).pack(side="left", padx=(8, 0))

        ttk.Label(controls, text="Kanal A:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.kin_channel_a_var = tk.StringVar(value=DEFAULT_RATIO_PAIR[0])
        self.kin_channel_a_combo = ttk.Combobox(
            controls, textvariable=self.kin_channel_a_var, state="readonly", width=16
        )
        self.kin_channel_a_combo.grid(row=1, column=1, padx=4, pady=(6, 0))

        self.kin_channel_b_label = ttk.Label(controls, text="Kanal B (Nenner):")
        self.kin_channel_b_label.grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.kin_channel_b_var = tk.StringVar(value=DEFAULT_RATIO_PAIR[1])
        self.kin_channel_b_combo = ttk.Combobox(
            controls, textvariable=self.kin_channel_b_var, state="readonly", width=16
        )
        self.kin_channel_b_combo.grid(row=1, column=3, padx=4, pady=(6, 0))

        ttk.Label(controls, text="Bin-Breite (s):").grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.kin_bin_width_var = tk.StringVar(value="1.0")
        ttk.Entry(controls, textvariable=self.kin_bin_width_var, width=6).grid(row=1, column=5, padx=4, pady=(6, 0))

        kin_min_label = ttk.Label(controls, text="Min. Signal:")
        kin_min_label.grid(row=1, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.kin_min_signal_var = tk.StringVar(value="1.0")
        kin_min_entry = ttk.Entry(controls, textvariable=self.kin_min_signal_var, width=6)
        kin_min_entry.grid(row=1, column=7, padx=4, pady=(6, 0))
        self.kin_ratio_widgets = [self.kin_channel_b_label, self.kin_channel_b_combo, kin_min_label, kin_min_entry]

        ttk.Button(controls, text="Kinetik aktualisieren", command=self._redraw_kinetics).grid(
            row=1, column=8, padx=(12, 4), pady=(6, 0)
        )

        ttk.Label(controls, text="Population:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.kin_gate_source_var = tk.StringVar(value=GATE_SOURCE_LIVE)
        self.kin_gate_source_combo = ttk.Combobox(
            controls, textvariable=self.kin_gate_source_var, state="readonly", width=24
        )
        self.kin_gate_source_combo.grid(row=2, column=1, columnspan=2, padx=4, pady=(6, 0), sticky="w")
        self.kin_gate_source_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw_kinetics())
        ttk.Label(
            controls,
            text="(eindeutig auswählen, WELCHES gespeicherte Gate-Set gemeint ist — sonst werden "
            "alle passenden automatisch UND-verknüpft, was bei mehreren sich ausschließenden "
            "Populationen wie \"Lebend\"/\"Dead\" falsch wäre)",
            foreground="#555", wraplength=900,
        ).grid(row=3, column=0, columnspan=9, sticky="w")

        ttk.Button(controls, text="Kinetik dieser Datei exportieren (CSV)", command=self._export_kinetics_csv).grid(
            row=2, column=4, columnspan=2, padx=(12, 4), pady=(6, 0), sticky="w"
        )
        ttk.Button(
            controls, text="Kinetik ALLER Dateien exportieren (CSV, für Overlays)", command=self._export_all_kinetics_csv
        ).grid(row=2, column=6, columnspan=3, padx=(4, 4), pady=(6, 0), sticky="w")
        ttk.Label(
            controls, text="(\"alle Dateien\"/\"ALLE Dateien\" nutzen die oben gewählte Population für jede Datei)",
            foreground="#555"
        ).grid(row=4, column=0, columnspan=9, sticky="w")

        ttk.Button(
            controls, text="Diesen Plot in Zwischenablage kopieren",
            command=lambda: self._copy_to_clipboard(self.kin_figure, self.kin_status_var)
        ).grid(
            row=5, column=0, columnspan=3, padx=(0, 4), pady=(6, 0), sticky="w"
        )
        ttk.Button(
            controls, text="Alle Kinetik-Plots als PNG exportieren", command=self._export_all_kinetics_png
        ).grid(row=5, column=3, columnspan=3, padx=(4, 4), pady=(6, 0), sticky="w")
        ttk.Button(
            controls, text="Plot exportieren (Größe, Beschriftung)...",
            command=lambda: self._open_plot_export(self.kin_figure, self.kin_canvas, "kinetik", self.kin_status_var)
        ).grid(row=5, column=6, columnspan=3, padx=(4, 4), pady=(6, 0), sticky="w")
        ttk.Label(
            controls, text="(Zwischenablage: direkt mit Strg+V in PowerPoint/Word einfügbar, kein Screenshot nötig)",
            foreground="#555"
        ).grid(row=6, column=0, columnspan=9, sticky="w")

        kin_axis_row = ttk.Frame(controls)
        kin_axis_row.grid(row=7, column=0, columnspan=9, sticky="w", pady=(6, 0))
        ttk.Label(kin_axis_row, text="Zeit-Achse Min:").pack(side="left")
        self.kin_x_min_var = tk.StringVar(value="")
        ttk.Entry(kin_axis_row, textvariable=self.kin_x_min_var, width=8).pack(side="left", padx=(4, 8))
        ttk.Label(kin_axis_row, text="Max:").pack(side="left")
        self.kin_x_max_var = tk.StringVar(value="")
        ttk.Entry(kin_axis_row, textvariable=self.kin_x_max_var, width=8).pack(side="left", padx=(4, 16))
        ttk.Label(kin_axis_row, text="Werte-Achse Min:").pack(side="left")
        self.kin_y_min_var = tk.StringVar(value="")
        ttk.Entry(kin_axis_row, textvariable=self.kin_y_min_var, width=8).pack(side="left", padx=(4, 8))
        ttk.Label(kin_axis_row, text="Max:").pack(side="left")
        self.kin_y_max_var = tk.StringVar(value="")
        ttk.Entry(kin_axis_row, textvariable=self.kin_y_max_var, width=8).pack(side="left", padx=(4, 16))
        for v in (self.kin_x_min_var, self.kin_x_max_var, self.kin_y_min_var, self.kin_y_max_var):
            v.trace_add("write", lambda *a: self._redraw_kinetics())
        ttk.Button(
            kin_axis_row, text="Zurücksetzen (Auto)", command=self._reset_kinetics_axis_limits
        ).pack(side="left")
        ttk.Label(
            controls, text="(Achsen-Limits: leer = automatisch — zum Reinzoomen in einen Zeit-/Wertebereich "
            "einfach Min/Max eintragen; die Toolbar-Lupe unten funktioniert ebenfalls)",
            foreground="#555"
        ).grid(row=8, column=0, columnspan=9, sticky="w")

        kin_disp_row = ttk.Frame(controls)
        kin_disp_row.grid(row=9, column=0, columnspan=9, sticky="w", pady=(6, 0))
        ttk.Label(kin_disp_row, text="Darstellung:").pack(side="left")
        self.kin_display_var = tk.StringVar(value=KIN_DISPLAY_MEDIAN)
        kin_disp_combo = ttk.Combobox(
            kin_disp_row, textvariable=self.kin_display_var, state="readonly", values=KIN_DISPLAYS, width=26
        )
        kin_disp_combo.pack(side="left", padx=(4, 12))
        kin_disp_combo.bind("<<ComboboxSelected>>", lambda e: self._on_kin_display_changed())
        # Event-display options live in their own frame so the whole group can
        # be hidden (pack_forget) when a non-event display is chosen.
        self.kin_event_frame = ttk.Frame(kin_disp_row)
        self.kin_event_frame.pack(side="left")
        kin_disp_row = self.kin_event_frame
        self.kin_overlay_median_var = tk.BooleanVar(value=True)
        self.kin_event_widgets = [
            ttk.Checkbutton(kin_disp_row, text="Median-Linie überlagern", variable=self.kin_overlay_median_var,
                            command=self._redraw_kinetics),
        ]
        self.kin_point_size_var = tk.StringVar(value="2")
        self.kin_point_alpha_var = tk.StringVar(value="0.3")
        self.kin_max_points_var = tk.StringVar(value="200000")
        for text, var in (("Punktgröße:", self.kin_point_size_var), ("Deckkraft:", self.kin_point_alpha_var),
                          ("Max. Punkte:", self.kin_max_points_var)):
            self.kin_event_widgets.append(ttk.Label(kin_disp_row, text=text))
            self.kin_event_widgets.append(ttk.Entry(kin_disp_row, textvariable=var, width=8))
            var.trace_add("write", lambda *a: self._redraw_kinetics())
        self.kin_point_color_btn = tk.Button(
            kin_disp_row, text="Punktfarbe…", command=self._kin_pick_point_color, bg=self.kin_point_color,
            fg="white", activebackground=self.kin_point_color, relief="groove",
        )
        self.kin_event_widgets.append(self.kin_point_color_btn)
        self.kin_event_widgets.append(ttk.Button(
            kin_disp_row, text="Einzel-Events als CSV", command=self._export_kinetics_events_csv
        ))
        for w in self.kin_event_widgets:
            w.pack(side="left", padx=(0, 6))
        self.kin_event_help = ttk.Label(
            controls,
            text="(Einzel-Events: jedes Event als Punkt über der Zeit — bei sehr vielen Events wird für die Anzeige "
            "zufällig auf \"Max. Punkte\" reduziert, der CSV-Export enthält immer alle. \"Dichte\" zeigt alle Events als "
            "Zeit×Wert-Heatmap im Farbschema des Gating-Tabs.)",
            foreground="#555", wraplength=1000,
        )
        self.kin_event_help.grid(row=10, column=0, columnspan=9, sticky="w")

        # --- Ridgeline settings (only active for the Ridgeline display)
        ridge_row = ttk.Frame(controls)
        ridge_row.grid(row=11, column=0, columnspan=9, sticky="w", pady=(6, 0))
        ridge_row2 = ttk.Frame(controls)
        ridge_row2.grid(row=12, column=0, columnspan=9, sticky="w", pady=(4, 0))
        redraw = lambda *a: self._redraw_kinetics()  # noqa: E731
        self.kin_ridge_widgets = []

        def add(w, padx=(0, 6)):
            w.pack(side="left", padx=padx)
            self.kin_ridge_widgets.append(w)
            return w

        ttk.Label(ridge_row, text="Ridgeline:", font=("", 9, "bold")).pack(side="left", padx=(0, 8))
        self.kin_ridge_window_var = tk.StringVar(value="10")
        self.kin_ridge_offset_var = tk.StringVar(value="60")
        self.kin_ridge_smooth_var = tk.StringVar(value="1.5")
        self.kin_ridge_alpha_var = tk.StringVar(value="0.8")
        for text, var in (("Zeitfenster (s):", self.kin_ridge_window_var), ("Abstand (%):", self.kin_ridge_offset_var),
                          ("Glättung:", self.kin_ridge_smooth_var), ("Deckkraft:", self.kin_ridge_alpha_var)):
            add(ttk.Label(ridge_row, text=text))
            add(ttk.Entry(ridge_row, textvariable=var, width=6), padx=(0, 10))
            var.trace_add("write", redraw)
        add(ttk.Label(ridge_row, text="Werte-Achse:"))
        self.kin_ridge_transform_var = tk.StringVar(value="Linear")
        add(ttk.Combobox(ridge_row, textvariable=self.kin_ridge_transform_var, state="readonly",
                         values=TRANSFORM_NAMES, width=9), padx=(0, 10)).bind("<<ComboboxSelected>>", redraw)
        add(ttk.Label(ridge_row, text="Normierung:"))
        self.kin_ridge_norm_var = tk.StringVar(value=OVERLAY_NORMALIZATIONS[0])
        add(ttk.Combobox(ridge_row, textvariable=self.kin_ridge_norm_var, state="readonly",
                         values=OVERLAY_NORMALIZATIONS, width=20)).bind("<<ComboboxSelected>>", redraw)

        add(ttk.Label(ridge_row2, text="Farbschema:"), padx=(0, 4))
        self.kin_ridge_scheme_var = tk.StringVar(value=RIDGE_SCHEME_STIM)
        add(ttk.Combobox(ridge_row2, textvariable=self.kin_ridge_scheme_var, state="readonly",
                         values=ridge_color_schemes(), width=20), padx=(0, 8)).bind("<<ComboboxSelected>>", redraw)
        # Color buttons sit in their own frame: only the ones the chosen scheme
        # actually uses are shown (pre/post for "Vor / nach Stimulus", the
        # single color for "Einheitsfarbe", none for a color gradient).
        ridge_color_frame = ttk.Frame(ridge_row2)
        ridge_color_frame.pack(side="left")
        self.kin_ridge_colors = {"pre": "#8c8c8c", "post": "#d62728", "single": "#1f77b4"}
        self.kin_ridge_color_btns = {}
        for key, text in (("pre", "Farbe vor Stimulus…"), ("post", "Farbe nach Stimulus…"), ("single", "Einheitsfarbe…")):
            c = self.kin_ridge_colors[key]
            btn = tk.Button(ridge_color_frame, text=text, bg=c, fg="white", activebackground=c, relief="groove",
                            command=lambda k=key: self._kin_pick_ridge_color(k))
            self.kin_ridge_color_btns[key] = btn
        self.kin_ridge_scheme_var.trace_add("write", lambda *a: self._update_kin_ridge_color_buttons())
        self._update_kin_ridge_color_buttons()
        add(ttk.Label(ridge_row2, text="Kontur:"), padx=(8, 4))
        self.kin_ridge_edge_var = tk.StringVar(value=OVERLAY_EDGE_COLORS[0])
        add(ttk.Combobox(ridge_row2, textvariable=self.kin_ridge_edge_var, state="readonly",
                         values=OVERLAY_EDGE_COLORS, width=18), padx=(0, 10)).bind("<<ComboboxSelected>>", redraw)
        self.kin_ridge_fill_var = tk.BooleanVar(value=True)
        self.kin_ridge_early_top_var = tk.BooleanVar(value=True)
        self.kin_ridge_median_var = tk.BooleanVar(value=True)
        for text, var in (("Gefüllt", self.kin_ridge_fill_var), ("Früh oben", self.kin_ridge_early_top_var),
                          ("Median-Punkte", self.kin_ridge_median_var)):
            add(ttk.Checkbutton(ridge_row2, text=text, variable=var, command=redraw))
        ridge_help = ttk.Label(
            controls,
            text="(Ridgeline: ein Kamm pro Zeitfenster, X = Wert. Eine Fenstergrenze liegt genau auf der "
            "Stimulus-Zeit aus dem Dateinamen. \"Werte-Achse Min/Max\" oben begrenzt hier die X-Achse, "
            "\"Zeit-Achse Min/Max\" den Zeitbereich der Kämme.)",
            foreground="#555", wraplength=1000,
        )
        ridge_help.grid(row=13, column=0, columnspan=9, sticky="w")
        self.kin_ridge_rows = [ridge_row, ridge_row2, ridge_help]

        self.kin_figure = Figure(figsize=(8, 6))
        self.kin_ax = self.kin_figure.add_subplot(111)
        self.kin_canvas = FigureCanvasTkAgg(self.kin_figure, master=parent)
        self.kin_canvas.get_tk_widget().pack(fill="both", expand=True)
        kin_toolbar = NavigationToolbar2Tk(self.kin_canvas, parent, pack_toolbar=False)
        kin_toolbar.update()
        kin_toolbar.pack(fill="x")

        self.kin_status_var = tk.StringVar(value="Bereit.")
        ttk.Label(parent, textvariable=self.kin_status_var, anchor="w").pack(fill="x", padx=4, pady=(0, 4))

        self._update_kin_mode()
        self._on_kin_display_changed(redraw=False)

    def _on_kin_display_changed(self, redraw: bool = True):
        # Only show the settings the chosen display actually uses.
        display = self.kin_display_var.get()
        is_events = display in KIN_EVENT_DISPLAYS
        if is_events:
            self.kin_event_frame.pack(side="left")
        else:
            self.kin_event_frame.pack_forget()
        self._set_shown([self.kin_event_help], is_events)
        self._set_shown(self.kin_ridge_rows, display == KIN_DISPLAY_RIDGE)
        if redraw:
            self._redraw_kinetics()

    def _update_kin_ridge_color_buttons(self):
        scheme = self.kin_ridge_scheme_var.get()
        shown = {"pre": scheme == RIDGE_SCHEME_STIM, "post": scheme == RIDGE_SCHEME_STIM,
                 "single": scheme == SCHEME_SINGLE}
        for key, btn in self.kin_ridge_color_btns.items():  # re-pack in fixed order
            btn.pack_forget()
        for key, btn in self.kin_ridge_color_btns.items():
            if shown[key]:
                btn.pack(side="left", padx=(0, 4))

    def _kin_pick_ridge_color(self, key: str):
        from tkinter import colorchooser

        titles = {"pre": "Farbe der Kämme VOR dem Stimulus", "post": "Farbe der Kämme NACH dem Stimulus",
                  "single": "Einheitsfarbe aller Kämme"}
        _, hex_color = colorchooser.askcolor(color=self.kin_ridge_colors[key], title=titles[key], parent=self)
        if not hex_color:
            return
        self.kin_ridge_colors[key] = hex_color
        self.kin_ridge_color_btns[key].configure(bg=hex_color, activebackground=hex_color)
        if key == "single":
            self.kin_ridge_scheme_var.set(SCHEME_SINGLE)
        elif self.kin_ridge_scheme_var.get() != RIDGE_SCHEME_STIM:
            self.kin_ridge_scheme_var.set(RIDGE_SCHEME_STIM)
        self._redraw_kinetics()

    def _kin_ridge_options(self) -> dict:
        def num(var, default, lo, hi):
            v = self._parse_optional_float(var.get())
            return min(hi, max(lo, default if v is None else v))

        return {
            "window_s": num(self.kin_ridge_window_var, 10.0, 0.01, 1e6),
            "offset_frac": num(self.kin_ridge_offset_var, 60.0, 5.0, 1000.0) / 100.0,
            "smooth_sigma": num(self.kin_ridge_smooth_var, 1.5, 0.0, 50.0),
            "alpha": num(self.kin_ridge_alpha_var, 0.8, 0.0, 1.0),
            "normalization": self.kin_ridge_norm_var.get(),
            "fill": self.kin_ridge_fill_var.get(),
            "edge": self.kin_ridge_edge_var.get(),
            "scheme": self.kin_ridge_scheme_var.get(),
            "single_color": self.kin_ridge_colors["single"],
            "pre_color": self.kin_ridge_colors["pre"],
            "post_color": self.kin_ridge_colors["post"],
            "early_on_top": self.kin_ridge_early_top_var.get(),
            "show_median": self.kin_ridge_median_var.get(),
        }

    def _kin_axis_limits(self) -> tuple[float | None, float | None, float | None, float | None]:
        return tuple(self._parse_optional_float(v.get()) for v in (
            self.kin_x_min_var, self.kin_x_max_var, self.kin_y_min_var, self.kin_y_max_var))

    def _render_kinetics(self, ax, filename: str, binned, t_ev, v_ev, value_label: str, channel: str | None,
                         title: str) -> str:
        """Draw one kinetic (any display) onto a cleared ax, with the tab's
        settings and axis limits. Shared by the on-screen plot and the batch
        PNG export so both always look identical. Returns a status note."""
        opts = self._kin_draw_options()
        x_lo, x_hi, y_lo, y_hi = self._kin_axis_limits()
        baseline_end_s = parse_baseline_duration_s(filename)
        note = ""
        if opts["display"] == KIN_DISPLAY_RIDGE:
            tr = self._make_display_transform(self.kin_ridge_transform_var.get(), channel, [v_ev])
            info = draw_kinetics_ridgeline(
                ax, t_ev, v_ev, tr, value_limits=(y_lo, y_hi), time_limits=(x_lo, x_hi),
                baseline_end_s=baseline_end_s, **self._kin_ridge_options(),
            )
            ax.set_xlabel(value_label)
            note = f"  Ridgeline: {info['n_windows']} Zeitfenster."
            if info["n_out_of_range"]:
                note += f" {info['n_out_of_range']} Events außerhalb des Werte-Bereichs."
            if not info["stimulus_found"] and self.kin_ridge_scheme_var.get() == RIDGE_SCHEME_STIM:
                note += " Keine Stimulus-Zeit im Dateinamen gefunden → alle Kämme in \"nach\"-Farbe."
        else:
            n_drawn = draw_kinetics(ax, binned, t_ev, v_ev, y_limits=(y_lo, y_hi),
                                    baseline_end_s=baseline_end_s, **opts)
            ax.set_xlabel("Zeit (s)")
            ax.set_ylabel(kinetics_y_label(value_label, opts["display"]))
            if x_lo is not None or x_hi is not None:
                ax.set_xlim(left=x_lo, right=x_hi)
            if y_lo is not None or y_hi is not None:
                ax.set_ylim(bottom=y_lo, top=y_hi)
            if opts["display"] in KIN_EVENT_DISPLAYS and n_drawn < len(t_ev):
                if opts["display"] == KIN_EVENT_DISPLAYS[0]:
                    note = f"  Angezeigt: zufällige Stichprobe von {n_drawn} Events (Max. Punkte)."
                else:
                    note = f"  {len(t_ev) - n_drawn} Events außerhalb des Y-Bereichs der Heatmap."
        ax.set_title(title, fontsize=10)
        return note

    def _kin_pick_point_color(self):
        from tkinter import colorchooser

        _, hex_color = colorchooser.askcolor(color=self.kin_point_color, title="Farbe der Einzel-Events", parent=self)
        if not hex_color:
            return
        self.kin_point_color = hex_color
        self.kin_point_color_btn.configure(bg=hex_color, activebackground=hex_color)
        self._redraw_kinetics()

    def _kin_draw_options(self) -> dict:
        """Display settings of the Kinetik tab as kwargs for draw_kinetics()."""
        def num(var, default, lo, hi):
            v = self._parse_optional_float(var.get())
            return min(hi, max(lo, default if v is None else v))

        return {
            "display": self.kin_display_var.get(),
            "summary_overlay": self.kin_overlay_median_var.get(),
            "point_size": num(self.kin_point_size_var, 2.0, 0.1, 50.0),
            "point_alpha": num(self.kin_point_alpha_var, 0.3, 0.01, 1.0),
            "max_points": int(num(self.kin_max_points_var, 200000, 1000, 10_000_000)),
            "point_color": self.kin_point_color,
            "cmap": self._colormap(),
        }

    def _update_kin_mode(self):
        self._set_shown(self.kin_ratio_widgets, self.kin_mode_var.get() == "ratio")

    def _reset_kinetics_axis_limits(self):
        for v in (self.kin_x_min_var, self.kin_x_max_var, self.kin_y_min_var, self.kin_y_max_var):
            v.set("")

    def _redraw_kinetics(self):
        if not self.current_path:
            return
        fcs = self._get_data(self.current_path)
        df = fcs.df

        try:
            bin_width = float(self.kin_bin_width_var.get())
            min_signal = float(self.kin_min_signal_var.get())
        except ValueError:
            self.kin_status_var.set("Bin-Breite / Min. Signal müssen Zahlen sein.")
            return

        mask, pop_desc = self._population_mask(df, fcs.filename, self.kin_gate_source_var.get())
        gated_df = df[mask]

        mode = self.kin_mode_var.get()
        if mode == "single":
            channel = self.kin_channel_a_var.get()
            if channel not in gated_df.columns:
                return
            value = gated_df[channel]
            ylabel = channel
        else:
            channel = None
            ch_a, ch_b = self.kin_channel_a_var.get(), self.kin_channel_b_var.get()
            if ch_a not in gated_df.columns or ch_b not in gated_df.columns:
                return
            value = compute_ratio(gated_df, ch_a, ch_b, min_signal=min_signal)
            ylabel = f"Ratio {ch_a} / {ch_b}"

        binned = bin_kinetics(gated_df["Time"], value, bin_width_s=bin_width)
        t_ev, v_ev = event_values(gated_df["Time"], value)

        # The whole figure is rebuilt (not just ax.clear()): the Ridgeline
        # display hides spines / sets a non-linear X scale, which would
        # otherwise leak into the next display.
        self.kin_figure.clear()
        self.kin_ax = self.kin_figure.add_subplot(111)
        try:
            note = self._render_kinetics(
                self.kin_ax, fcs.filename, binned, t_ev, v_ev, ylabel, channel,
                f"{fcs.filename}\nPopulation: {pop_desc} — {mask.sum()}/{len(df)} Events",
            )
            self.kin_figure.tight_layout()
        except Exception as exc:
            self.kin_status_var.set(f"Fehler beim Zeichnen: {exc}")
            return
        self.kin_canvas.draw_idle()

        self.kin_status_var.set(
            f"Population: {pop_desc} — {len(binned)} Zeit-Bins, {len(t_ev)} Events mit gültigem Wert.{note}"
        )

        self._kin_last_events = (t_ev, v_ev)
        self._kin_last_binned = binned
        self._kin_last_label = ylabel
        self._kin_last_filename = fcs.filename
        self._kin_last_population = pop_desc

    def _export_kinetics_csv(self):
        binned = getattr(self, "_kin_last_binned", None)
        if binned is None or len(binned) == 0:
            messagebox.showwarning("Keine Daten", "Bitte zuerst die Kinetik berechnen (Tab anzeigen/aktualisieren).")
            return
        stem = os.path.splitext(self._kin_last_filename)[0]
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=f"{stem}_kinetics.csv",
            initialdir=self._ensure_exports_dir(),
        )
        if not path:
            return
        out = binned.copy()
        out.insert(0, "filename", self._kin_last_filename)
        out.insert(1, "population", self._kin_last_population)
        out.insert(2, "channel", self._kin_last_label)
        out.to_csv(path, index=False)
        messagebox.showinfo(
            "Exportiert", f"Kinetik gespeichert unter:\n{path}\n\n(Population: {self._kin_last_population})"
        )

    def _export_kinetics_events_csv(self):
        events = self._kin_last_events
        if events is None or not len(events[0]):
            messagebox.showwarning("Keine Daten", "Bitte zuerst die Kinetik berechnen (Tab anzeigen/aktualisieren).")
            return
        stem = os.path.splitext(self._kin_last_filename)[0]
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=f"{stem}_kinetics_events.csv",
            initialdir=self._ensure_exports_dir(),
        )
        if not path:
            return
        t_ev, v_ev = events
        pd.DataFrame({
            "filename": self._kin_last_filename, "population": self._kin_last_population,
            "channel": self._kin_last_label, "time_s": t_ev, "value": v_ev,
        }).to_csv(path, index=False)
        messagebox.showinfo("Exportiert", f"{len(t_ev)} Einzel-Events gespeichert unter:\n{path}")

    def _export_all_kinetics_csv(self):
        source = self._require_named_population(self.kin_gate_source_var)
        if source is None:
            return
        mode = self.kin_mode_var.get()
        ch_a = self.kin_channel_a_var.get()
        ch_b = self.kin_channel_b_var.get() if mode == "ratio" else None
        try:
            bin_width = float(self.kin_bin_width_var.get())
            min_signal = float(self.kin_min_signal_var.get())
        except ValueError:
            messagebox.showwarning("Ungültige Eingabe", "Bin-Breite / Min. Signal müssen Zahlen sein.")
            return

        rows = []
        for path in self.fcs_paths:
            fcs = self._get_data(path)
            df = fcs.df
            mask, applied = self._named_population_mask(df, fcs.filename, source)
            gated_df = df[mask]
            if mode == "single":
                if ch_a not in gated_df.columns:
                    continue
                value, label = gated_df[ch_a], ch_a
            else:
                if ch_a not in gated_df.columns or ch_b not in gated_df.columns:
                    continue
                value = compute_ratio(gated_df, ch_a, ch_b, min_signal=min_signal)
                label = f"Ratio {ch_a} / {ch_b}"
            binned = bin_kinetics(gated_df["Time"], value, bin_width_s=bin_width)
            if len(binned) == 0:
                continue
            binned = binned.copy()
            binned.insert(0, "filename", fcs.filename)
            binned.insert(1, "population", source)
            binned.insert(2, "channel", label)
            binned.insert(3, "gates_applied", ";".join(applied) or "none")
            rows.append(binned)

        if not rows:
            messagebox.showwarning("Keine Daten", f"Für keine Datei konnte die Population '{source}' angewendet werden.")
            return

        combined = pd.concat(rows, ignore_index=True)
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=f"all_files_kinetics_{source}.csv",
            initialdir=self._ensure_exports_dir(),
        )
        if not path:
            return
        combined.to_csv(path, index=False)
        messagebox.showinfo(
            "Exportiert",
            f"Kinetik (Population: {source}) für {len(rows)} Dateien gespeichert unter:\n{path}\n\n"
            "Long-Format (eine Zeile pro Datei & Zeit-Bin) — geeignet zum Filtern/Pivotieren für Overlays."
        )

    def _export_all_kinetics_png(self):
        source = self._require_named_population(self.kin_gate_source_var)
        if source is None:
            return
        mode = self.kin_mode_var.get()
        ch_a = self.kin_channel_a_var.get()
        ch_b = self.kin_channel_b_var.get() if mode == "ratio" else None
        try:
            bin_width = float(self.kin_bin_width_var.get())
            min_signal = float(self.kin_min_signal_var.get())
        except ValueError:
            messagebox.showwarning("Ungültige Eingabe", "Bin-Breite / Min. Signal müssen Zahlen sein.")
            return

        out_dir = filedialog.askdirectory(
            title="Ordner für PNG-Export wählen", initialdir=self._ensure_exports_dir()
        )
        if not out_dir:
            return

        count = 0
        for path in self.fcs_paths:
            fcs = self._get_data(path)
            df = fcs.df
            mask, _ = self._named_population_mask(df, fcs.filename, source)
            gated_df = df[mask]
            if mode == "single":
                if ch_a not in gated_df.columns:
                    continue
                value, label, channel = gated_df[ch_a], ch_a, ch_a
            else:
                if ch_a not in gated_df.columns or ch_b not in gated_df.columns:
                    continue
                value = compute_ratio(gated_df, ch_a, ch_b, min_signal=min_signal)
                label, channel = f"Ratio {ch_a} / {ch_b}", None
            binned = bin_kinetics(gated_df["Time"], value, bin_width_s=bin_width)
            if len(binned) == 0:
                continue
            t_ev, v_ev = event_values(gated_df["Time"], value)
            # Same display settings and axis limits as the on-screen plot;
            # Ridgelines get more height so the ridges stay readable.
            is_ridge = self.kin_display_var.get() == KIN_DISPLAY_RIDGE
            fig = Figure(figsize=(8, 8) if is_ridge else (8, 5))
            ax = fig.add_subplot(111)
            self._render_kinetics(
                ax, fcs.filename, binned, t_ev, v_ev, label, channel,
                f"{fcs.filename}\nPopulation: {source} — {mask.sum()}/{len(df)} Events",
            )
            fig.tight_layout()
            stem = os.path.splitext(fcs.filename)[0]
            safe_source = "".join(c if c.isalnum() or c in "-_" else "_" for c in source)
            out_path = os.path.join(out_dir, f"{stem}_{safe_source}_kinetics.png")
            fig.savefig(out_path, dpi=150)
            count += 1

        messagebox.showinfo("Exportiert", f"{count} Kinetik-PNG(s) (Population: {source}) gespeichert in:\n{out_dir}")
