"""Statistik tab: histogram of one file + cohort statistics."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from kinetics import compute_ratio, value_stats
from gui.constants import DEFAULT_RATIO_PAIR, GATE_SOURCE_LIVE


class StatsTabMixin:
    """Builds and draws the Statistik tab and the cohort table."""

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

        stat_min_label = ttk.Label(controls, text="Min. Signal:")
        stat_min_label.grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.stat_min_signal_var = tk.StringVar(value="1.0")
        stat_min_entry = ttk.Entry(controls, textvariable=self.stat_min_signal_var, width=6)
        stat_min_entry.grid(row=1, column=5, padx=4, pady=(6, 0))
        # Kanal B and Min. Signal only matter for a ratio
        self.stat_ratio_widgets = [self.stat_channel_b_label, self.stat_channel_b_combo, stat_min_label, stat_min_entry]

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
        stat_btn_row = ttk.Frame(plot_frame)
        stat_btn_row.pack(anchor="w", pady=(4, 0))
        ttk.Button(
            stat_btn_row, text="Plot in Zwischenablage kopieren", command=lambda: self._copy_to_clipboard(self.stat_figure)
        ).pack(side="left")
        ttk.Button(
            stat_btn_row, text="Plot exportieren...",
            command=lambda: self._open_plot_export(self.stat_figure, self.stat_canvas, "statistik")
        ).pack(side="left", padx=(4, 0))

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

    def _update_stat_mode(self):
        self._set_shown(self.stat_ratio_widgets, self.stat_mode_var.get() == "ratio")

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
