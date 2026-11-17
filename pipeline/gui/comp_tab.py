"""Kompensation tab: editing the spillover matrix."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from gating import list_gate_set_names
from compensation import CompMatrix, export_matrix_csv, import_matrix_csv, save_matrix
from transforms import TRANSFORM_NAMES, apply_axis_scale
from gui.constants import GATE_SOURCE_NONE, COMP_MATRIX_FILE


class CompensationTabMixin:
    """Matrix grid, undo, CSV import/export and drag-to-compensate on the NxN plot."""

    _NON_FLUOR_PREFIXES = ("FSC", "SSC", "Time", "TIME", "Event", "Width", "Height")

    def _build_comp_tab(self, parent):
        self.comp_work: CompMatrix | None = None  # the matrix being edited (not yet applied)
        self._comp_undo: list[tuple[list[str], np.ndarray]] = []
        self._comp_plot: dict | None = None
        self._comp_drag: dict | None = None
        self._comp_grid_vars: dict[tuple[int, int], tk.StringVar] = {}
        self._comp_grid_entries: dict[tuple[int, int], tk.Entry] = {}
        self._comp_grid_channels: list[str] = []

        ttk.Label(
            parent,
            text="Die Population im Plot GREIFEN und ZIEHEN: senkrecht ändert den Spillover X → Y (Zeile X, "
            "Spalte Y der Matrix), waagerecht Y → X. Rote Linie = Median von Y entlang X; bei richtiger "
            "Kompensation verläuft sie waagerecht auf Höhe der grauen Linie (Median der X-negativen Zellen). "
            "Am besten an den Einzelfärbungen (Compensation Controls) prüfen — Datei links wählen. Änderungen wirken "
            "erst nach 'Speichern & auf alle Dateien anwenden'.",
            foreground="#555", wraplength=1150, justify="left",
        ).pack(anchor="w", padx=6, pady=(6, 2))

        row0 = ttk.Frame(parent)
        row0.pack(fill="x", padx=4, pady=2)
        ttk.Label(row0, text="Matrix laden:").pack(side="left")
        ttk.Button(row0, text="aus aktueller Datei ($SPILL)", command=self._comp_load_from_file).pack(side="left", padx=2)
        ttk.Button(row0, text="gespeicherte Experiment-Matrix", command=self._comp_load_experiment).pack(
            side="left", padx=2
        )
        ttk.Button(row0, text="Einheitsmatrix (alles 0 %)", command=self._comp_load_identity).pack(side="left", padx=2)
        ttk.Button(row0, text="CSV importieren...", command=self._comp_import_csv).pack(side="left", padx=(12, 2))
        ttk.Button(row0, text="CSV exportieren...", command=self._comp_export_csv).pack(side="left", padx=2)

        row1 = ttk.Frame(parent)
        row1.pack(fill="x", padx=4, pady=2)
        ttk.Label(row1, text="X (Fluorochrom):").pack(side="left")
        self.comp_x_var = tk.StringVar()
        self.comp_x_combo = ttk.Combobox(row1, textvariable=self.comp_x_var, state="readonly", width=14)
        self.comp_x_combo.pack(side="left", padx=2)
        ttk.Button(row1, text="X↔Y", width=5, command=self._comp_swap_axes).pack(side="left", padx=2)
        ttk.Label(row1, text="Y (Detektor):").pack(side="left", padx=(4, 0))
        self.comp_y_var = tk.StringVar()
        self.comp_y_combo = ttk.Combobox(row1, textvariable=self.comp_y_var, state="readonly", width=14)
        self.comp_y_combo.pack(side="left", padx=2)
        for combo in (self.comp_x_combo, self.comp_y_combo):
            combo.bind("<<ComboboxSelected>>", lambda e: self._redraw_comp())
        ttk.Label(row1, text="Population:").pack(side="left", padx=(12, 0))
        self.comp_pop_var = tk.StringVar(value=GATE_SOURCE_NONE)
        self.comp_pop_combo = ttk.Combobox(row1, textvariable=self.comp_pop_var, state="readonly", width=22)
        self.comp_pop_combo.pack(side="left", padx=2)
        self.comp_pop_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw_comp())
        ttk.Label(row1, text="Achsen:").pack(side="left", padx=(12, 0))
        self.comp_scale_var = tk.StringVar(value="Logicle")
        scale_combo = ttk.Combobox(row1, textvariable=self.comp_scale_var, state="readonly",
                                   values=TRANSFORM_NAMES, width=8)
        scale_combo.pack(side="left", padx=2)
        scale_combo.bind("<<ComboboxSelected>>", lambda e: self._redraw_comp())
        ttk.Label(row1, text="Max. Punkte:").pack(side="left", padx=(12, 0))
        self.comp_max_points_var = tk.StringVar(value="30000")
        mp = ttk.Entry(row1, textvariable=self.comp_max_points_var, width=7)
        mp.pack(side="left", padx=2)
        mp.bind("<Return>", lambda e: self._redraw_comp())

        row2 = ttk.Frame(parent)
        row2.pack(fill="x", padx=4, pady=2)
        ttk.Button(row2, text="Rückgängig", command=self._comp_undo_last).pack(side="left", padx=2)
        ttk.Button(row2, text="Speichern & auf alle Dateien anwenden", command=self._comp_save_apply).pack(
            side="left", padx=2
        )
        self.comp_info_var = tk.StringVar(value="")
        ttk.Label(row2, textvariable=self.comp_info_var, foreground="#a33").pack(side="left", padx=12)

        body = ttk.PanedWindow(parent, orient="horizontal")
        body.pack(fill="both", expand=True, padx=4, pady=4)
        plot_frame = ttk.Frame(body)
        body.add(plot_frame, weight=1)
        self.comp_figure = Figure(figsize=(6, 5))
        self.comp_ax = self.comp_figure.add_subplot(111)
        self.comp_canvas = FigureCanvasTkAgg(self.comp_figure, master=plot_frame)
        self.comp_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.comp_canvas.mpl_connect("button_press_event", self._comp_on_press)
        self.comp_canvas.mpl_connect("motion_notify_event", self._comp_on_motion)
        self.comp_canvas.mpl_connect("button_release_event", self._comp_on_release)
        ttk.Button(
            plot_frame, text="Plot in Zwischenablage kopieren", command=lambda: self._copy_to_clipboard(self.comp_figure)
        ).pack(anchor="w", pady=(4, 0))

        grid_outer = ttk.Frame(body)
        body.add(grid_outer, weight=1)
        ttk.Label(
            grid_outer, text="Spillover-Matrix in %  (Zeile = Fluorochrom/Quelle, Spalte = Detektor; Klick auf eine "
            "Zelle zeigt das Paar, Eingabe + Enter übernimmt den Wert)",
            foreground="#555", wraplength=520, justify="left",
        ).pack(anchor="w", padx=4)
        canvas = tk.Canvas(grid_outer, highlightthickness=0)
        gx = ttk.Scrollbar(grid_outer, orient="horizontal", command=canvas.xview)
        gy = ttk.Scrollbar(grid_outer, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=gx.set, yscrollcommand=gy.set)
        gy.pack(side="right", fill="y")
        gx.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)
        self.comp_grid_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=self.comp_grid_frame, anchor="nw")
        self.comp_grid_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

    # ---- working matrix
    def _comp_fluor_channels(self, channels: list[str]) -> list[str]:
        return [c for c in channels if not c.startswith(self._NON_FLUOR_PREFIXES)]

    def _comp_ensure_work(self):
        if self.comp_work is not None or not self.current_path:
            return
        raw = self._get_raw_data(self.current_path)
        if self.experiment_comp is not None:
            self.comp_work = self.experiment_comp.copy()
        elif raw.spill is not None:
            self.comp_work = raw.spill.copy()
        else:
            chs = self._comp_fluor_channels(list(raw.df.columns))
            self.comp_work = CompMatrix(chs, np.eye(len(chs)), name="Einheitsmatrix")
        self._comp_update_info()

    def _comp_set_work(self, matrix: CompMatrix, push_undo: bool = True):
        if push_undo and self.comp_work is not None:
            self._comp_push_undo()
        self.comp_work = matrix
        self._comp_update_info()
        self._redraw_comp()

    def _comp_push_undo(self):
        self._comp_undo.append((list(self.comp_work.channels), self.comp_work.spill.copy()))
        del self._comp_undo[:-100]

    def _comp_undo_last(self):
        if not self._comp_undo:
            self.comp_info_var.set("Nichts rückgängig zu machen.")
            return
        channels, spill = self._comp_undo.pop()
        self.comp_work = CompMatrix(channels, spill, name=self.comp_work.name if self.comp_work else "")
        self._comp_update_info()
        self._redraw_comp()

    def _comp_active_reference(self) -> CompMatrix | None:
        """What the working matrix is compared against for the 'unsaved' hint."""
        if self.comp_mode == "experiment":
            return self.experiment_comp
        if self.comp_mode == "file" and self.current_path:
            return self._get_raw_data(self.current_path).spill
        return None

    def _comp_update_info(self):
        if self.comp_work is None:
            self.comp_info_var.set("")
            return
        if self.comp_work.same_values(self._comp_active_reference()):
            self.comp_info_var.set("")
        else:
            self.comp_info_var.set("Matrix weicht von der angewendeten ab — noch nicht gespeichert/angewendet.")

    def _comp_load_from_file(self):
        if not self.current_path:
            return
        raw = self._get_raw_data(self.current_path)
        if raw.spill is None:
            messagebox.showinfo("Keine Matrix", f"'{raw.filename}' enthält keine Spillover-Matrix ($SPILL).")
            return
        self._comp_set_work(raw.spill.copy())

    def _comp_load_experiment(self):
        if self.experiment_comp is None:
            messagebox.showinfo("Keine Experiment-Matrix", "Für dieses Experiment ist noch keine Matrix gespeichert.")
            return
        self._comp_set_work(self.experiment_comp.copy())

    def _comp_load_identity(self):
        if not self.current_path:
            return
        chs = (self.comp_work.channels if self.comp_work is not None
               else self._comp_fluor_channels(list(self._get_raw_data(self.current_path).df.columns)))
        self._comp_set_work(CompMatrix(list(chs), np.eye(len(chs)), name="Einheitsmatrix"))

    def _comp_import_csv(self):
        path = filedialog.askopenfilename(
            title="Kompensationsmatrix (CSV) importieren", filetypes=[("CSV", "*.csv"), ("Alle", "*.*")],
            initialdir=self.comp_dir if os.path.isdir(self.comp_dir) else self.input_dir,
        )
        if not path:
            return
        try:
            matrix = import_matrix_csv(path)
        except Exception as exc:
            messagebox.showerror("Import fehlgeschlagen", f"Die Datei konnte nicht als Matrix gelesen werden:\n{exc}")
            return
        if self.current_path:
            missing = [c for c in matrix.channels if c not in self._get_raw_data(self.current_path).df.columns]
            if missing:
                messagebox.showwarning(
                    "Kanäle fehlen",
                    "Diese Kanäle der Matrix gibt es in der aktuellen Datei nicht (werden dort ignoriert):\n"
                    + ", ".join(missing)
                )
        self._comp_set_work(matrix)

    def _comp_export_csv(self):
        if self.comp_work is None:
            return
        os.makedirs(self.comp_dir, exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Kompensationsmatrix als CSV speichern", defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialdir=self.comp_dir, initialfile="matrix.csv",
        )
        if path:
            export_matrix_csv(self.comp_work, path)

    def _comp_save_apply(self):
        if self.comp_work is None:
            return
        matrix = self.comp_work.copy()
        matrix.name = "Experiment-Matrix"
        try:
            save_matrix(matrix, os.path.join(self.comp_dir, COMP_MATRIX_FILE))
        except OSError as exc:
            messagebox.showerror("Speichern fehlgeschlagen", str(exc))
            return
        self.experiment_comp = matrix
        self._set_comp_mode("experiment")
        self._comp_update_info()
        note = ""
        if self.existing_gate_sets:
            note = ("\n\nHinweis: Gates auf Fluoreszenz-Kanälen wurden auf der vorherigen Kompensation gezeichnet "
                    "und sollten kurz kontrolliert werden.")
        messagebox.showinfo(
            "Kompensation angewendet",
            f"Matrix gespeichert unter:\n{os.path.join(self.comp_dir, COMP_MATRIX_FILE)}\n\n"
            f"Alle Tabs und Exporte verwenden jetzt die Experiment-Matrix.{note}"
        )

    # ---- channel / population pickers
    def _refresh_comp_channels(self, _file_channels: list[str] | None = None):
        if not hasattr(self, "comp_x_combo") or not self.current_path:
            return
        self._comp_ensure_work()
        raw = self._get_raw_data(self.current_path)
        chs = [c for c in self.comp_work.channels if c in raw.df.columns]
        labels = chs
        self.comp_x_combo["values"] = labels
        self.comp_y_combo["values"] = labels
        if chs:
            if self.comp_x_var.get() not in chs:
                self.comp_x_var.set(chs[0])
            if self.comp_y_var.get() not in chs or self.comp_y_var.get() == self.comp_x_var.get():
                self.comp_y_var.set(next((c for c in chs if c != self.comp_x_var.get()), chs[0]))
        pops = [GATE_SOURCE_NONE] + list_gate_set_names(self.existing_gate_sets)
        self.comp_pop_combo["values"] = pops
        if self.comp_pop_var.get() not in pops:
            self.comp_pop_var.set(GATE_SOURCE_NONE)

    def _comp_swap_axes(self):
        x, y = self.comp_x_var.get(), self.comp_y_var.get()
        self.comp_x_var.set(y)
        self.comp_y_var.set(x)
        self._redraw_comp()

    def _comp_select_pair(self, i: int, j: int):
        chs = self.comp_work.channels
        if i == j:
            return
        self.comp_x_var.set(chs[i])
        self.comp_y_var.set(chs[j])
        self._redraw_comp()

    # ---- matrix grid
    def _comp_rebuild_grid(self):
        for w in self.comp_grid_frame.winfo_children():
            w.destroy()
        self._comp_grid_vars.clear()
        self._comp_grid_entries.clear()
        chs = list(self.comp_work.channels)
        self._comp_grid_channels = chs
        names = self.panel_marker_names if hasattr(self, "panel_marker_names") else {}

        def head(ch):
            return f"{ch}\n{names[ch]}" if ch in names else ch

        ttk.Label(self.comp_grid_frame, text="Quelle ↓  Detektor →", foreground="#555").grid(row=0, column=0, padx=2)
        for j, ch in enumerate(chs):
            ttk.Label(self.comp_grid_frame, text=head(ch), justify="center").grid(row=0, column=j + 1, padx=1)
        for i, ch in enumerate(chs):
            ttk.Label(self.comp_grid_frame, text=head(ch)).grid(row=i + 1, column=0, sticky="e", padx=(2, 4))
            for j in range(len(chs)):
                if i == j:
                    tk.Label(self.comp_grid_frame, text="100", width=7, bg="#ddd").grid(row=i + 1, column=j + 1)
                    continue
                var = tk.StringVar()
                entry = tk.Entry(self.comp_grid_frame, textvariable=var, width=7, justify="right", relief="solid", bd=1)
                entry.grid(row=i + 1, column=j + 1, padx=0, pady=0)
                entry.bind("<Return>", lambda e, a=i, b=j: self._comp_cell_commit(a, b))
                entry.bind("<FocusOut>", lambda e, a=i, b=j: self._comp_cell_commit(a, b, redraw_pair=False))
                entry.bind("<Button-1>", lambda e, a=i, b=j: self._comp_select_pair(a, b))
                self._comp_grid_vars[(i, j)] = var
                self._comp_grid_entries[(i, j)] = entry

    def _comp_refresh_grid(self):
        if self.comp_work is None:
            return
        if self.comp_work.channels != self._comp_grid_channels:
            self._comp_rebuild_grid()
        chs = self.comp_work.channels
        sel = (chs.index(self.comp_x_var.get()) if self.comp_x_var.get() in chs else -1,
               chs.index(self.comp_y_var.get()) if self.comp_y_var.get() in chs else -1)
        for (i, j), var in self._comp_grid_vars.items():
            v = self.comp_work.spill[i, j] * 100
            var.set(f"{v:.2f}")
            if (i, j) == sel:
                bg = "#ffe066"
            elif (j, i) == sel:
                bg = "#fff4c2"
            elif abs(v) >= 10:
                bg = "#f8cbad"
            elif abs(v) >= 1:
                bg = "#fbe5d6"
            else:
                bg = "white"
            self._comp_grid_entries[(i, j)].configure(bg=bg)

    def _comp_cell_commit(self, i: int, j: int, redraw_pair: bool = True):
        var = self._comp_grid_vars.get((i, j))
        if var is None or self.comp_work is None:
            return
        try:
            value = float(var.get().strip().replace(",", ".")) / 100.0
        except ValueError:
            var.set(f"{self.comp_work.spill[i, j] * 100:.2f}")
            return
        if abs(value - self.comp_work.spill[i, j]) < 1e-9:
            return
        self._comp_push_undo()
        self.comp_work.spill[i, j] = value
        self._comp_update_info()
        if redraw_pair:
            self._comp_select_pair(i, j)
        else:
            self._comp_update_artists()
            self._comp_refresh_grid()

    # ---- plot
    def _redraw_comp(self):
        if not hasattr(self, "comp_ax") or not self.current_path:
            return
        self._refresh_comp_channels()
        self._comp_refresh_grid()
        raw = self._get_raw_data(self.current_path)
        x_ch, y_ch = self.comp_x_var.get(), self.comp_y_var.get()
        chs = [c for c in self.comp_work.channels if c in raw.df.columns]

        self.comp_figure.clear()
        self.comp_ax = ax = self.comp_figure.add_subplot(111)
        self._comp_plot = None
        if x_ch not in chs or y_ch not in chs or x_ch == y_ch:
            ax.text(0.5, 0.5, "Bitte zwei verschiedene Kanäle der Matrix wählen.", ha="center", va="center",
                    transform=ax.transAxes)
            self.comp_canvas.draw_idle()
            return

        mask, pop_desc = self._population_mask(self._get_data(self.current_path).df, raw.filename,
                                               self.comp_pop_var.get())
        idx = np.flatnonzero(mask)
        try:
            max_points = max(1000, int(self.comp_max_points_var.get()))
        except ValueError:
            max_points = 30000
        if len(idx) > max_points:
            idx = np.sort(np.random.default_rng(0).choice(idx, max_points, replace=False))
        R = raw.df[chs].to_numpy(dtype=float)[idx]
        full_idx = [self.comp_work.channels.index(c) for c in chs]
        self._comp_plot = {"R": R, "full_idx": full_idx, "ix": chs.index(x_ch), "iy": chs.index(y_ch),
                           "x_ch": x_ch, "y_ch": y_ch}
        cx, cy = self._comp_compute_xy()

        self._comp_plot["scatter"] = ax.scatter(cx, cy, s=2, c="#1f4e8c", alpha=0.35, linewidths=0, rasterized=True)
        (self._comp_plot["median_line"],) = ax.plot([], [], color="red", lw=1.8, marker="o", ms=3)
        self._comp_plot["neg_line"] = ax.axhline(0, color="#777", ls="--", lw=1)
        names = self.panel_marker_names
        ax.set_xlabel(f"{x_ch} ({names[x_ch]})" if x_ch in names else x_ch)
        ax.set_ylabel(f"{y_ch} ({names[y_ch]})" if y_ch in names else y_ch)
        scale = self.comp_scale_var.get()
        ranges = raw.channel_ranges or {}
        apply_axis_scale(ax, "x", scale, channel_range=ranges.get(x_ch, 262144.0))
        apply_axis_scale(ax, "y", scale, channel_range=ranges.get(y_ch, 262144.0))
        for axis, v in (("x", cx), ("y", cy)):
            if len(v):
                lo, hi = np.percentile(v, [0.2, 99.9])
                span = hi - lo or 1.0
                lo, hi = min(lo - 0.05 * span, -100.0), hi + 0.1 * span
                (ax.set_xlim if axis == "x" else ax.set_ylim)(lo, hi)
        self._comp_plot["pop_desc"] = pop_desc
        self._comp_update_artists(draw=False)
        self.comp_figure.tight_layout()
        self.comp_canvas.draw_idle()

    def _comp_sub_spill(self) -> np.ndarray:
        fi = self._comp_plot["full_idx"]
        return self.comp_work.spill[np.ix_(fi, fi)]

    def _comp_compute_xy(self) -> tuple[np.ndarray, np.ndarray]:
        p = self._comp_plot
        try:
            inv = np.linalg.inv(self._comp_sub_spill())
        except np.linalg.LinAlgError:
            inv = np.eye(len(p["full_idx"]))
            self.comp_info_var.set("Matrix ist nicht invertierbar — bitte Werte prüfen.")
        cols = p["R"] @ inv[:, [p["ix"], p["iy"]]]
        return cols[:, 0], cols[:, 1]

    def _comp_update_artists(self, draw: bool = True):
        p = self._comp_plot
        if p is None:
            return
        cx, cy = self._comp_compute_xy()
        p["scatter"].set_offsets(np.column_stack([cx, cy]))
        if len(cx) >= 50:
            # running median of Y over X (equal-count bins), and the X-negative reference level
            edges = np.unique(np.percentile(cx, np.linspace(0, 100, 21)))
            which = np.clip(np.searchsorted(edges, cx, side="right") - 1, 0, len(edges) - 2)
            mx, my = [], []
            for b in range(len(edges) - 1):
                sel = which == b
                if sel.sum() >= 10:
                    mx.append(np.median(cx[sel]))
                    my.append(np.median(cy[sel]))
            p["median_line"].set_data(mx, my)
            neg = cx <= np.percentile(cx, 30)
            p["neg_line"].set_ydata([np.median(cy[neg])] * 2)
        fi = p["full_idx"]
        s_xy = self.comp_work.spill[fi[p["ix"]], fi[p["iy"]]] * 100
        s_yx = self.comp_work.spill[fi[p["iy"]], fi[p["ix"]]] * 100
        self.comp_ax.set_title(
            f"{os.path.basename(self.current_path)} — Population: {p['pop_desc']} ({len(cx)} Punkte)\n"
            f"{p['x_ch']} → {p['y_ch']}: {s_xy:.2f} %     {p['y_ch']} → {p['x_ch']}: {s_yx:.2f} %",
            fontsize=9,
        )
        if draw:
            self.comp_canvas.draw_idle()

    # ---- drag to compensate
    def _comp_on_press(self, event):
        if event.button != 1 or event.inaxes is not self.comp_ax or self._comp_plot is None or event.xdata is None:
            return
        p = self._comp_plot
        fi = p["full_idx"]
        self._comp_drag = {
            "px": event.x, "py": event.y, "x0": event.xdata, "y0": event.ydata,
            "spill0": self.comp_work.spill.copy(),
            "xy": (fi[p["ix"]], fi[p["iy"]]), "yx": (fi[p["iy"]], fi[p["ix"]]),
        }

    def _comp_on_motion(self, event):
        d = self._comp_drag
        if d is None or event.xdata is None or event.ydata is None:
            return
        spill = d["spill0"].copy()
        if abs(event.y - d["py"]) >= abs(event.x - d["px"]):
            # vertical: change X -> Y so the grabbed population (at compensated x0) moves by dy
            if abs(d["x0"]) < 1.0:
                self.comp_info_var.set("Bitte eine Population mit deutlichem X-Signal greifen (nicht bei X ≈ 0).")
                return
            spill[d["xy"]] = d["spill0"][d["xy"]] - (event.ydata - d["y0"]) / d["x0"]
        else:
            if abs(d["y0"]) < 1.0:
                self.comp_info_var.set("Bitte eine Population mit deutlichem Y-Signal greifen (nicht bei Y ≈ 0).")
                return
            spill[d["yx"]] = d["spill0"][d["yx"]] - (event.xdata - d["x0"]) / d["y0"]
        self.comp_work.spill = spill
        self._comp_update_artists()

    def _comp_on_release(self, _event):
        d = self._comp_drag
        self._comp_drag = None
        if d is None or self.comp_work is None:
            return
        if not np.array_equal(d["spill0"], self.comp_work.spill):
            current = self.comp_work.spill
            self.comp_work.spill = d["spill0"]
            self._comp_push_undo()
            self.comp_work.spill = current
        self._comp_update_info()
        self._comp_refresh_grid()
