"""Gate tree: tree view, navigation, boolean gates, rename/edit/delete."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import numpy as np
from matplotlib.widgets import EllipseSelector, PolygonSelector, RectangleSelector

from gating import (
    GateStep,
    add_child_node,
    make_boolean_step,
    new_gate_tree,
    node_path,
    point_in_step,
    remove_subtree,
)
from gui.constants import SHAPES, HIST_1D


class GateTreeMixin:
    """Everything that works on the gate hierarchy of the current strategy."""

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

    def _create_boolean_gate(self):
        """Combine child gates of the CURRENT population with AND / OR / NOT
        into a new child gate (like FlowJo's Boolean gates). The new gate
        stores copies of the referenced gates' shapes, so it stays valid when
        saved as a gate-set on its own; reshaping a referenced gate later does
        NOT update it (delete + recreate the boolean gate then)."""
        self._cancel_drawing()
        parent_id = self.current_node_id
        children = [c for c in self.gate_tree[parent_id].children]
        if not children:
            messagebox.showinfo(
                "Keine Kind-Gates",
                "Boolean-Gates kombinieren Gates, die DIREKT unter der aktuell angezeigten Population liegen. "
                f"'{self.gate_tree[parent_id].name}' hat noch keine Kind-Gates — erst zeichnen, dann kombinieren."
            )
            return

        win = tk.Toplevel(self)
        win.title("Boolean-Gate erstellen")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        ttk.Label(
            win, text=f"Neues Kind-Gate unter '{self.gate_tree[parent_id].name}' aus diesen Kind-Gates:",
            font=("", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 4))

        op_var = tk.StringVar(value="and")
        op_frame = ttk.Frame(win)
        op_frame.grid(row=1, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Label(op_frame, text="Verknüpfung:").pack(side="left")
        ttk.Radiobutton(op_frame, text="UND (alle Bedingungen)", variable=op_var, value="and").pack(side="left", padx=4)
        ttk.Radiobutton(op_frame, text="ODER (mindestens eine)", variable=op_var, value="or").pack(side="left", padx=4)

        ttk.Label(win, text="verwenden").grid(row=2, column=0, padx=8, pady=(8, 0))
        ttk.Label(win, text="NICHT").grid(row=2, column=1, padx=8, pady=(8, 0))
        ttk.Label(win, text="Gate").grid(row=2, column=2, sticky="w", pady=(8, 0))
        rows = []
        for r, cid in enumerate(children, start=3):
            use, neg = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
            ttk.Checkbutton(win, variable=use).grid(row=r, column=0)
            ttk.Checkbutton(win, variable=neg).grid(row=r, column=1)
            node = self.gate_tree[cid]
            ttk.Label(win, text=f"{node.name}   ({node.step.describe()})", foreground="#333").grid(
                row=r, column=2, sticky="w", padx=(0, 8)
            )
            rows.append((cid, use, neg))
        r = len(children) + 3
        ttk.Label(
            win, text="Beispiele: 'CD4+' UND NICHT 'CD8+' → CD4+CD8-.  Nur ein Gate mit NICHT → alles im Parent "
            "außer diesem Gate.", foreground="#555", wraplength=460, justify="left",
        ).grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 4))
        ttk.Label(win, text="Name:").grid(row=r + 1, column=0, sticky="e", padx=8)
        name_var = tk.StringVar(value="")
        name_entry = ttk.Entry(win, textvariable=name_var, width=40)
        name_entry.grid(row=r + 1, column=1, columnspan=2, sticky="w", pady=4)

        def selected_terms():
            return [(cid, neg.get()) for cid, use, neg in rows if use.get()]

        def suggested_name(*_):
            terms = selected_terms()
            joiner = " & " if op_var.get() == "and" else " | "
            return joiner.join(("NOT " if n else "") + self.gate_tree[c].name for c, n in terms)

        last_suggestion = {"text": ""}

        def update_name(*_):
            if name_var.get() in ("", last_suggestion["text"]):
                last_suggestion["text"] = suggested_name()
                name_var.set(last_suggestion["text"])

        for _, use, neg in rows:
            use.trace_add("write", update_name)
            neg.trace_add("write", update_name)
        op_var.trace_add("write", update_name)

        def create():
            terms = selected_terms()
            if not terms:
                messagebox.showwarning("Keine Gates gewählt", "Bitte mindestens ein Gate anhaken.", parent=win)
                return
            if len(terms) == 1 and not terms[0][1]:
                messagebox.showwarning(
                    "Nur ein Gate", "Ein einzelnes Gate ohne NICHT wäre identisch mit dem Gate selbst.", parent=win
                )
                return
            name = name_var.get().strip() or suggested_name()
            step = make_boolean_step(
                op_var.get(), [(self.gate_tree[c].name, [self.gate_tree[c].step], n) for c, n in terms]
            )
            node_id = f"node_{self._node_counter}"
            self._node_counter += 1
            add_child_node(self.gate_tree, parent_id, step, name, node_id)
            self._mark_strategy_dirty()
            win.destroy()
            self._redraw()
            self.status_var.set(f"Boolean-Gate '{name}' erstellt: {step.describe()}")

        btns = ttk.Frame(win)
        btns.grid(row=r + 2, column=0, columnspan=3, sticky="e", padx=8, pady=8)
        ttk.Button(btns, text="Erstellen", command=create).pack(side="left", padx=4)
        ttk.Button(btns, text="Abbrechen", command=win.destroy).pack(side="left")
        name_entry.focus_set()

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
                if self._step_on_axes(step, x_ch, y_ch) and point_in_step(step, event.xdata, event.ydata):
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
        if step.shape in ("boolean", "range"):
            what = "Boolean-Gates" if step.shape == "boolean" else "Bereichs-Gates (1D)"
            messagebox.showinfo(
                "Nicht unterstützt",
                f"{what} haben keine Ecken zum Verschieben ({step.describe()}). Bitte löschen und neu anlegen."
            )
            return
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
