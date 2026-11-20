"""Gating tab: widget layout."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from transforms import TRANSFORM_NAMES
from gui.constants import SHAPES, SCOPES, HIST_1D, PLOT_TYPES, COLORMAPS, DEFAULT_CHANNEL_PAIR


class GatingTabMixin:
    """Builds the Gating tab (plot, channel/shape pickers, gate tree panel)."""

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
            axis_limits_row, text="Achsen-Limits zurücksetzen (Kanalbereich)", command=self._reset_axis_limits
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
        ttk.Button(
            toolbar_row, text="Plot exportieren...",
            command=lambda: self._open_plot_export(self.figure, self.canvas, "gating", self.status_var)
        ).pack(side="left", padx=(4, 0))

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
        ttk.Button(
            tree_btns, text="Boolean-Gate (UND / ODER / NICHT)...", command=self._create_boolean_gate
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
