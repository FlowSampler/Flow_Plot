"""Interactive GUI for defining pregates on FCS files.

Navigate between files in a list, pick the X/Y channels from dropdowns, pick a
gate shape (Polygon / Rectangle / Ellipse), draw it on the density plot. Gates
form a tree (like FlowJo): drawing a gate adds a CHILD of the currently viewed
population without leaving it, so you can draw several sibling gates (Child A,
Child B, ...) at the same hierarchy level. Double-click inside a drawn gate
(or double-click it in the "Kind-Gates" list) to descend into that population;
the breadcrumb at the top of the right panel navigates back up. Saving a
gate-set stores the path from the root down to whichever node is currently
displayed, with a scope (all files / a group / just this one file).

Run with:
    python gating_gui.py
    python gating_gui.py --input-dir "..\\Ca2+_kinetic" --gates-dir "..\\gates"
"""
from __future__ import annotations

import argparse
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib
import numpy as np
import pandas as pd
from matplotlib import patheffects
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.colors import ListedColormap, LogNorm
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse as EllipsePatch
from matplotlib.patches import Polygon as PolygonPatch
from matplotlib.widgets import EllipseSelector, PolygonSelector, RectangleSelector
from scipy.ndimage import gaussian_filter

from gating import (
    GateSet,
    GateStep,
    add_child_node,
    apply_named_gate_set,
    delete_gate_tree,
    list_gate_set_names,
    list_saved_strategies,
    load_all_gate_sets,
    load_gate_tree,
    new_gate_tree,
    node_mask,
    node_path,
    node_steps,
    point_in_step,
    remove_subtree,
    resolve_gate_sets_for_file,
    save_gate_set,
    save_gate_tree,
)
from clipboard_utils import copy_figure_to_clipboard
from histogram_overlay import (
    COLOR_SCHEMES as OVERLAY_COLOR_SCHEMES,
    EDGE_COLORS as OVERLAY_EDGE_COLORS,
    MODES as OVERLAY_MODES,
    NORMALIZATIONS as OVERLAY_NORMALIZATIONS,
    NORM_COUNT,
    SCHEME_FILE,
    SCHEME_SINGLE,
    DisplayTransform,
    OverlaySeries,
    compute_overlay,
    default_color,
    draw_overlay,
    scheme_colors,
)
from io_utils import FcsData, find_fcs_files, load_fcs
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
    value_stats,
)
from transforms import TRANSFORM_NAMES, apply_axis_scale

SHAPES = ["Polygon", "Rectangle", "Ellipse", "Quadrant", "Quadrant (Winkel)"]
QUADRANT_SHAPES = ("Quadrant", "Quadrant (Winkel)")
SCOPES = ["all", "group", "file"]
HIST_1D = "Histogramm (1D)"
PLOT_TYPES = ["Pseudocolor", "Contour", "Density", "Scatter", HIST_1D]
# Display name -> matplotlib colormap for the 2D density plots.
COLORMAPS = {
    "Turbo (FlowJo-ähnlich)": "turbo",
    "Viridis": "viridis",
    "Magma": "magma_r",
    "Blau (einfarbig)": "Blues",
    "Graustufen": "Greys",
}
DEFAULT_CHANNEL_PAIR = ("FSC-A", "SSC-A")
DEFAULT_RATIO_PAIR = ("PerCP-A", "Qdot 655-A")

# Options for the "which population?" selector in the Kinetik/Statistik tabs.
# Picking a specific saved gate-set NAME avoids silently ANDing together every
# saved gate-set that happens to match a file's scope — important once you
# have several mutually exclusive named populations (e.g. "Lebend"/"Dead").
GATE_SOURCE_LIVE = "Live (Gating-Tab-Ansicht, nur aktuelle Datei)"
GATE_SOURCE_NONE = "Ungegated (alle Events)"


class GatingApp(ttk.Frame):
    def __init__(self, master, input_dir: str, gates_dir: str):
        super().__init__(master)
        self.input_dir = input_dir
        self.gates_dir = gates_dir
        self.exports_dir = self._derive_sibling_dir(gates_dir, "exports")
        self.strategies_dir = self._derive_sibling_dir(gates_dir, "strategies")
        try:
            self.fcs_paths = find_fcs_files(input_dir, recursive=True)
        except FileNotFoundError:
            self.fcs_paths = []
        self.data_cache: dict[str, FcsData] = {}
        self.existing_gate_sets = load_all_gate_sets(gates_dir)

        self.current_path: str | None = None
        self.gate_tree = new_gate_tree()
        self.current_node_id = "root"
        self._node_counter = 0
        self.active_selector = None
        self.pending_shape_coords = None  # captured at draw-start for the step being drawn
        self.editing_node_id: str | None = None  # set while reshaping an existing gate in place
        self._picking_point_center = False  # True while waiting for the click that sets a quadrant gate's center
        self.current_strategy_name: str | None = None  # name of the loaded/saved gating strategy, None = unsaved
        self.current_strategy_path: str | None = None
        self._strategy_dirty = False  # True once gate_tree has changed since the last strategy save/load
        self._strategies: list[tuple[str, str]] = []  # (name, path) cache backing the strategy combobox
        self._kin_last_binned = None
        self._kin_last_label = None
        self._kin_last_filename = None
        self._kin_last_population = None
        self._kin_last_events = None  # (time_s, value) arrays of the last drawn kinetic
        self.kin_point_color = "#1f4e8c"
        self.ov_colors: dict[str, str] = {}  # per-file color overrides for the overlay tab (path -> hex)
        self.ov_single_color = "#1f77b4"  # curve color for the "Einheitsfarbe" scheme
        self._ov_redraw_job = None
        self._ov_last_result = None

        self._build_ui()
        if self.fcs_paths:
            self._select_file_index(0)
        else:
            self.status_var.set(
                "Kein Experiment-Ordner geladen (oder keine .fcs-Dateien darin gefunden). "
                "Bitte oben links 'Experiment-Ordner wählen...' klicken."
            )
        self._bind_file_navigation_keys()

    @staticmethod
    def _derive_sibling_dir(gates_dir: str, name: str) -> str:
        """A default folder next to the gates folder — inside the experiment
        folder when gates_dir is '<experiment>/gates'."""
        parent = os.path.dirname(os.path.normpath(gates_dir)) or "."
        return os.path.join(parent, name)

    def _ensure_exports_dir(self) -> str:
        os.makedirs(self.exports_dir, exist_ok=True)
        return self.exports_dir

    def _bind_file_navigation_keys(self):
        """Left/Right arrow keys cycle through ALL files, wrapping around at
        either end, no matter which widget currently has focus — except while
        typing in a text-entry-like widget, where the arrow keys keep their
        normal cursor-movement behavior instead."""

        def handle(direction):
            def _handler(event):
                if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text, tk.Spinbox)):
                    return
                if direction < 0:
                    self._prev_file()
                else:
                    self._next_file()
                return "break"
            return _handler

        self.master.bind_all("<Left>", handle(-1))
        self.master.bind_all("<Right>", handle(1))

    # ---------------------------------------------------------- UI layout
    def _build_ui(self):
        self.pack(fill="both", expand=True)

        root_pane = ttk.PanedWindow(self, orient="horizontal")
        root_pane.pack(fill="both", expand=True)

        # ---- left: file navigator ----
        left = ttk.Frame(root_pane, width=260)
        root_pane.add(left, weight=0)

        ttk.Label(left, text="Experiment", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        self.experiment_dir_var = tk.StringVar(value=self.input_dir)
        ttk.Label(
            left, textvariable=self.experiment_dir_var, foreground="#555", wraplength=250, justify="left"
        ).pack(anchor="w", padx=4)
        ttk.Button(left, text="Experiment-Ordner wählen...", command=self._choose_experiment_folder).pack(
            fill="x", padx=4, pady=(2, 2)
        )
        ttk.Button(left, text="Dateien neu einlesen (Ordner erneut scannen)", command=self._rescan_files).pack(
            fill="x", padx=4, pady=(0, 2)
        )
        ttk.Label(
            left,
            text="(lädt automatisch alle .fcs-Dateien aus diesem Ordner + Unterordnern; neu hinzugefügte Dateien "
            "erscheinen erst nach 'Dateien neu einlesen', nicht von selbst; Gates und Exporte werden hier in "
            "'gates/' bzw. 'exports/' gespeichert)",
            foreground="#555", wraplength=250
        ).pack(anchor="w", padx=4, pady=(0, 8))

        ttk.Label(left, text="FCS Dateien", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        self.file_listbox = tk.Listbox(left, exportselection=False)
        for p in self.fcs_paths:
            self.file_listbox.insert("end", os.path.relpath(p, self.input_dir))
        self.file_listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_selected)

        nav = ttk.Frame(left)
        nav.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(nav, text="<< Prev", command=self._prev_file).pack(side="left", expand=True, fill="x")
        ttk.Button(nav, text="Next >>", command=self._next_file).pack(side="left", expand=True, fill="x")

        ttk.Label(left, text="Existierende Gate-Sets", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(8, 0))
        self.existing_listbox = tk.Listbox(left, height=6)
        self._refresh_existing_listbox()
        self.existing_listbox.pack(fill="x", padx=4, pady=4)
        self.show_existing_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            left, text="Auf aktuelle Datei anwenden (Vorschau)",
            variable=self.show_existing_var, command=self._redraw
        ).pack(anchor="w", padx=4, pady=(0, 2))
        ttk.Label(
            left,
            text="(nur wirksam, solange am Baum noch nichts gezeichnet ist — als Basis, auf der man weitergated; "
            "sobald ein Gate im Baum existiert, zählt nur noch der Baum selbst, damit Parent/Geschwister nach dem "
            "Speichern korrekt navigierbar bleiben)",
            foreground="#555", wraplength=250
        ).pack(anchor="w", padx=4, pady=(0, 8))

        # ---- right side: tabs for Gating, Kinetik and Statistik ----
        self.notebook = ttk.Notebook(root_pane)
        root_pane.add(self.notebook, weight=1)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        gating_tab = ttk.Frame(self.notebook)
        kinetics_tab = ttk.Frame(self.notebook)
        stats_tab = ttk.Frame(self.notebook)
        overlay_tab = ttk.Frame(self.notebook)
        self.notebook.add(gating_tab, text="Gating")
        self.notebook.add(kinetics_tab, text="Kinetik")
        self.notebook.add(stats_tab, text="Statistik")
        self.notebook.add(overlay_tab, text="Histogramm-Overlay")

        self._build_gating_tab(gating_tab)
        self._build_kinetics_tab(kinetics_tab)
        self._build_stats_tab(stats_tab)
        self._build_overlay_tab(overlay_tab)
        self._refresh_gate_source_combos()

    def _on_tab_changed(self, _event):
        idx = self.notebook.index(self.notebook.select())
        if idx == 1:
            self._redraw_kinetics()
        elif idx == 2:
            self._redraw_stats()
        elif idx == 3:
            self._redraw_overlay()

    def _build_gating_tab(self, parent):
        gating_pane = ttk.PanedWindow(parent, orient="horizontal")
        gating_pane.pack(fill="both", expand=True)

        # ---- center: plot ----
        center = ttk.Frame(gating_pane)
        gating_pane.add(center, weight=1)

        controls = ttk.Frame(center)
        controls.pack(fill="x", padx=4, pady=4)

        ttk.Label(controls, text="X-Kanal:").grid(row=0, column=0, sticky="w")
        self.x_channel_var = tk.StringVar(value=DEFAULT_CHANNEL_PAIR[0])
        self.x_channel_combo = ttk.Combobox(controls, textvariable=self.x_channel_var, state="readonly", width=14)
        self.x_channel_combo.grid(row=0, column=1, padx=4)
        self.x_channel_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw())

        ttk.Label(controls, text="Y-Kanal:").grid(row=0, column=2, sticky="w")
        self.y_channel_var = tk.StringVar(value=DEFAULT_CHANNEL_PAIR[1])
        self.y_channel_combo = ttk.Combobox(controls, textvariable=self.y_channel_var, state="readonly", width=14)
        self.y_channel_combo.grid(row=0, column=3, padx=4)
        self.y_channel_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw())

        ttk.Label(controls, text="Gate-Form:").grid(row=0, column=4, sticky="w", padx=(12, 0))
        self.shape_var = tk.StringVar(value=SHAPES[0])
        self.shape_combo = ttk.Combobox(controls, textvariable=self.shape_var, state="readonly", values=SHAPES, width=16)
        self.shape_combo.grid(row=0, column=5, padx=4)
        self.shape_combo.bind("<<ComboboxSelected>>", self._on_shape_changed)

        self.draw_button = ttk.Button(controls, text="Gate zeichnen starten", command=self._start_drawing)
        self.draw_button.grid(row=0, column=6, padx=(12, 4))
        self.finish_button = ttk.Button(controls, text="Schritt übernehmen", command=self._finish_step, state="disabled")
        self.finish_button.grid(row=0, column=7, padx=4)
        self.cancel_button = ttk.Button(controls, text="Abbrechen", command=self._cancel_drawing, state="disabled")
        self.cancel_button.grid(row=0, column=8, padx=4)

        ttk.Label(controls, text="Darstellung:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.plot_type_var = tk.StringVar(value=PLOT_TYPES[0])
        plot_type_combo = ttk.Combobox(
            controls, textvariable=self.plot_type_var, state="readonly", values=PLOT_TYPES, width=14
        )
        plot_type_combo.grid(row=1, column=1, padx=4, pady=(6, 0))
        plot_type_combo.bind("<<ComboboxSelected>>", lambda e: self._on_plot_type_changed())

        ttk.Label(controls, text="Farbskala:").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.colormap_var = tk.StringVar(value=next(iter(COLORMAPS)))
        colormap_combo = ttk.Combobox(
            controls, textvariable=self.colormap_var, state="readonly", values=list(COLORMAPS), width=20
        )
        colormap_combo.grid(row=1, column=3, columnspan=2, padx=4, pady=(6, 0), sticky="w")
        colormap_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        ttk.Label(
            controls, text="(Farbe = log. Event-Dichte, weiß = leer; Histogramm (1D) = X-Intensität gegen Events)",
            foreground="#555"
        ).grid(row=1, column=5, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(controls, text="Quadranten-Winkel (°):").grid(row=1, column=7, sticky="w", padx=(12, 0), pady=(6, 0))
        self.quadrant_angle_var = tk.StringVar(value="0")
        self.quadrant_angle_entry = ttk.Entry(
            controls, textvariable=self.quadrant_angle_var, width=6, state="disabled"
        )
        self.quadrant_angle_entry.grid(row=1, column=8, padx=4, pady=(6, 0), sticky="w")
        self.quadrant_angle_var.trace_add("write", lambda *a: self._redraw())

        ttk.Label(controls, text="X-Transform:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.x_transform_var = tk.StringVar(value="Linear")
        x_transform_combo = ttk.Combobox(
            controls, textvariable=self.x_transform_var, state="readonly", values=TRANSFORM_NAMES, width=10
        )
        x_transform_combo.grid(row=2, column=1, padx=4, pady=(6, 0))
        x_transform_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw())

        ttk.Label(controls, text="Y-Transform:").grid(row=2, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.y_transform_var = tk.StringVar(value="Linear")
        y_transform_combo = ttk.Combobox(
            controls, textvariable=self.y_transform_var, state="readonly", values=TRANSFORM_NAMES, width=10
        )
        y_transform_combo.grid(row=2, column=3, padx=4, pady=(6, 0))
        y_transform_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw())

        ttk.Label(controls, text="Log linthresh:").grid(row=2, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.log_linthresh_var = tk.StringVar(value="10")
        ttk.Entry(controls, textvariable=self.log_linthresh_var, width=6).grid(row=2, column=5, padx=4, pady=(6, 0))

        ttk.Label(controls, text="Logicle W:").grid(row=2, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.logicle_w_var = tk.StringVar(value="0.5")
        ttk.Entry(controls, textvariable=self.logicle_w_var, width=5).grid(row=2, column=7, padx=4, pady=(6, 0))

        ttk.Label(controls, text="Logicle M:").grid(row=2, column=8, sticky="w", padx=(12, 0), pady=(6, 0))
        self.logicle_m_var = tk.StringVar(value="4.5")
        ttk.Entry(controls, textvariable=self.logicle_m_var, width=5).grid(row=2, column=9, padx=4, pady=(6, 0))
        for v in (self.log_linthresh_var, self.logicle_w_var, self.logicle_m_var):
            v.trace_add("write", lambda *a: self._redraw())

        ttk.Label(
            controls,
            text="Linear / Log (symlog, verträgt negative Rauschwerte) / Logicle (FlowKit-Standardtransform). "
            "Ändert nur die Darstellung, Gate-Koordinaten bleiben in Rohwerten.",
            foreground="#555", wraplength=760,
        ).grid(row=3, column=0, columnspan=10, sticky="w", pady=(4, 0))

        axis_limits_row = ttk.Frame(controls)
        axis_limits_row.grid(row=4, column=0, columnspan=10, sticky="w", pady=(6, 0))

        ttk.Label(axis_limits_row, text="X-Achse Min:").pack(side="left")
        self.x_min_var = tk.StringVar(value="")
        ttk.Entry(axis_limits_row, textvariable=self.x_min_var, width=8).pack(side="left", padx=(4, 8))
        ttk.Label(axis_limits_row, text="Max:").pack(side="left")
        self.x_max_var = tk.StringVar(value="")
        ttk.Entry(axis_limits_row, textvariable=self.x_max_var, width=8).pack(side="left", padx=(4, 16))

        ttk.Label(axis_limits_row, text="Y-Achse Min:").pack(side="left")
        self.y_min_var = tk.StringVar(value="")
        ttk.Entry(axis_limits_row, textvariable=self.y_min_var, width=8).pack(side="left", padx=(4, 8))
        ttk.Label(axis_limits_row, text="Max:").pack(side="left")
        self.y_max_var = tk.StringVar(value="")
        ttk.Entry(axis_limits_row, textvariable=self.y_max_var, width=8).pack(side="left", padx=(4, 16))
        for v in (self.x_min_var, self.x_max_var, self.y_min_var, self.y_max_var):
            v.trace_add("write", lambda *a: self._redraw())

        ttk.Button(
            axis_limits_row, text="Ausreißer abschneiden (1.–99. Perzentil)", command=self._autofit_axis_limits
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            axis_limits_row, text="Achsen-Limits zurücksetzen (Auto)", command=self._reset_axis_limits
        ).pack(side="left", padx=(4, 0))

        ttk.Label(
            controls,
            text="Leer = automatisch. Ein gesetztes Limit schneidet Ausreißer an den Rand ab (statt die Achse "
            "zu sprengen) und markiert sie dort rot mit '×'.",
            foreground="#555", wraplength=760,
        ).grid(row=5, column=0, columnspan=10, sticky="w", pady=(2, 0))

        self.figure = Figure(figsize=(7, 6))
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=center)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_click)
        toolbar_row = ttk.Frame(center)
        toolbar_row.pack(fill="x")
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_row, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="left")
        ttk.Button(
            toolbar_row, text="Plot in Zwischenablage kopieren",
            command=lambda: self._copy_to_clipboard(self.figure, self.status_var)
        ).pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(
            value="Bereit. Doppelklick IN ein Gate wechselt in dessen Population. "
            "Klick AUF die rote Gate-Linie bearbeitet dessen Ecken/Ränder."
        )
        ttk.Label(center, textvariable=self.status_var, anchor="w").pack(fill="x", padx=4, pady=(0, 4))

        # ---- right: gate hierarchy navigation + save ----
        right = ttk.Frame(gating_pane, width=320)
        gating_pane.add(right, weight=0)

        ttk.Label(right, text="Gating-Strategie", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        ttk.Label(
            right,
            text="Der GESAMTE Gate-Baum (alle Äste) unter einem Namen speichern/laden — so lassen sich mehrere "
            "grundverschiedene Gating-Schemata anlegen, vergleichen und die Arbeit an einem anderen Tag fortsetzen.",
            foreground="#555", wraplength=300
        ).pack(anchor="w", padx=4)
        self.strategy_var = tk.StringVar(value="(unbenannt, nicht gespeichert)")
        self.strategy_combo = ttk.Combobox(right, textvariable=self.strategy_var, state="readonly")
        self.strategy_combo.pack(fill="x", padx=4, pady=(4, 2))
        self.strategy_combo.bind("<<ComboboxSelected>>", self._on_strategy_selected)
        strategy_btns = ttk.Frame(right)
        strategy_btns.pack(fill="x", padx=4, pady=(0, 8))
        ttk.Button(strategy_btns, text="Neu", command=self._new_strategy).pack(side="left", expand=True, fill="x")
        ttk.Button(strategy_btns, text="Speichern", command=self._save_strategy).pack(side="left", expand=True, fill="x")
        ttk.Button(
            strategy_btns, text="Speichern unter...", command=self._save_strategy_as
        ).pack(side="left", expand=True, fill="x")
        ttk.Button(strategy_btns, text="Löschen", command=self._delete_strategy).pack(side="left", expand=True, fill="x")

        ttk.Separator(right, orient="horizontal").pack(fill="x", padx=4, pady=(0, 8))

        ttk.Label(right, text="Pfad (Wurzel → aktuell)", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        self.breadcrumb_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.breadcrumb_var, foreground="#333", wraplength=300, justify="left").pack(
            anchor="w", padx=4, pady=(0, 6)
        )
        ttk.Button(right, text="▲ Eine Ebene hoch (Parent)", command=self._go_to_parent).pack(fill="x", padx=4, pady=(0, 8))

        ttk.Label(right, text="Gate-Baum (gesamte Hierarchie)", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        ttk.Label(
            right,
            text="(untereinander & eingerückt je Ebene; aktuelle Ansicht ist markiert; "
            "Doppelklick navigiert hinein)",
            foreground="#555", wraplength=300
        ).pack(anchor="w", padx=4)

        tree_container = ttk.Frame(right)
        tree_container.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree_view = ttk.Treeview(tree_container, columns=("events",), show="tree headings", height=14)
        self.tree_view.heading("#0", text="Gate")
        self.tree_view.heading("events", text="Events (% Parent)")
        self.tree_view.column("#0", width=170, stretch=True)
        self.tree_view.column("events", width=110, anchor="e", stretch=False)
        tree_vsb = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree_view.yview)
        self.tree_view.configure(yscrollcommand=tree_vsb.set)
        self.tree_view.pack(side="left", fill="both", expand=True)
        tree_vsb.pack(side="right", fill="y")
        self.tree_view.tag_configure("current", background="#cfe8ff")
        self.tree_view.bind("<Double-Button-1>", self._on_tree_double_click)

        tree_btns = ttk.Frame(right)
        tree_btns.pack(fill="x", padx=4)
        ttk.Button(tree_btns, text="In ausgewählten Knoten wechseln", command=self._navigate_selected_tree_node).pack(fill="x", pady=2)
        ttk.Button(tree_btns, text="Ausgewählten Knoten umbenennen", command=self._rename_tree_selected).pack(fill="x", pady=2)
        ttk.Button(
            tree_btns, text="Ausgewählten Knoten bearbeiten (Ecken anpassen)", command=self._edit_tree_selected
        ).pack(fill="x", pady=2)
        ttk.Button(tree_btns, text="Ausgewählten Knoten + Kinder löschen", command=self._delete_tree_selected).pack(fill="x", pady=2)
        ttk.Button(tree_btns, text="Baum zurücksetzen", command=self._reset_tree).pack(fill="x", pady=2)

        ttk.Separator(right, orient="horizontal").pack(fill="x", padx=4, pady=10)

        ttk.Label(right, text="Speichern als Gate-Set", font=("", 10, "bold")).pack(anchor="w", padx=4)
        ttk.Label(
            right, text="Speichert den Pfad Wurzel → aktuell angezeigter Knoten als Gate-Set.",
            foreground="#555", wraplength=250
        ).pack(anchor="w", padx=4, pady=(0, 4))

        form = ttk.Frame(right)
        form.pack(fill="x", padx=4, pady=4)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value="gate_set")
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(form, text="Scope:").grid(row=1, column=0, sticky="w")
        self.scope_var = tk.StringVar(value="all")
        scope_combo = ttk.Combobox(form, textvariable=self.scope_var, state="readonly", values=SCOPES, width=10)
        scope_combo.grid(row=1, column=1, sticky="ew", pady=2)
        scope_combo.bind("<<ComboboxSelected>>", lambda e: self._update_scope_hint())

        ttk.Label(form, text="Pattern (group):").grid(row=2, column=0, sticky="w")
        self.pattern_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=self.pattern_var).grid(row=2, column=1, sticky="ew", pady=2)

        form.columnconfigure(1, weight=1)

        self.scope_hint_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.scope_hint_var, foreground="#555", wraplength=250).pack(
            anchor="w", padx=4, pady=(0, 8)
        )

        ttk.Button(right, text="Gate-Set speichern", command=self._save_gate_set).pack(fill="x", padx=4, pady=(8, 4))

        self._update_scope_hint()
        self._on_shape_changed()
        self._refresh_strategy_combo()

    def _on_plot_type_changed(self):
        if self.plot_type_var.get() == HIST_1D:
            self.status_var.set(
                "Histogramm (1D): X-Kanal gegen Anzahl Events. Grau = aktuelle Population, farbige Linien = "
                "Kind-Gates (gleiche Farbe wie ihr Umriss im 2D-Plot). Zum Zeichnen eines Gates wird "
                "automatisch auf Pseudocolor umgeschaltet."
            )
        self._redraw()

    def _ensure_2d_plot_for_gating(self):
        """Gates live on an X/Y channel pair — they can't be drawn on the 1D
        histogram (its Y axis is event count, not a channel)."""
        if self.plot_type_var.get() == HIST_1D:
            self.plot_type_var.set("Pseudocolor")
            self._redraw()

    def _on_shape_changed(self, _event=None):
        """Enable the angle entry only for the tilted quadrant shape — the
        straight one is always axis-aligned (angle 0)."""
        is_angled = self.shape_var.get() == "Quadrant (Winkel)"
        self.quadrant_angle_entry.configure(state="normal" if is_angled else "disabled")

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

        ttk.Label(controls, text="Min. Signal:").grid(row=1, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.kin_min_signal_var = tk.StringVar(value="1.0")
        ttk.Entry(controls, textvariable=self.kin_min_signal_var, width=6).grid(row=1, column=7, padx=4, pady=(6, 0))

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
        ttk.Label(
            controls,
            text="(Einzel-Events: jedes Event als Punkt über der Zeit — bei sehr vielen Events wird für die Anzeige "
            "zufällig auf \"Max. Punkte\" reduziert, der CSV-Export enthält immer alle. \"Dichte\" zeigt alle Events als "
            "Zeit×Wert-Heatmap im Farbschema des Gating-Tabs.)",
            foreground="#555", wraplength=1000,
        ).grid(row=10, column=0, columnspan=9, sticky="w")

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
        self.kin_ridge_colors = {"pre": "#8c8c8c", "post": "#d62728", "single": "#1f77b4"}
        self.kin_ridge_color_btns = {}
        for key, text in (("pre", "Farbe vor Stimulus…"), ("post", "Farbe nach Stimulus…"), ("single", "Einheitsfarbe…")):
            c = self.kin_ridge_colors[key]
            btn = tk.Button(ridge_row2, text=text, bg=c, fg="white", activebackground=c, relief="groove",
                            command=lambda k=key: self._kin_pick_ridge_color(k))
            add(btn, padx=(0, 4))
            self.kin_ridge_color_btns[key] = btn
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
        ttk.Label(
            controls,
            text="(Ridgeline: ein Kamm pro Zeitfenster, X = Wert. Eine Fenstergrenze liegt genau auf der "
            "Stimulus-Zeit aus dem Dateinamen. \"Werte-Achse Min/Max\" oben begrenzt hier die X-Achse, "
            "\"Zeit-Achse Min/Max\" den Zeitbereich der Kämme.)",
            foreground="#555", wraplength=1000,
        ).grid(row=13, column=0, columnspan=9, sticky="w")

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
        display = self.kin_display_var.get()
        for widgets, active in ((self.kin_event_widgets, display in KIN_EVENT_DISPLAYS),
                                (self.kin_ridge_widgets, display == KIN_DISPLAY_RIDGE)):
            for w in widgets:
                try:
                    if isinstance(w, ttk.Combobox):
                        w.configure(state="readonly" if active else "disabled")
                    else:
                        w.configure(state="normal" if active else "disabled")
                except tk.TclError:
                    pass
        if redraw:
            self._redraw_kinetics()

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
        is_ratio = self.kin_mode_var.get() == "ratio"
        self.kin_channel_b_label.configure(text="Kanal B (Nenner):" if is_ratio else "")
        self.kin_channel_b_combo.configure(state="readonly" if is_ratio else "disabled")

    @staticmethod
    def _parse_optional_float(s: str) -> float | None:
        s = s.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _reset_kinetics_axis_limits(self):
        for v in (self.kin_x_min_var, self.kin_x_max_var, self.kin_y_min_var, self.kin_y_max_var):
            v.set("")

    def _build_stats_tab(self, parent):
        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=4, pady=4)

        ttk.Label(controls, text="Quelle:").grid(row=0, column=0, sticky="w")
        self.stat_mode_var = tk.StringVar(value="ratio")
        mode_frame = ttk.Frame(controls)
        mode_frame.grid(row=0, column=1, columnspan=4, sticky="w")
        ttk.Radiobutton(
            mode_frame, text="Einzelkanal", variable=self.stat_mode_var, value="single", command=self._update_stat_mode
        ).pack(side="left")
        ttk.Radiobutton(
            mode_frame, text="Ratio (Kanal A / Kanal B)", variable=self.stat_mode_var, value="ratio",
            command=self._update_stat_mode
        ).pack(side="left", padx=(8, 0))

        ttk.Label(controls, text="Kanal A:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.stat_channel_a_var = tk.StringVar(value=DEFAULT_RATIO_PAIR[0])
        self.stat_channel_a_combo = ttk.Combobox(
            controls, textvariable=self.stat_channel_a_var, state="readonly", width=16
        )
        self.stat_channel_a_combo.grid(row=1, column=1, padx=4, pady=(6, 0))

        self.stat_channel_b_label = ttk.Label(controls, text="Kanal B (Nenner):")
        self.stat_channel_b_label.grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.stat_channel_b_var = tk.StringVar(value=DEFAULT_RATIO_PAIR[1])
        self.stat_channel_b_combo = ttk.Combobox(
            controls, textvariable=self.stat_channel_b_var, state="readonly", width=16
        )
        self.stat_channel_b_combo.grid(row=1, column=3, padx=4, pady=(6, 0))

        ttk.Label(controls, text="Min. Signal:").grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.stat_min_signal_var = tk.StringVar(value="1.0")
        ttk.Entry(controls, textvariable=self.stat_min_signal_var, width=6).grid(row=1, column=5, padx=4, pady=(6, 0))

        ttk.Label(controls, text="Schwelle (pos/neg):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.stat_threshold_var = tk.StringVar(value="")
        ttk.Entry(controls, textvariable=self.stat_threshold_var, width=10).grid(row=2, column=1, padx=4, pady=(6, 0))
        ttk.Label(controls, text="(oder ins Histogramm klicken)", foreground="#555").grid(
            row=2, column=2, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Button(controls, text="Aktualisieren", command=self._redraw_stats).grid(
            row=2, column=4, padx=(12, 4), pady=(6, 0)
        )

        ttk.Label(controls, text="Population:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.stat_gate_source_var = tk.StringVar(value=GATE_SOURCE_LIVE)
        self.stat_gate_source_combo = ttk.Combobox(
            controls, textvariable=self.stat_gate_source_var, state="readonly", width=24
        )
        self.stat_gate_source_combo.grid(row=3, column=1, columnspan=2, padx=4, pady=(6, 0), sticky="w")
        self.stat_gate_source_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw_stats())
        ttk.Label(
            controls,
            text="(welches gespeicherte Gate-Set gemeint ist explizit wählen — sonst würden bei mehreren sich "
            "ausschließenden Populationen wie \"Lebend\"/\"Dead\" alle passenden UND-verknüpft)",
            foreground="#555", wraplength=900,
        ).grid(row=4, column=0, columnspan=6, sticky="w")

        body = ttk.PanedWindow(parent, orient="horizontal")
        body.pack(fill="both", expand=True, padx=4, pady=4)

        plot_frame = ttk.Frame(body)
        body.add(plot_frame, weight=1)
        self.stat_figure = Figure(figsize=(6, 5))
        self.stat_ax = self.stat_figure.add_subplot(111)
        self.stat_canvas = FigureCanvasTkAgg(self.stat_figure, master=plot_frame)
        self.stat_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.stat_canvas.mpl_connect("button_press_event", self._on_stat_hist_click)
        ttk.Button(
            plot_frame, text="Plot in Zwischenablage kopieren", command=lambda: self._copy_to_clipboard(self.stat_figure)
        ).pack(anchor="w", pady=(4, 0))

        info_frame = ttk.Frame(body, width=260)
        body.add(info_frame, weight=0)
        ttk.Label(info_frame, text="Statistik (aktuelle Datei)", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        self.stat_info_var = tk.StringVar(value="")
        ttk.Label(info_frame, textvariable=self.stat_info_var, justify="left").pack(anchor="w", padx=4, pady=4)

        ttk.Separator(info_frame, orient="horizontal").pack(fill="x", padx=4, pady=10)
        ttk.Label(info_frame, text="Kohorte (alle Dateien)", font=("", 10, "bold")).pack(anchor="w", padx=4)
        ttk.Label(
            info_frame, text="nutzt die oben gewählte Population\n(muss ein gespeichertes Gate-Set sein, nicht \"Live\")",
            foreground="#555", justify="left"
        ).pack(anchor="w", padx=4, pady=(0, 4))
        ttk.Button(info_frame, text="Kohorte berechnen", command=self._compute_cohort_stats).pack(fill="x", padx=4, pady=2)
        ttk.Button(info_frame, text="Als CSV exportieren", command=self._export_cohort_csv).pack(fill="x", padx=4, pady=2)

        table_frame = ttk.Frame(parent)
        table_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        columns = [
            "filename", "gates_applied", "channel", "n_total", "n_gated", "pct_gated", "n_valid",
            "threshold", "n_positive", "pct_positive", "n_negative", "pct_negative", "mean", "median",
        ]
        self.cohort_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        for c in columns:
            self.cohort_tree.heading(c, text=c)
            self.cohort_tree.column(c, width=90, anchor="center")
        self.cohort_tree.column("filename", width=220, anchor="w")
        self.cohort_tree.column("gates_applied", width=140, anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.cohort_tree.yview)
        self.cohort_tree.configure(yscrollcommand=vsb.set)
        self.cohort_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.cohort_df: pd.DataFrame | None = None
        self._update_stat_mode()

    # ---------------------------------------------------------- histogram overlay tab
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

        ttk.Label(controls, text="Min. Signal:").grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_min_signal_var = tk.StringVar(value="1.0")
        ttk.Entry(controls, textvariable=self.ov_min_signal_var, width=6).grid(row=1, column=5, padx=4, pady=(6, 0))

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
        display_combo.bind("<<ComboboxSelected>>", redraw)

        ttk.Label(controls, text="Normierung:").grid(row=2, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_norm_var = tk.StringVar(value=OVERLAY_NORMALIZATIONS[0])
        norm_combo = ttk.Combobox(
            controls, textvariable=self.ov_norm_var, state="readonly", values=OVERLAY_NORMALIZATIONS, width=20
        )
        norm_combo.grid(row=2, column=3, padx=4, pady=(6, 0))
        norm_combo.bind("<<ComboboxSelected>>", redraw)

        ttk.Label(controls, text="Offset Y (%):").grid(row=2, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_offset_var = tk.StringVar(value="60")
        ttk.Entry(controls, textvariable=self.ov_offset_var, width=6).grid(row=2, column=5, padx=4, pady=(6, 0))

        ttk.Label(controls, text="Versatz X (%):").grid(row=2, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_stagger_var = tk.StringVar(value="5")
        ttk.Entry(controls, textvariable=self.ov_stagger_var, width=6).grid(row=2, column=7, padx=4, pady=(6, 0), sticky="w")

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

        ttk.Label(controls, text="Deckkraft Füllung:").grid(row=3, column=6, sticky="w", padx=(12, 0), pady=(6, 0))
        self.ov_alpha_var = tk.StringVar(value="0.35")
        ttk.Entry(controls, textvariable=self.ov_alpha_var, width=6).grid(row=3, column=7, padx=4, pady=(6, 0), sticky="w")

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

        ttk.Label(
            controls,
            text="Overlay = übereinander · Offset = vertikal versetzt · Staggered = vertikal + horizontal versetzt · "
            "Getrennt = eigenes Panel je Datei. Die erste Datei liegt vorne/unten. \"Live\" wendet den aktuell "
            "angezeigten Knoten des Gate-Baums auf JEDE Datei an. Farben: Doppelklick auf eine Datei (Schema "
            "\"Dateifarben\"), eine Einheitsfarbe oder ein Farbverlauf von der ersten zur letzten Kurve.",
            foreground="#555", wraplength=1000,
        ).grid(row=6, column=0, columnspan=10, sticky="w", pady=(4, 0))

        body = ttk.PanedWindow(parent, orient="horizontal")
        body.pack(fill="both", expand=True, padx=4, pady=4)

        files_frame = ttk.Frame(body, width=260)
        body.add(files_frame, weight=0)
        ttk.Label(files_frame, text="Dateien im Overlay", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(
            files_frame, text="Strg/Shift-Klick = Mehrfachauswahl\nDoppelklick = Farbe ändern",
            foreground="#555", justify="left"
        ).pack(anchor="w")
        list_container = ttk.Frame(files_frame)
        list_container.pack(fill="both", expand=True, pady=4)
        self.ov_file_listbox = tk.Listbox(list_container, selectmode="extended", exportselection=False, width=36)
        ov_vsb = ttk.Scrollbar(list_container, orient="vertical", command=self.ov_file_listbox.yview)
        self.ov_file_listbox.configure(yscrollcommand=ov_vsb.set)
        self.ov_file_listbox.pack(side="left", fill="both", expand=True)
        ov_vsb.pack(side="right", fill="y")
        self.ov_file_listbox.bind("<<ListboxSelect>>", redraw)
        self.ov_file_listbox.bind("<Double-Button-1>", self._overlay_pick_color)

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
        ttk.Button(files_frame, text="Farben zurücksetzen", command=self._overlay_reset_colors).pack(fill="x", pady=(4, 0))

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
        ttk.Button(ov_toolbar_row, text="Histogramm-Daten als CSV", command=self._export_overlay_csv).pack(
            side="left", padx=(4, 0)
        )

        self.ov_status_var = tk.StringVar(value="Dateien links auswählen.")
        ttk.Label(plot_frame, textvariable=self.ov_status_var, anchor="w").pack(fill="x", padx=4, pady=(0, 4))

        self._update_overlay_mode(redraw=False)
        self._refresh_overlay_file_list(select_paths=set())

    def _update_overlay_mode(self, redraw: bool = True):
        is_ratio = self.ov_mode_var.get() == "ratio"
        self.ov_channel_b_label.configure(text="Kanal B (Nenner):" if is_ratio else "")
        self.ov_channel_b_combo.configure(state="readonly" if is_ratio else "disabled")
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
            lb.insert("end", os.path.relpath(p, self.input_dir))
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

    def _overlay_reset_colors(self):
        self.ov_colors.clear()
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

    def _make_display_transform(self, transform_name: str, channel: str | None, value_arrays) -> DisplayTransform:
        """Axis transform with the Gating tab's Log/Logicle parameters.
        ``channel`` = raw channel (its instrument range sets the Logicle
        top-of-scale) or None for a ratio (data maximum is used instead)."""
        try:
            linthresh = float(self.log_linthresh_var.get())
        except ValueError:
            linthresh = 10.0
        try:
            logicle_w = float(self.logicle_w_var.get())
        except ValueError:
            logicle_w = 0.5
        try:
            logicle_m = float(self.logicle_m_var.get())
        except ValueError:
            logicle_m = 4.5
        # Logicle top-of-scale: the channel's declared range when plotting a
        # raw channel; for a ratio (no instrument range) the data maximum.
        channel_range = None
        if channel and self.current_path:
            channel_range = (self._get_data(self.current_path).channel_ranges or {}).get(channel)
        if not channel_range:
            channel_range = max((float(np.max(np.abs(a))) for a in value_arrays if len(a)), default=262144.0)
        return DisplayTransform(
            transform_name, channel_range=channel_range,
            log_linthresh=linthresh, logicle_w=logicle_w, logicle_m=logicle_m,
        )

    def _redraw_overlay(self):
        self._ov_redraw_job = None
        if not hasattr(self, "ov_figure") or self.notebook.index(self.notebook.select()) != 3:
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

    def _update_stat_mode(self):
        is_ratio = self.stat_mode_var.get() == "ratio"
        self.stat_channel_b_label.configure(text="Kanal B (Nenner):" if is_ratio else "")
        self.stat_channel_b_combo.configure(state="readonly" if is_ratio else "disabled")

    def _stat_values(self, df: pd.DataFrame) -> tuple[pd.Series, str]:
        mode = self.stat_mode_var.get()
        if mode == "single":
            ch = self.stat_channel_a_var.get()
            return df[ch], ch
        ch_a, ch_b = self.stat_channel_a_var.get(), self.stat_channel_b_var.get()
        try:
            min_signal = float(self.stat_min_signal_var.get())
        except ValueError:
            min_signal = 1.0
        return compute_ratio(df, ch_a, ch_b, min_signal=min_signal), f"{ch_a}/{ch_b}"

    def _stat_threshold(self) -> float | None:
        thr_str = self.stat_threshold_var.get().strip()
        if not thr_str:
            return None
        try:
            return float(thr_str)
        except ValueError:
            return None

    def _population_mask(self, df, filename: str, source: str) -> tuple[np.ndarray, str]:
        """Resolve the 'Population:' selector to a mask. Returns (mask, description)."""
        if source == GATE_SOURCE_LIVE:
            return self._cumulative_pending_mask(df), self.gate_tree[self.current_node_id].name
        if source == GATE_SOURCE_NONE:
            return np.ones(len(df), dtype=bool), "ungegated"
        mask, applied = apply_named_gate_set(df, filename, self.existing_gate_sets, source)
        return mask, (applied[0] if applied else f"{source} (kein Treffer für diese Datei)")

    def _redraw_stats(self):
        if not self.current_path:
            return
        fcs = self._get_data(self.current_path)
        df = fcs.df
        mask, pop_desc = self._population_mask(df, fcs.filename, self.stat_gate_source_var.get())
        gated_df = df[mask]
        values, label = self._stat_values(gated_df)
        valid = values.dropna()
        threshold = self._stat_threshold()

        self.stat_ax.clear()
        if len(valid):
            self.stat_ax.hist(valid, bins=100, color="tab:blue", alpha=0.85)
        if threshold is not None:
            self.stat_ax.axvline(threshold, color="red", linestyle="--", linewidth=1.5)
        self.stat_ax.set_xlabel(label)
        self.stat_ax.set_ylabel("Events")
        self.stat_ax.set_title(f"{fcs.filename}\nPopulation: {pop_desc} (Klick in den Plot setzt die Schwelle)")
        self.stat_canvas.draw_idle()

        stats = value_stats(values, threshold, n_total=len(df), n_gated=len(gated_df))
        lines = [
            f"Population: {pop_desc}",
            f"Kanal/Ratio: {label}",
            f"Events gesamt: {stats['n_total']}",
            f"Events gegated: {stats['n_gated']} ({stats['pct_gated']:.1f}%)",
            f"gültige Werte: {stats['n_valid']}",
            f"Mean: {stats['mean']:.3g}   Median: {stats['median']:.3g}",
        ]
        if threshold is not None:
            lines += [
                f"Schwelle: {threshold:.3g}",
                f"positiv: {stats['n_positive']} ({stats['pct_positive']:.1f}%)",
                f"negativ: {stats['n_negative']} ({stats['pct_negative']:.1f}%)",
            ]
        self.stat_info_var.set("\n".join(lines))

    def _on_stat_hist_click(self, event):
        if event.inaxes != self.stat_ax or event.xdata is None:
            return
        self.stat_threshold_var.set(f"{event.xdata:.4g}")
        self._redraw_stats()

    def _compute_cohort_stats(self):
        source = self._require_named_population(self.stat_gate_source_var)
        if source is None:
            return
        threshold = self._stat_threshold()
        if self.stat_threshold_var.get().strip() and threshold is None:
            messagebox.showwarning("Ungültige Schwelle", "Schwelle muss eine Zahl sein.")
            return
        mode = self.stat_mode_var.get()
        ch_a = self.stat_channel_a_var.get()
        ch_b = self.stat_channel_b_var.get() if mode == "ratio" else None
        try:
            min_signal = float(self.stat_min_signal_var.get())
        except ValueError:
            min_signal = 1.0

        rows = []
        for path in self.fcs_paths:
            fcs = self._get_data(path)
            df = fcs.df
            mask, applied = self._named_population_mask(df, fcs.filename, source)
            gated_df = df[mask]
            if mode == "single":
                if ch_a not in gated_df.columns:
                    continue
                values, label = gated_df[ch_a], ch_a
            else:
                if ch_a not in gated_df.columns or ch_b not in gated_df.columns:
                    continue
                values = compute_ratio(gated_df, ch_a, ch_b, min_signal=min_signal)
                label = f"{ch_a}/{ch_b}"
            stats = value_stats(values, threshold, n_total=len(df), n_gated=len(gated_df))
            row = {"filename": fcs.filename, "gates_applied": ";".join(applied) or "none", "channel": label}
            row.update(stats)
            rows.append(row)

        self.cohort_df = pd.DataFrame(rows)
        self._refresh_cohort_tree()
        self.status_var.set(f"Kohorten-Statistik für {len(rows)} Dateien berechnet.")

    def _refresh_cohort_tree(self):
        self.cohort_tree.delete(*self.cohort_tree.get_children())
        if self.cohort_df is None:
            return
        for _, row in self.cohort_df.iterrows():
            values = []
            for c in self.cohort_tree["columns"]:
                v = row.get(c, "")
                if isinstance(v, float):
                    v = "" if np.isnan(v) else f"{v:.3g}"
                values.append(v)
            self.cohort_tree.insert("", "end", values=values)

    def _export_cohort_csv(self):
        if self.cohort_df is None or len(self.cohort_df) == 0:
            messagebox.showwarning("Keine Daten", "Bitte zuerst 'Kohorte berechnen' klicken.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="cohort_stats.csv",
            initialdir=self._ensure_exports_dir(),
        )
        if not path:
            return
        self.cohort_df.to_csv(path, index=False)
        messagebox.showinfo("Exportiert", f"Kohorten-Statistik gespeichert unter:\n{path}")

    # ---------------------------------------------------------- experiment folder
    def _choose_experiment_folder(self):
        chosen = filedialog.askdirectory(
            title="Experiment-Ordner wählen (enthält die .fcs-Dateien, auch in Unterordnern)",
            initialdir=self.input_dir if os.path.isdir(self.input_dir) else os.getcwd(),
        )
        if not chosen:
            return
        self._load_experiment(chosen)

    def _rescan_files(self):
        """Re-scan the current experiment folder for .fcs files — unlike
        _load_experiment, this does NOT touch the gate tree/strategy or
        cached data for files still present, so it's safe to use mid-session
        after copying new files in (nothing here watches the folder live, so
        new files never appear on their own)."""
        if not self.input_dir or not os.path.isdir(self.input_dir):
            messagebox.showwarning("Kein Ordner geladen", "Bitte zuerst einen Experiment-Ordner wählen.")
            return
        try:
            fcs_paths = find_fcs_files(self.input_dir, recursive=True)
        except FileNotFoundError as exc:
            messagebox.showwarning("Keine Dateien gefunden", str(exc))
            return

        added = sorted(set(fcs_paths) - set(self.fcs_paths))
        removed = sorted(set(self.fcs_paths) - set(fcs_paths))
        for p in removed:
            self.data_cache.pop(p, None)

        previous_selection = self.current_path
        self.fcs_paths = fcs_paths
        self.file_listbox.delete(0, "end")
        for p in self.fcs_paths:
            self.file_listbox.insert("end", os.path.relpath(p, self.input_dir))

        if previous_selection in self.fcs_paths:
            idx = self.fcs_paths.index(previous_selection)
            self.file_listbox.selection_clear(0, "end")
            self.file_listbox.selection_set(idx)
            self.file_listbox.see(idx)
            self.current_path = previous_selection
        elif self.fcs_paths:
            self._select_file_index(0)
        self._refresh_overlay_file_list()

        self.status_var.set(
            f"Dateiliste aktualisiert: {len(added)} neu, {len(removed)} entfernt, {len(self.fcs_paths)} gesamt."
        )

    def _load_experiment(self, root_dir: str):
        """Point the whole app at a new experiment folder: .fcs files are
        picked up recursively from it, gate-sets are read from/saved to its
        'gates' subfolder, and exports default into its 'exports' subfolder —
        so one folder holds everything for one experiment."""
        try:
            fcs_paths = find_fcs_files(root_dir, recursive=True)
        except FileNotFoundError as exc:
            messagebox.showerror("Keine FCS-Dateien gefunden", str(exc))
            return

        self._cancel_drawing()
        self.input_dir = root_dir
        self.gates_dir = os.path.join(root_dir, "gates")
        self.exports_dir = os.path.join(root_dir, "exports")
        self.strategies_dir = os.path.join(root_dir, "strategies")
        self.fcs_paths = fcs_paths
        self.data_cache.clear()
        self.existing_gate_sets = load_all_gate_sets(self.gates_dir)
        self.gate_tree = new_gate_tree()
        self.current_node_id = "root"
        self._node_counter = 0
        self.current_strategy_name = None
        self.current_strategy_path = None
        self._strategy_dirty = False
        self.cohort_df = None

        self.experiment_dir_var.set(self.input_dir)
        self.file_listbox.delete(0, "end")
        for p in self.fcs_paths:
            self.file_listbox.insert("end", os.path.relpath(p, self.input_dir))
        self._refresh_existing_listbox()
        self._refresh_strategy_combo()
        self.ov_colors.clear()
        self._refresh_overlay_file_list(select_paths=set())
        self._select_file_index(0)
        self.status_var.set(
            f"Experiment geladen: {len(self.fcs_paths)} FCS-Datei(en) aus '{root_dir}'. "
            f"Gates: '{self.gates_dir}', Exporte: '{self.exports_dir}', Strategien: '{self.strategies_dir}'."
        )

    # ---------------------------------------------------------- data access
    def _get_data(self, path: str):
        if path not in self.data_cache:
            self.data_cache[path] = load_fcs(path)
        return self.data_cache[path]

    def _select_file_index(self, idx: int):
        idx = idx % len(self.fcs_paths)  # wrap around so you can cycle through all files either direction
        self.file_listbox.selection_clear(0, "end")
        self.file_listbox.selection_set(idx)
        self.file_listbox.see(idx)
        self.current_path = self.fcs_paths[idx]
        fcs = self._get_data(self.current_path)
        channels = list(fcs.df.columns)
        self.x_channel_combo["values"] = channels
        self.y_channel_combo["values"] = channels
        if self.x_channel_var.get() not in channels:
            self.x_channel_var.set(channels[0])
        if self.y_channel_var.get() not in channels:
            self.y_channel_var.set(channels[1] if len(channels) > 1 else channels[0])
        self._update_scope_hint()
        self._redraw()

        self.kin_channel_a_combo["values"] = channels
        self.kin_channel_b_combo["values"] = channels
        if self.kin_channel_a_var.get() not in channels:
            self.kin_channel_a_var.set(channels[0])
        if self.kin_channel_b_var.get() not in channels:
            self.kin_channel_b_var.set(channels[1] if len(channels) > 1 else channels[0])

        self.stat_channel_a_combo["values"] = channels
        self.stat_channel_b_combo["values"] = channels
        if self.stat_channel_a_var.get() not in channels:
            self.stat_channel_a_var.set(channels[0])
        if self.stat_channel_b_var.get() not in channels:
            self.stat_channel_b_var.set(channels[1] if len(channels) > 1 else channels[0])

        self.ov_channel_a_combo["values"] = channels
        self.ov_channel_b_combo["values"] = channels
        if self.ov_channel_a_var.get() not in channels:
            self.ov_channel_a_var.set(channels[0])
        if self.ov_channel_b_var.get() not in channels:
            self.ov_channel_b_var.set(channels[1] if len(channels) > 1 else channels[0])

        tab_idx = self.notebook.index(self.notebook.select())
        if tab_idx == 1:
            self._redraw_kinetics()
        elif tab_idx == 2:
            self._redraw_stats()
        elif tab_idx == 3:
            self._redraw_overlay()

    def _on_file_selected(self, _event):
        sel = self.file_listbox.curselection()
        if sel:
            self._cancel_drawing()
            self._select_file_index(sel[0])

    def _prev_file(self):
        sel = self.file_listbox.curselection()
        idx = sel[0] if sel else 0
        self._cancel_drawing()
        self._select_file_index(idx - 1)

    def _next_file(self):
        sel = self.file_listbox.curselection()
        idx = sel[0] if sel else 0
        self._cancel_drawing()
        self._select_file_index(idx + 1)

    def _refresh_existing_listbox(self):
        self.existing_listbox.delete(0, "end")
        for gs in self.existing_gate_sets:
            self.existing_listbox.insert("end", f"{gs.name}  [{gs.scope.get('type')}]")
        if hasattr(self, "kin_gate_source_combo"):
            self._refresh_gate_source_combos()

    def _refresh_gate_source_combos(self):
        """Keep the Kinetik/Statistik 'Population:' dropdowns in sync with the
        saved gate-sets, so every distinct saved name is pickable explicitly."""
        values = [GATE_SOURCE_LIVE, GATE_SOURCE_NONE] + list_gate_set_names(self.existing_gate_sets)
        for combo, var in (
            (self.kin_gate_source_combo, self.kin_gate_source_var),
            (self.stat_gate_source_combo, self.stat_gate_source_var),
            (self.ov_gate_source_combo, self.ov_gate_source_var),
        ):
            combo["values"] = values
            if var.get() not in values:
                var.set(values[0])

    # ---------------------------------------------------------- plotting
    def _base_mask(self, df) -> np.ndarray:
        """Mask from already-saved gate-sets applicable to the current file —
        used ONLY as a starting baseline before you've drawn anything in this
        session (the live tree is still just the root, no children yet), e.g.
        to preview a previously committed debris/singlet gate so you can draw
        today's gates inside it without redrawing it.

        Once you've started building a live tree, this stops contributing.
        Every gate-set you save is always an export of root->current-node
        FROM THAT SAME TREE, so once saved it immediately reappears in
        self.existing_gate_sets — if it kept being ANDed in here, every node
        in the tree (including ancestors/siblings that gate was never meant
        to restrict) would get that deep restriction baked back in, making
        'go to parent'/'show sibling' look stuck showing the child's
        population instead of their own."""
        if not self.current_path:
            return np.ones(len(df), dtype=bool)
        return self._base_mask_for(df, os.path.basename(self.current_path))

    def _base_mask_for(self, df, filename: str) -> np.ndarray:
        """_base_mask for an arbitrary file (used by the overlay tab, which
        applies the live tree to several files at once)."""
        if not self.show_existing_var.get() or self.gate_tree["root"].children:
            return np.ones(len(df), dtype=bool)
        applicable = resolve_gate_sets_for_file(filename, self.existing_gate_sets)
        mask = np.ones(len(df), dtype=bool)
        for gs in applicable:
            mask &= gs.mask(df)
        return mask

    def _cumulative_pending_mask(self, df) -> np.ndarray:
        """Mask for the currently viewed node: saved gate-sets (preview) ANDed
        with every step from the root down to the current node in the tree."""
        return node_mask(df, self.gate_tree, self.current_node_id, base_mask=self._base_mask(df))

    @staticmethod
    def _resolve_axis_limits(values: np.ndarray, lo_str: str, hi_str: str) -> tuple[float | None, float | None, bool]:
        """Parses the Min/Max entry fields for one axis. Returns (lo, hi,
        manual). manual is False when both fields are empty — meaning
        matplotlib should autoscale that axis as usual. As soon as either
        bound is set, manual is True and the missing bound is backfilled from
        the data's own extent, so outliers on the OTHER side still get
        clipped/flagged even if only one side was constrained."""
        lo_str, hi_str = lo_str.strip(), hi_str.strip()
        try:
            lo = float(lo_str) if lo_str else None
        except ValueError:
            lo = None
        try:
            hi = float(hi_str) if hi_str else None
        except ValueError:
            hi = None
        if lo is None and hi is None:
            return None, None, False
        finite = values[np.isfinite(values)] if len(values) else values
        data_lo = float(finite.min()) if len(finite) else 0.0
        data_hi = float(finite.max()) if len(finite) else 1.0
        if lo is None:
            lo = data_lo
        if hi is None:
            hi = data_hi
        if lo >= hi:
            hi = lo + 1.0
        return lo, hi, True

    def _autofit_axis_limits(self):
        """Fill the Min/Max fields from the 1st/99th percentile of the
        currently displayed population — a one-click fix for the case that
        prompted this feature: a couple of far-out events (e.g. -10^6) that
        blow the axis out so badly the real population is squeezed flat."""
        if not self.current_path:
            return
        fcs = self._get_data(self.current_path)
        df = fcs.df
        x_ch, y_ch = self.x_channel_var.get(), self.y_channel_var.get()
        if x_ch not in df.columns or y_ch not in df.columns:
            return
        view_mask = self._cumulative_pending_mask(df)
        x_vals = df[x_ch].to_numpy()[view_mask]
        y_vals = df[y_ch].to_numpy()[view_mask]
        if len(x_vals) == 0:
            return
        x_lo, x_hi = np.percentile(x_vals, [1, 99])
        y_lo, y_hi = np.percentile(y_vals, [1, 99])
        self.x_min_var.set(f"{x_lo:.4g}")
        self.x_max_var.set(f"{x_hi:.4g}")
        self.y_min_var.set(f"{y_lo:.4g}")
        self.y_max_var.set(f"{y_hi:.4g}")

    def _reset_axis_limits(self):
        for v in (self.x_min_var, self.x_max_var, self.y_min_var, self.y_max_var):
            v.set("")

    def _redraw(self):
        if not self.current_path:
            return
        fcs = self._get_data(self.current_path)
        df = fcs.df
        x_ch, y_ch = self.x_channel_var.get(), self.y_channel_var.get()
        if x_ch not in df.columns or y_ch not in df.columns:
            return

        # Drill-down view: only events that passed every already-committed
        # step (saved gate-sets if enabled, plus every pending step so far)
        # are shown — cells excluded by an earlier step disappear from later
        # steps' plots, same as navigating down into a FlowJo gate hierarchy.
        view_mask = self._cumulative_pending_mask(df)
        plot_type = self.plot_type_var.get()

        x_raw = df[x_ch].to_numpy(dtype=float)
        y_raw = df[y_ch].to_numpy(dtype=float)

        # Manual axis limits: clip out-of-range events to the border instead
        # of letting a couple of far-out events (e.g. -10^6) blow the axis
        # out and squeeze the real population flat. Clipped events are
        # tracked (out_of_range) and re-drawn as a red '×' at the border so
        # their presence stays obvious instead of silently disappearing.
        x_lo, x_hi, x_manual = self._resolve_axis_limits(x_raw[view_mask], self.x_min_var.get(), self.x_max_var.get())
        y_lo, y_hi, y_manual = self._resolve_axis_limits(y_raw[view_mask], self.y_min_var.get(), self.y_max_var.get())
        x_plot = np.clip(x_raw, x_lo, x_hi) if x_manual else x_raw
        y_plot = np.clip(y_raw, y_lo, y_hi) if y_manual else y_raw
        x_out = ((x_raw < x_lo) | (x_raw > x_hi)) if x_manual else np.zeros(len(df), dtype=bool)
        y_out = ((y_raw < y_lo) | (y_raw > y_hi)) if y_manual else np.zeros(len(df), dtype=bool)
        out_of_range = (x_out | y_out) & view_mask

        value_range = [[x_lo, x_hi] if x_manual else None, [y_lo, y_hi] if y_manual else None]
        is_hist = plot_type == HIST_1D

        self.ax.clear()
        self.ax.set_facecolor("white")
        if is_hist:
            n_out = self._plot_histogram_1d(
                df, view_mask, x_raw, x_ch, x_lo if x_manual else None, x_hi if x_manual else None
            )
        else:
            xv, yv = x_plot[view_mask], y_plot[view_mask]
            if plot_type == "Scatter":
                self.ax.scatter(xv, yv, s=1, color="#1f4e8c", alpha=0.5, linewidths=0, rasterized=True)
            elif plot_type == "Pseudocolor":
                self._plot_pseudocolor(self.ax, xv, yv, x_ch, y_ch, value_range, sigma=0.0)
            elif plot_type == "Density":
                self._plot_pseudocolor(self.ax, xv, yv, x_ch, y_ch, value_range, sigma=2.0)
            elif plot_type == "Contour":
                self._plot_contour(self.ax, xv, yv, x_ch, y_ch, value_range)
            self._overlay_child_shapes(self.ax, x_ch, y_ch)
            n_out = int(out_of_range.sum())

        self._apply_axis_transforms(x_ch, y_ch, y_linear=is_hist)
        if x_manual:
            self.ax.set_xlim(x_lo, x_hi)
        if y_manual and not is_hist:
            self.ax.set_ylim(y_lo, y_hi)

        if not is_hist:
            self._overlay_quadrant_preview(self.ax, x_ch, y_ch)
            if n_out:
                self.ax.scatter(
                    x_plot[out_of_range], y_plot[out_of_range], marker="x", s=45, color="red",
                    linewidths=1.8, zorder=6,
                )

        self.ax.set_xlabel(x_ch)
        self.ax.set_ylabel("Events / Bin" if is_hist else y_ch)
        node_name = self.gate_tree[self.current_node_id].name
        title_pop = f"{node_name}: {int(view_mask.sum())}/{len(df)} Events"
        if n_out:
            title_pop += f"  |  {n_out} außerhalb der Achsen-Grenzen" + ("" if is_hist else " (rot ×)")
        self.ax.set_title(f"{os.path.basename(self.current_path)}\n{title_pop}")
        self.canvas.draw_idle()
        self._update_breadcrumb()
        self._refresh_gate_tree_view(df)

    # ---- 2D density plots -------------------------------------------------
    # All 2D density views bin in DISPLAY space (after the Log/Logicle axis
    # transform), so each bin is the same size on screen — binning raw values
    # on a log axis gives giant smeared cells left and invisible ones right.
    # Colors use a LOG scale: with a linear scale the debris/noise peak takes
    # the whole top of the colormap and every real population drowns in the
    # darkest color. Empty bins stay white so population borders are visible.
    def _colormap(self):
        name = COLORMAPS.get(self.colormap_var.get(), "turbo")
        base = matplotlib.colormaps[name]
        # Skip the near-black/near-white end so the LOWEST density is still a
        # clearly visible color (not mistaken for "dense" or for background).
        start = {"turbo": 0.12, "Blues": 0.25, "Greys": 0.25}.get(name, 0.0)
        cmap = ListedColormap(base(np.linspace(start, 1.0, 256)), name=f"{name}_trunc")
        cmap.set_bad("white")
        cmap.set_under("white")
        return cmap

    def _density_grid(self, x, y, x_ch, y_ch, value_range, bins=200, sigma=0.0):
        tx = self._display_transform(self.x_transform_var.get(), x_ch)
        ty = self._display_transform(self.y_transform_var.get(), y_ch)
        xt, yt = tx.forward(x), ty.forward(y)
        ok = np.isfinite(xt) & np.isfinite(yt)
        xt, yt = xt[ok], yt[ok]

        def rng(vals, tr, manual):
            if manual is not None:
                lo, hi = tr.forward(manual)
            elif len(vals):
                lo, hi = float(vals.min()), float(vals.max())
            else:
                lo, hi = 0.0, 1.0
            return (lo, hi) if hi > lo else (lo, lo + 1.0)

        H, xe_t, ye_t = np.histogram2d(
            xt, yt, bins=bins, range=[rng(xt, tx, value_range[0]), rng(yt, ty, value_range[1])]
        )
        if sigma > 0:
            H = gaussian_filter(H, sigma=sigma)
        return {
            "H": H, "xe": tx.inverse(xe_t), "ye": ty.inverse(ye_t),
            "xc": tx.inverse((xe_t[:-1] + xe_t[1:]) / 2), "yc": ty.inverse((ye_t[:-1] + ye_t[1:]) / 2),
            "xe_t": xe_t, "ye_t": ye_t, "xt": xt, "yt": yt, "ok": ok,
        }

    def _plot_pseudocolor(self, ax, x, y, x_ch, y_ch, value_range, sigma=0.0):
        g = self._density_grid(x, y, x_ch, y_ch, value_range, bins=200, sigma=sigma)
        H = g["H"]
        if not (H > 0).any():
            return
        # Smoothed: one isolated event peaks at ~1/(2πσ²) (≈0.04 for σ=2);
        # a floor above that keeps lone outliers from becoming big blobs.
        floor = 1.0 if sigma == 0 else max(2.5 / (2 * np.pi * sigma**2), float(H.max()) * 1e-4)
        masked = np.ma.masked_less(H, floor)
        ax.pcolormesh(
            g["xe"], g["ye"], masked.T, cmap=self._colormap(),
            norm=LogNorm(vmin=floor, vmax=max(float(H.max()), floor * 1.01)), shading="flat", rasterized=True,
        )

    CONTOUR_PROBABILITIES = (0.98, 0.95, 0.90, 0.75, 0.50, 0.25, 0.10)

    def _plot_contour(self, ax, x, y, x_ch, y_ch, value_range):
        """Equal-probability contours (like FlowJo): each line encloses the
        given share of the events, e.g. the '50%' line surrounds the densest
        half. Events outside the outermost line are drawn as grey dots so
        sparse populations don't silently vanish."""
        g = self._density_grid(x, y, x_ch, y_ch, value_range, bins=200, sigma=2.0)
        H = g["H"]
        total = float(H.sum())
        if total <= 0:
            return
        flat = np.sort(H.ravel())[::-1]
        cum = np.cumsum(flat) / total
        level_to_pct = {}
        for p in self.CONTOUR_PROBABILITIES:
            thr = float(flat[min(np.searchsorted(cum, p), len(flat) - 1)])
            if thr > 0 and thr not in level_to_pct:
                level_to_pct[thr] = p
        levels = sorted(level_to_pct)
        if not levels:
            return

        ix = np.clip(np.digitize(g["xt"], g["xe_t"]) - 1, 0, H.shape[0] - 1)
        iy = np.clip(np.digitize(g["yt"], g["ye_t"]) - 1, 0, H.shape[1] - 1)
        outlier = H[ix, iy] < levels[0]
        xs, ys = np.asarray(x)[g["ok"]], np.asarray(y)[g["ok"]]
        ax.scatter(xs[outlier], ys[outlier], s=1.5, color="#8a8a8a", alpha=0.6, linewidths=0, rasterized=True)

        if len(levels) == 1:
            cs = ax.contour(g["xc"], g["yc"], H.T, levels=levels, colors=["#1f4e8c"], linewidths=1.3)
        else:
            cs = ax.contour(  # floor avoids LogNorm's "z <= 0 masked" warning; below levels[0] anyway
                g["xc"], g["yc"], np.maximum(H, levels[0] * 1e-3).T, levels=levels, cmap=self._colormap(),
                norm=LogNorm(vmin=levels[0], vmax=levels[-1]), linewidths=1.3,
            )
        ax.clabel(cs, fmt={lv: f"{level_to_pct[lv] * 100:.0f}%" for lv in levels}, fontsize=7, inline=True)

    def _plot_histogram_1d(self, df, view_mask, x_raw, x_ch, lo, hi) -> int:
        """Intensity (X channel) vs. number of events, as lines: the current
        population filled in grey, every child gate's sub-population as a
        line in that gate's color (same color as its outline in 2D plots).
        Returns the number of events outside the manual X limits."""
        node = self.gate_tree[self.current_node_id]
        series = [OverlaySeries(node.name, x_raw[view_mask & np.isfinite(x_raw)], "#555555")]
        n_parent = max(int(view_mask.sum()), 1)
        for i, child_id in enumerate(node.children):
            child = self.gate_tree[child_id]
            m = view_mask & child.step.mask(df) & np.isfinite(x_raw)
            series.append(OverlaySeries(
                f"{child.name}  ({int(m.sum())}, {100.0 * m.sum() / n_parent:.1f}%)", x_raw[m], default_color(i)
            ))
        tr = self._display_transform(self.x_transform_var.get(), x_ch)
        result = compute_overlay(series, tr, n_bins=256, lo=lo, hi=hi, normalization=NORM_COUNT, smooth_sigma=1.0)
        x = result.centers_raw
        parent_y = result.curves[0]
        self.ax.fill_between(x, 0, parent_y, color="#bbbbbb", alpha=0.5, linewidth=0)
        self.ax.plot(x, parent_y, color="#333333", linewidth=1.5, label=f"{node.name}  ({int(view_mask.sum())})")
        for s, y in zip(series[1:], result.curves[1:]):
            self.ax.plot(x, y, color=s.color, linewidth=1.6, label=s.label)
        y_max = max((float(c.max()) for c in result.curves if len(c)), default=1.0)
        self.ax.set_ylim(0, (y_max or 1.0) * 1.05)
        if len(x):
            self.ax.set_xlim(lo if lo is not None else x[0], hi if hi is not None else x[-1])
        self.ax.legend(fontsize=8, loc="upper right", framealpha=0.85)
        return result.n_out_of_range[0]

    def _channel_range(self, channel: str) -> float:
        fcs = self._get_data(self.current_path)
        r = (fcs.channel_ranges or {}).get(channel)
        if r:
            return r
        col = fcs.df[channel]
        return float(col.max()) if len(col) else 262144.0

    def _scale_params(self) -> dict:
        try:
            linthresh = float(self.log_linthresh_var.get())
        except ValueError:
            linthresh = 10.0
        try:
            logicle_w = float(self.logicle_w_var.get())
        except ValueError:
            logicle_w = 0.5
        try:
            logicle_m = float(self.logicle_m_var.get())
        except ValueError:
            logicle_m = 4.5
        return {"log_linthresh": linthresh, "logicle_w": logicle_w, "logicle_m": logicle_m}

    def _display_transform(self, transform_name: str, channel: str) -> DisplayTransform:
        return DisplayTransform(transform_name, channel_range=self._channel_range(channel), **self._scale_params())

    def _apply_axis_transforms(self, x_ch: str, y_ch: str, y_linear: bool = False):
        params = self._scale_params()
        apply_axis_scale(self.ax, "x", self.x_transform_var.get(), channel_range=self._channel_range(x_ch), **params)
        apply_axis_scale(
            self.ax, "y", "Linear" if y_linear else self.y_transform_var.get(),
            channel_range=self._channel_range(y_ch), **params,
        )

    @staticmethod
    def _shape_patch(step: GateStep, **kwargs):
        if step.shape in ("polygon", "rectangle"):
            return PolygonPatch(step.vertices, closed=True, fill=False, **kwargs)
        if step.shape == "ellipse":
            p = step.params
            return EllipsePatch((p["cx"], p["cy"]), width=2 * p["rx"], height=2 * p["ry"], fill=False, **kwargs)
        return None

    @staticmethod
    def _draw_quadrant_lines(ax, cx: float, cy: float, angle_deg: float, **kwargs):
        """Draw the two perpendicular dividing lines of a quadrant gate,
        crossing at (cx, cy) and rotated by angle_deg. Drawn far beyond the
        current view and clipped to it (matplotlib's default clip-on-axes
        behavior for line artists) so the lines always look like they span
        the whole plot; scalex/scaley=False keeps them from affecting
        autoscale/the current view themselves."""
        angle = math.radians(angle_deg)
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        span = math.hypot(xlim[1] - xlim[0], ylim[1] - ylim[0]) * 2 + 1e-9
        kwargs.setdefault("linewidth", 1.5)
        for dx, dy in ((math.sin(angle), -math.cos(angle)), (math.cos(angle), math.sin(angle))):
            ax.plot(
                [cx - dx * span, cx + dx * span], [cy - dy * span, cy + dy * span],
                scalex=False, scaley=False, **kwargs,
            )

    @staticmethod
    def _quadrant_label_position(ax, params: dict) -> tuple[float, float]:
        """Approximate label position inside the given quadrant: offset from
        the center towards that quadrant, normalized by the current axis
        span per-axis so it lands sensibly even when X and Y have very
        different raw ranges (this is only for label placement, not gate
        math — the gate boundary itself always rotates in raw data units)."""
        cx, cy = params["cx"], params["cy"]
        angle = math.radians(params.get("angle_deg", 0.0))
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        x_span, y_span = (xlim[1] - xlim[0]) or 1.0, (ylim[1] - ylim[0]) or 1.0
        x_sign = 1.0 if params["x_sign"] == "+" else -1.0
        y_sign = 1.0 if params["y_sign"] == "+" else -1.0
        frac = 0.22
        off_x = x_sign * frac * math.cos(angle) - y_sign * frac * math.sin(angle)
        off_y = x_sign * frac * math.sin(angle) + y_sign * frac * math.cos(angle)
        return cx + off_x * x_span, cy + off_y * y_span

    def _overlay_child_shapes(self, ax, x_ch: str, y_ch: str):
        """Draw outlines of the current node's child gates that use this exact
        channel pair, as a visual reference on top of any plot type — also
        what you double-click on to navigate into that child population."""
        # Each child gate gets its own color (same index -> same color as its
        # line in the 1D histogram), with a white halo so outline and label
        # stay readable on top of any colormap.
        halo_line = [patheffects.withStroke(linewidth=3.5, foreground="white")]
        halo_text = [patheffects.withStroke(linewidth=2.5, foreground="white")]
        drawn_quadrant_centers = set()
        for i, child_id in enumerate(self.gate_tree[self.current_node_id].children):
            step = self.gate_tree[child_id].step
            if step.x_channel != x_ch or step.y_channel != y_ch:
                continue
            color = default_color(i)
            label_kw = dict(ha="center", fontsize=8, fontweight="bold", path_effects=halo_text, zorder=8)
            if step.shape == "quadrant":
                p = step.params
                key = (p["cx"], p["cy"], p.get("angle_deg", 0.0))
                if key not in drawn_quadrant_centers:
                    drawn_quadrant_centers.add(key)
                    self._draw_quadrant_lines(
                        ax, p["cx"], p["cy"], p.get("angle_deg", 0.0), color="black", path_effects=halo_line
                    )
                cx, cy = self._quadrant_label_position(ax, p)
                ax.annotate(self.gate_tree[child_id].name, (cx, cy), color=color, **label_kw)
                continue
            patch = self._shape_patch(step, edgecolor=color, linewidth=1.8, path_effects=halo_line, zorder=7)
            if patch is None:
                continue
            ax.add_patch(patch)
            if step.shape == "ellipse":
                cx, cy = step.params["cx"], step.params["cy"]
            else:
                cx, cy = patch.get_path().vertices.mean(axis=0)
            ax.annotate(self.gate_tree[child_id].name, (cx, cy), color=color, **label_kw)

    def _overlay_quadrant_preview(self, ax, x_ch: str, y_ch: str):
        """While a quadrant gate is being placed: once its center is picked,
        keep showing the (dashed, orange) preview crosshair across redraws
        until 'Schritt übernehmen'/'Abbrechen' is clicked."""
        coords = self.pending_shape_coords
        if not coords or coords.get("shape") not in QUADRANT_SHAPES:
            return
        center = coords.get("center")
        if center is None or coords["x_channel"] != x_ch or coords["y_channel"] != y_ch:
            return
        angle_deg = self._quadrant_angle_value() if coords["shape"] == "Quadrant (Winkel)" else 0.0
        self._draw_quadrant_lines(ax, center[0], center[1], angle_deg, color="orange", linestyle="--", linewidth=1.5)

    def _update_breadcrumb(self):
        path = node_path(self.gate_tree, self.current_node_id)
        self.breadcrumb_var.set(" › ".join(self.gate_tree[nid].name for nid in path))

    def _gate_tree_counts(self, df) -> dict[str, tuple[int, float]]:
        """DFS over the WHOLE tree (not just the current node's lineage),
        returning {node_id: (n_events, pct_of_parent)} so the tree panel can
        show every gate's size at a glance without navigating to it.

        Deliberately starts from the RAW, unfiltered event count — NOT
        _base_mask()/the saved-gate-sets preview. Those saved gate-sets often
        duplicate steps that already exist as nodes in this very tree (e.g.
        right after you save one, or when you've redrawn a previously-saved
        gate to keep extending it); folding them in here would make an
        already-applied step's own gate look like a 100%-passthrough at every
        level below it, since it becomes a no-op AND against a population
        that's secretly already been restricted to the same boundary. The
        live tree's own steps are the only thing this panel needs — what
        _base_mask() additionally restricts is a separate, PLOT-only preview
        (see _redraw/_cumulative_pending_mask)."""
        counts: dict[str, tuple[int, float]] = {}

        def walk(node_id: str, parent_mask: np.ndarray, parent_total: int):
            node = self.gate_tree[node_id]
            mask = parent_mask if node.step is None else (parent_mask & node.step.mask(df))
            n = int(mask.sum())
            pct = (100.0 * n / parent_total) if parent_total else 0.0
            counts[node_id] = (n, pct)
            for child_id in node.children:
                walk(child_id, mask, n)

        walk("root", np.ones(len(df), dtype=bool), len(df))
        return counts

    def _refresh_gate_tree_view(self, df):
        """Rebuild the hierarchy tree panel: every gate, indented under its
        parent (ttk.Treeview nests children visually), with the currently
        viewed node highlighted — so the whole gating scheme is visible at
        once instead of only the current level's siblings."""
        tv = self.tree_view

        open_state: dict[str, bool] = {}

        def collect_open(node_id: str):
            if tv.exists(node_id):
                open_state[node_id] = bool(tv.item(node_id, "open"))
            for child_id in self.gate_tree[node_id].children:
                collect_open(child_id)

        collect_open("root")
        tv.delete(*tv.get_children(""))

        counts = self._gate_tree_counts(df)
        path_to_current = set(node_path(self.gate_tree, self.current_node_id))

        def insert_node(parent_iid: str, node_id: str):
            node = self.gate_tree[node_id]
            n, pct = counts.get(node_id, (0, 0.0))
            label = str(n) if node_id == "root" else f"{n} ({pct:.1f}%)"
            is_open = open_state.get(node_id, node_id in path_to_current)
            tags = ("current",) if node_id == self.current_node_id else ()
            tv.insert(parent_iid, "end", iid=node_id, text=node.name, values=(label,), open=is_open, tags=tags)
            for child_id in node.children:
                insert_node(node_id, child_id)

        insert_node("", "root")
        tv.selection_set(self.current_node_id)
        tv.see(self.current_node_id)

    def _on_tree_double_click(self, event):
        row_id = self.tree_view.identify_row(event.y)
        if row_id:
            self._navigate_to(row_id)

    def _tree_selected_node_id(self) -> str | None:
        sel = self.tree_view.selection()
        return sel[0] if sel else None

    def _navigate_selected_tree_node(self):
        node_id = self._tree_selected_node_id()
        if node_id is None:
            messagebox.showinfo("Kein Knoten ausgewählt", "Bitte zuerst einen Knoten im Gate-Baum auswählen.")
            return
        self._navigate_to(node_id)

    def _rename_tree_selected(self):
        node_id = self._tree_selected_node_id()
        if node_id is None:
            messagebox.showinfo("Kein Knoten ausgewählt", "Bitte zuerst einen Knoten im Gate-Baum auswählen.")
            return
        if node_id == "root":
            messagebox.showinfo("Wurzel", "Die Wurzel-Ebene (alle Events) kann nicht umbenannt werden.")
            return
        self._rename_node(node_id)

    def _edit_tree_selected(self):
        node_id = self._tree_selected_node_id()
        if node_id is None:
            messagebox.showinfo("Kein Knoten ausgewählt", "Bitte zuerst einen Knoten im Gate-Baum auswählen.")
            return
        if node_id == "root":
            messagebox.showinfo("Wurzel", "Die Wurzel-Ebene hat kein Gate zum Bearbeiten.")
            return
        parent_id = self.gate_tree[node_id].parent_id
        if self.current_node_id != parent_id:
            self._navigate_to(parent_id)
        self._start_editing_node(node_id)

    def _delete_tree_selected(self):
        node_id = self._tree_selected_node_id()
        if node_id is None:
            messagebox.showinfo("Kein Knoten ausgewählt", "Bitte zuerst einen Knoten im Gate-Baum auswählen.")
            return
        if node_id == "root":
            messagebox.showinfo("Wurzel", "Die Wurzel-Ebene (alle Events) kann nicht gelöscht werden.")
            return
        node = self.gate_tree[node_id]
        if node.children and not messagebox.askyesno(
            "Unter-Gates löschen?", f"'{node.name}' hat {len(node.children)} Unter-Gate(s). Wirklich mit löschen?"
        ):
            return
        need_navigate_up = node_id in node_path(self.gate_tree, self.current_node_id)
        parent_id = node.parent_id
        self._cancel_drawing()
        remove_subtree(self.gate_tree, node_id)
        self._mark_strategy_dirty()
        if need_navigate_up:
            self._navigate_to(parent_id)
        else:
            self._redraw()

    # ---------------------------------------------------------- tree navigation
    def _navigate_to(self, node_id: str):
        self._cancel_drawing()
        self.current_node_id = node_id
        self._redraw()

    def _on_canvas_click(self, event):
        if self._picking_point_center:
            self._on_quadrant_center_click(event)
            return
        if self.active_selector is not None:
            return

        # Right-click on the X/Y axis label: show a popup menu to switch channel.
        if event.button == 3 and event.guiEvent is not None:
            renderer = self.canvas.get_renderer()
            try:
                if self.ax.xaxis.label.get_window_extent(renderer).contains(event.x, event.y):
                    self._show_channel_menu(event.guiEvent, self.x_channel_var)
                    return
                if self.ax.yaxis.label.get_window_extent(renderer).contains(event.x, event.y):
                    self._show_channel_menu(event.guiEvent, self.y_channel_var)
                    return
            except Exception:
                pass
            return

        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        if self.plot_type_var.get() == HIST_1D:
            return  # Y is event count here, not a channel — gate hit-tests would be meaningless
        x_ch, y_ch = self.x_channel_var.get(), self.y_channel_var.get()

        # Double-click INSIDE a child gate's shape: navigate into it.
        if event.dblclick:
            for child_id in self.gate_tree[self.current_node_id].children:
                step = self.gate_tree[child_id].step
                if step.x_channel == x_ch and step.y_channel == y_ch and point_in_step(step, event.xdata, event.ydata):
                    self._navigate_to(child_id)
                    return
            return

        # Single click ON a child gate's outline (not its interior): reshape it.
        hit_id = self._find_child_on_line(x_ch, y_ch, event)
        if hit_id is not None:
            self._start_editing_node(hit_id)

    def _shape_boundary_points(self, step: GateStep) -> np.ndarray:
        if step.shape in ("polygon", "rectangle"):
            pts = np.array(step.vertices, dtype=float)
            return np.vstack([pts, pts[:1]])
        if step.shape == "ellipse":
            p = step.params
            t = np.linspace(0, 2 * np.pi, 128)
            return np.column_stack([p["cx"] + p["rx"] * np.cos(t), p["cy"] + p["ry"] * np.sin(t)])
        return np.empty((0, 2))

    @staticmethod
    def _point_to_polyline_distance_px(points_disp: np.ndarray, px: float, py: float) -> float:
        if len(points_disp) < 2:
            return float("inf")
        a, b = points_disp[:-1], points_disp[1:]
        ab = b - a
        ab_len2 = np.sum(ab**2, axis=1)
        ab_len2[ab_len2 == 0] = 1e-9
        p = np.array([px, py])
        t = np.clip(np.sum((p - a) * ab, axis=1) / ab_len2, 0, 1)
        proj = a + ab * t[:, None]
        return float(np.min(np.sqrt(np.sum((p - proj) ** 2, axis=1))))

    def _find_child_on_line(self, x_ch: str, y_ch: str, event, tolerance_px: float = 8.0) -> str | None:
        best_id, best_dist = None, tolerance_px
        for child_id in self.gate_tree[self.current_node_id].children:
            step = self.gate_tree[child_id].step
            if step.x_channel != x_ch or step.y_channel != y_ch:
                continue
            boundary = self._shape_boundary_points(step)
            if len(boundary) == 0:
                continue
            disp = self.ax.transData.transform(boundary)
            dist = self._point_to_polyline_distance_px(disp, event.x, event.y)
            if dist < best_dist:
                best_dist, best_id = dist, child_id
        return best_id

    def _show_channel_menu(self, gui_event, channel_var: tk.StringVar):
        fcs = self._get_data(self.current_path)
        menu = tk.Menu(self, tearoff=0)
        for ch in fcs.df.columns:
            menu.add_command(label=ch, command=lambda c=ch: self._set_channel_and_redraw(channel_var, c))
        try:
            menu.tk_popup(gui_event.x_root, gui_event.y_root)
        finally:
            menu.grab_release()

    def _set_channel_and_redraw(self, channel_var: tk.StringVar, channel: str):
        channel_var.set(channel)
        self._redraw()

    def _rename_node(self, node_id: str):
        node = self.gate_tree[node_id]
        new_name = simpledialog.askstring("Gate umbenennen", "Neuer Name:", initialvalue=node.name, parent=self)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showwarning("Name fehlt", "Der Name darf nicht leer sein.")
            return
        node.name = new_name
        self._mark_strategy_dirty()
        self._redraw()

    def _start_editing_node(self, node_id: str):
        """Reshape an existing gate in place: preload the selector with its
        current vertices/extents so only the shape changes — node id, name
        and children are left untouched, so they keep matching across files."""
        if self.active_selector is not None or node_id == "root":
            return
        node = self.gate_tree[node_id]
        step = node.step
        if step.shape == "quadrant":
            messagebox.showinfo(
                "Nicht unterstützt",
                "Quadranten-Gates können nicht per Ecken-Editor angepasst werden. Bitte löschen und neu zeichnen."
            )
            return
        shape_label = step.shape.capitalize()
        if shape_label not in SHAPES:
            return

        self.x_channel_var.set(step.x_channel)
        self.y_channel_var.set(step.y_channel)
        self.shape_var.set(shape_label)
        if self.plot_type_var.get() == HIST_1D:
            self.plot_type_var.set("Pseudocolor")
        self._redraw()  # current_node_id (the parent) is unchanged, just refresh for the channel pair

        self.editing_node_id = node_id
        self.pending_shape_coords = {"x_channel": step.x_channel, "y_channel": step.y_channel, "shape": shape_label}

        if shape_label == "Polygon":
            self.active_selector = PolygonSelector(self.ax, onselect=lambda verts: None, useblit=True)
            self.active_selector.verts = [list(v) for v in step.vertices]
        elif shape_label == "Rectangle":
            xs = [v[0] for v in step.vertices]
            ys = [v[1] for v in step.vertices]
            self.active_selector = RectangleSelector(self.ax, onselect=lambda e1, e2: None, useblit=True, interactive=True)
            self.active_selector.extents = (min(xs), max(xs), min(ys), max(ys))
        elif shape_label == "Ellipse":
            p = step.params
            self.active_selector = EllipseSelector(self.ax, onselect=lambda e1, e2: None, useblit=True, interactive=True)
            self.active_selector.extents = (p["cx"] - p["rx"], p["cx"] + p["rx"], p["cy"] - p["ry"], p["cy"] + p["ry"])

        self.x_channel_combo.configure(state="disabled")
        self.y_channel_combo.configure(state="disabled")
        self.shape_combo.configure(state="disabled")
        self.draw_button.configure(state="disabled")
        self.finish_button.configure(state="normal", text="Änderungen speichern")
        self.cancel_button.configure(state="normal")
        self.status_var.set(
            f"Gate '{node.name}' wird bearbeitet — Ecken/Ränder anpassen, dann 'Änderungen speichern' klicken. "
            "Name und Kind-Gates bleiben dabei erhalten."
        )

    def _go_to_parent(self):
        parent_id = self.gate_tree[self.current_node_id].parent_id
        if parent_id is None:
            return
        self._navigate_to(parent_id)

    def _reset_tree(self):
        if not messagebox.askyesno("Baum zurücksetzen?", "Wirklich den gesamten Gate-Baum verwerfen?"):
            return
        self._cancel_drawing()
        self.gate_tree = new_gate_tree()
        self.current_node_id = "root"
        self._node_counter = 0
        self.current_strategy_name = None
        self.current_strategy_path = None
        self._strategy_dirty = False
        self.strategy_var.set("(unbenannt, nicht gespeichert)")
        self._redraw()

    # ---------------------------------------------------------- gating strategies (whole-tree save/load)
    def _mark_strategy_dirty(self):
        self._strategy_dirty = True

    def _refresh_strategy_combo(self):
        self._strategies = list_saved_strategies(self.strategies_dir)
        names = [n for n, _ in self._strategies]
        self.strategy_combo["values"] = names
        if self.current_strategy_name in names:
            self.strategy_var.set(self.current_strategy_name)
        elif self.current_strategy_name is None:
            self.strategy_var.set("(unbenannt, nicht gespeichert)")

    def _strategy_path_for_name(self, name: str) -> str | None:
        for n, p in self._strategies:
            if n == name:
                return p
        return None

    def _confirm_discard_current_strategy(self) -> bool:
        if not self._strategy_dirty:
            return True
        return messagebox.askyesno(
            "Ungespeicherte Änderungen",
            "Die aktuelle Gating-Strategie hat ungespeicherte Änderungen. Trotzdem verwerfen und wechseln?"
        )

    def _new_strategy(self):
        if not self._confirm_discard_current_strategy():
            return
        self._cancel_drawing()
        self.gate_tree = new_gate_tree()
        self.current_node_id = "root"
        self._node_counter = 0
        self.current_strategy_name = None
        self.current_strategy_path = None
        self._strategy_dirty = False
        self.strategy_var.set("(unbenannt, nicht gespeichert)")
        self._redraw()
        self.status_var.set("Neue, leere Gating-Strategie gestartet.")

    def _save_strategy(self):
        if self.current_strategy_name is None:
            self._save_strategy_as()
            return
        path = save_gate_tree(self.gate_tree, self.current_strategy_name, self.strategies_dir)
        self.current_strategy_path = path
        self._strategy_dirty = False
        self._refresh_strategy_combo()
        self.status_var.set(f"Gating-Strategie '{self.current_strategy_name}' gespeichert.")

    def _save_strategy_as(self):
        name = simpledialog.askstring(
            "Strategie speichern unter...", "Name der Gating-Strategie:",
            initialvalue=self.current_strategy_name or "", parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showwarning("Name fehlt", "Der Name darf nicht leer sein.")
            return
        path = save_gate_tree(self.gate_tree, name, self.strategies_dir)
        self.current_strategy_name = name
        self.current_strategy_path = path
        self._strategy_dirty = False
        self._refresh_strategy_combo()
        self.status_var.set(f"Gating-Strategie '{name}' gespeichert unter:\n{path}")

    def _on_strategy_selected(self, _event=None):
        name = self.strategy_var.get()
        if name == self.current_strategy_name:
            return
        path = self._strategy_path_for_name(name)
        if path is None:
            return
        if not self._confirm_discard_current_strategy():
            self.strategy_var.set(self.current_strategy_name or "(unbenannt, nicht gespeichert)")
            return
        self._load_strategy(path, name)

    def _load_strategy(self, path: str, name: str):
        try:
            tree, node_counter = load_gate_tree(path)
        except Exception as exc:
            messagebox.showerror("Fehler beim Laden", f"Konnte Strategie nicht laden:\n{exc}")
            return
        self._cancel_drawing()
        self.gate_tree = tree
        self.current_node_id = "root"
        self._node_counter = node_counter
        self.current_strategy_name = name
        self.current_strategy_path = path
        self._strategy_dirty = False
        self._redraw()
        self.status_var.set(f"Gating-Strategie '{name}' geladen ({len(tree) - 1} Gate(s)).")

    def _delete_strategy(self):
        if self.current_strategy_name is None or self.current_strategy_path is None:
            messagebox.showinfo("Keine Strategie geladen", "Es ist keine gespeicherte Strategie aktuell geladen.")
            return
        if not messagebox.askyesno(
            "Strategie löschen?",
            f"Gespeicherte Datei für '{self.current_strategy_name}' wirklich löschen? "
            "(Der gerade angezeigte Gate-Baum bleibt bis zum Wechsel/Schließen erhalten, ist danach aber nicht "
            "mehr wiederherstellbar, falls nicht anderweitig gespeichert.)"
        ):
            return
        delete_gate_tree(self.current_strategy_path)
        self.current_strategy_name = None
        self.current_strategy_path = None
        self._strategy_dirty = True
        self._refresh_strategy_combo()
        self.strategy_var.set("(unbenannt, nicht gespeichert)")
        self.status_var.set("Gespeicherte Strategie-Datei gelöscht.")

    # ---------------------------------------------------------- drawing
    def _quadrant_angle_value(self) -> float:
        try:
            return float(self.quadrant_angle_var.get())
        except ValueError:
            return 0.0

    def _start_drawing(self):
        if self.active_selector is not None or self._picking_point_center:
            return
        self._ensure_2d_plot_for_gating()
        self.editing_node_id = None
        self.finish_button.configure(text="Schritt übernehmen")
        fcs = self._get_data(self.current_path)
        df = fcs.df
        shape = self.shape_var.get()
        self.pending_shape_coords = {
            "x_channel": self.x_channel_var.get(),
            "y_channel": self.y_channel_var.get(),
            "shape": shape,
        }

        if shape == "Polygon":
            self.active_selector = PolygonSelector(self.ax, onselect=lambda verts: None, useblit=True)
        elif shape == "Rectangle":
            self.active_selector = RectangleSelector(
                self.ax, onselect=lambda e1, e2: None, useblit=True, interactive=True
            )
        elif shape == "Ellipse":
            self.active_selector = EllipseSelector(
                self.ax, onselect=lambda e1, e2: None, useblit=True, interactive=True
            )
        elif shape in QUADRANT_SHAPES:
            self.pending_shape_coords["center"] = None
            self._picking_point_center = True
            self.canvas.get_tk_widget().configure(cursor="crosshair")

        self.x_channel_combo.configure(state="disabled")
        self.y_channel_combo.configure(state="disabled")
        self.shape_combo.configure(state="disabled")
        self.draw_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        if shape in QUADRANT_SHAPES:
            self.finish_button.configure(state="disabled")
            self.status_var.set(
                "Quadranten-Gate: Klicke auf den Kreuzungspunkt der beiden Trennlinien "
                "(erzeugt 4 Kind-Gates, eines je Quadrant)."
            )
        else:
            self.finish_button.configure(state="normal")
            self.status_var.set(
                f"{shape}-Gate: "
                + (
                    "Punkte anklicken, letzten Punkt doppelklicken zum Schließen."
                    if shape == "Polygon"
                    else "Ziehen zum Aufziehen der Form, danach Ecken/Ränder nachziehbar."
                )
                + " Dann 'Schritt übernehmen' klicken."
            )

    def _on_quadrant_center_click(self, event):
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        self.pending_shape_coords["center"] = (float(event.xdata), float(event.ydata))
        self._picking_point_center = False
        self.canvas.get_tk_widget().configure(cursor="")
        self.finish_button.configure(state="normal")
        self._redraw()
        self.status_var.set(
            f"Mittelpunkt bei ({event.xdata:.4g}, {event.ydata:.4g}) gesetzt — 'Schritt übernehmen' legt die "
            "4 Quadranten-Gates an. Erneut klicken verschiebt den Mittelpunkt."
        )

    def _finish_quadrant_step(self):
        coords = self.pending_shape_coords
        center = coords.get("center") if coords else None
        if center is None:
            messagebox.showerror(
                "Kein Mittelpunkt gesetzt", "Bitte zuerst auf den Kreuzungspunkt des Quadranten-Gates klicken."
            )
            return
        x_ch, y_ch = coords["x_channel"], coords["y_channel"]
        angle_deg = self._quadrant_angle_value() if coords["shape"] == "Quadrant (Winkel)" else 0.0
        cx, cy = center

        created_names = []
        for x_sign, y_sign in (("-", "-"), ("+", "-"), ("-", "+"), ("+", "+")):
            step = GateStep(
                x_channel=x_ch, y_channel=y_ch, shape="quadrant",
                params={"cx": cx, "cy": cy, "angle_deg": angle_deg, "x_sign": x_sign, "y_sign": y_sign},
            )
            node_id = f"node_{self._node_counter}"
            self._node_counter += 1
            name = f"{x_ch}{x_sign}/{y_ch}{y_sign}"
            add_child_node(self.gate_tree, self.current_node_id, step, name, node_id)
            created_names.append(name)

        self._mark_strategy_dirty()
        self._teardown_selector()
        self._redraw()
        self.status_var.set(
            f"4 Quadranten-Gates hinzugefügt ({', '.join(created_names)}). Umbenennen über den Gate-Baum rechts."
        )

    def _finish_step(self):
        if self.pending_shape_coords and self.pending_shape_coords.get("shape") in QUADRANT_SHAPES:
            self._finish_quadrant_step()
            return
        if self.active_selector is None:
            return
        shape = self.pending_shape_coords["shape"]
        x_ch = self.pending_shape_coords["x_channel"]
        y_ch = self.pending_shape_coords["y_channel"]

        try:
            if shape == "Polygon":
                verts = [[float(vx), float(vy)] for vx, vy in self.active_selector.verts]
                if len(verts) < 3:
                    raise ValueError("Polygon braucht mindestens 3 Punkte.")
                step = GateStep(x_channel=x_ch, y_channel=y_ch, shape="polygon", vertices=verts)
            elif shape == "Rectangle":
                xmin, xmax, ymin, ymax = self.active_selector.extents
                if xmin == xmax or ymin == ymax:
                    raise ValueError("Rechteck hat keine Fläche.")
                verts = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]
                step = GateStep(x_channel=x_ch, y_channel=y_ch, shape="rectangle", vertices=verts)
            elif shape == "Ellipse":
                xmin, xmax, ymin, ymax = self.active_selector.extents
                if xmin == xmax or ymin == ymax:
                    raise ValueError("Ellipse hat keine Fläche.")
                params = {
                    "cx": (xmin + xmax) / 2,
                    "cy": (ymin + ymax) / 2,
                    "rx": (xmax - xmin) / 2,
                    "ry": (ymax - ymin) / 2,
                }
                step = GateStep(x_channel=x_ch, y_channel=y_ch, shape="ellipse", params=params)
            else:
                raise ValueError(f"Unbekannte Form: {shape}")
        except Exception as exc:
            messagebox.showerror("Gate unvollständig", str(exc))
            return

        if self.editing_node_id is not None:
            node = self.gate_tree[self.editing_node_id]
            node.step = step  # node id, name and children stay exactly as they were
            self._mark_strategy_dirty()
            message = f"Gate '{node.name}' aktualisiert — Name und Kind-Gates bleiben erhalten."
            self.editing_node_id = None
        else:
            # Default name is just the axis pair — matches what you'll actually
            # remember this gate by. The rename prompt (defaulting to that
            # name) opens right away so you can commit a real name in one go;
            # Cancel/closing it just keeps the axis-pair default.
            default_name = f"{x_ch} / {y_ch}"
            node_id = f"node_{self._node_counter}"
            self._node_counter += 1
            add_child_node(self.gate_tree, self.current_node_id, step, default_name, node_id)
            self._mark_strategy_dirty()
            self._teardown_selector()
            self._redraw()
            self._rename_node(node_id)
            self.status_var.set(
                f"Kind-Gate '{self.gate_tree[node_id].name}' hinzugefügt (Ansicht bleibt auf der aktuellen Ebene — "
                "so lassen sich weitere Geschwister-Gates zeichnen). Doppelklick ins Gate wechselt in diese "
                "Population."
            )
            return

        self._teardown_selector()
        self._redraw()
        self.status_var.set(message)

    def _cancel_drawing(self):
        was_drawing = self.active_selector is not None or self._picking_point_center or self.pending_shape_coords is not None
        if was_drawing:
            self._teardown_selector()
            self._redraw()
            self.status_var.set("Zeichnen abgebrochen.")

    def _teardown_selector(self):
        if self.active_selector is not None:
            self.active_selector.set_active(False)
            self.active_selector.disconnect_events()
            self.active_selector = None
        self._picking_point_center = False
        self.canvas.get_tk_widget().configure(cursor="")
        self.pending_shape_coords = None
        self.editing_node_id = None
        self.x_channel_combo.configure(state="readonly")
        self.y_channel_combo.configure(state="readonly")
        self.shape_combo.configure(state="readonly")
        self.draw_button.configure(state="normal")
        self.finish_button.configure(state="disabled", text="Schritt übernehmen")
        self.cancel_button.configure(state="disabled")

    # ---------------------------------------------------------- scope / save
    def _update_scope_hint(self):
        scope = self.scope_var.get()
        if scope == "all":
            self.scope_hint_var.set("Gilt für ALLE Dateien im Ordner.")
        elif scope == "group":
            self.scope_hint_var.set(
                f'Gilt für alle Dateien, deren Name "{self.pattern_var.get() or "<Pattern>"}" enthält.'
            )
        elif scope == "file" and self.current_path:
            self.scope_hint_var.set(f"Gilt NUR für: {os.path.basename(self.current_path)}")

    def _save_gate_set(self):
        steps = node_steps(self.gate_tree, self.current_node_id)
        if not steps:
            messagebox.showwarning(
                "Keine Schritte",
                "Die aktuelle Ansicht ist die Wurzel (keine Gates). Bitte zuerst einen Gate-Schritt zeichnen "
                "und/oder per Doppelklick in die gewünschte Population navigieren."
            )
            return
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Name fehlt", "Bitte einen Namen für das Gate-Set eingeben.")
            return

        scope_type = self.scope_var.get()
        scope = {"type": scope_type}
        if scope_type == "group":
            pattern = self.pattern_var.get().strip()
            if not pattern:
                messagebox.showwarning("Pattern fehlt", "Bitte ein Pattern für scope=group eingeben.")
                return
            scope["pattern"] = pattern
        elif scope_type == "file":
            scope["filename"] = os.path.basename(self.current_path)

        gate_set = GateSet(name=name, scope=scope, steps=steps)
        out_path = save_gate_set(gate_set, self.gates_dir)

        self.existing_gate_sets = load_all_gate_sets(self.gates_dir)
        self._refresh_existing_listbox()
        messagebox.showinfo(
            "Gespeichert",
            f"Gate-Set '{name}' gespeichert unter:\n{out_path}\n\n"
            f"({len(steps)} Schritt(e) auf dem Pfad Wurzel → '{self.gate_tree[self.current_node_id].name}')"
        )

    # ---------------------------------------------------------- kinetics tab
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

    def _require_named_population(self, gate_source_var: tk.StringVar) -> str | None:
        """Guard for the 'all files' batch exports / cohort computation: the
        Live Gating-tab preview only applies to the currently selected file,
        so batch operations need an explicit saved gate-set name (or
        'ungegated') instead."""
        source = gate_source_var.get()
        if source == GATE_SOURCE_LIVE:
            messagebox.showwarning(
                "Population nicht geeignet",
                "\"Live (Gating-Tab-Ansicht)\" gilt nur für die aktuell gewählte Datei und kann nicht auf alle "
                "Dateien angewendet werden. Bitte zuerst ein gespeichertes Gate-Set (oder \"Ungegated\") als "
                "Population auswählen."
            )
            return None
        return source

    def _named_population_mask(self, df, filename: str, source: str) -> tuple[np.ndarray, list[str]]:
        if source == GATE_SOURCE_NONE:
            return np.ones(len(df), dtype=bool), []
        return apply_named_gate_set(df, filename, self.existing_gate_sets, source)

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

    # ---------------------------------------------------------- clipboard
    def _copy_to_clipboard(self, figure, status_var: tk.StringVar | None = None):
        try:
            copy_figure_to_clipboard(figure)
        except Exception as exc:
            messagebox.showerror(
                "Zwischenablage nicht verfügbar",
                f"Konnte den Plot nicht in die Zwischenablage kopieren:\n{exc}"
            )
            return
        message = "Plot in die Zwischenablage kopiert — mit Strg+V einfügbar."
        if status_var is not None:
            status_var.set(message)
        else:
            messagebox.showinfo("Kopiert", message)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", default=os.path.join(os.path.dirname(__file__), "..", "Ca2+_kinetic"))
    parser.add_argument("--gates-dir", default=os.path.join(os.path.dirname(__file__), "..", "gates"))
    args = parser.parse_args()

    root = tk.Tk()
    root.title("Ca2+ Kinetik - Gate Editor")
    root.geometry("1280x800")
    GatingApp(root, input_dir=args.input_dir, gates_dir=args.gates_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
