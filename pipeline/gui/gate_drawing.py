"""Drawing new gates and saving gate-sets."""
from __future__ import annotations

import os
from tkinter import messagebox

from matplotlib.widgets import EllipseSelector, PolygonSelector, RectangleSelector

from gating import (
    GateSet,
    GateStep,
    add_child_node,
    dependent_overrides,
    load_all_gate_sets,
    node_steps,
    save_gate_set,
)
from gui.constants import QUADRANT_SHAPES


class GateDrawingMixin:
    """Interactive selectors (polygon/rectangle/ellipse/quadrant), finishing a gate step and saving it as a named gate-set."""

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
        # populations saved on top of the general variant of this gate (e.g. 'CD19+'
        # below 'Lymphocytes') must follow a file/group-specific re-definition too
        dependents = []
        if scope_type != "all":
            probe = os.path.basename(self.current_path)
            if scope_type == "group":
                probe = next((os.path.basename(p) for p in self.fcs_paths if gate_set.matches(os.path.basename(p))),
                             probe)
            dependents = dependent_overrides(self.existing_gate_sets, gate_set, probe)
        out_path = save_gate_set(gate_set, self.gates_dir)
        note = ""
        if dependents:
            where = f"Datei '{scope.get('filename')}'" if scope_type == "file" else f"Gruppe '{scope.get('pattern')}'"
            listing = ", ".join(d.name for d in dependents[:8]) + (" …" if len(dependents) > 8 else "")
            if messagebox.askyesno(
                "Abhängige Populationen anpassen?",
                f"{len(dependents)} gespeicherte Population(en) liegen unter '{name}' und verwenden für {where} noch "
                f"das bisherige '{name}'-Gate:\n{listing}\n\n"
                f"Für {where} ebenfalls auf das neue '{name}'-Gate umstellen? (empfohlen — sonst rechnen diese "
                "Populationen hier weiter mit dem alten Eltern-Gate)",
            ):
                for d in dependents:
                    save_gate_set(d, self.gates_dir)
                note = f"\n\n{len(dependents)} abhängige Population(en) für {where} mit angepasst."

        self.existing_gate_sets = load_all_gate_sets(self.gates_dir)
        self._refresh_existing_listbox()
        messagebox.showinfo(
            "Gespeichert",
            f"Gate-Set '{name}' gespeichert unter:\n{out_path}\n\n"
            f"({len(steps)} Schritt(e) auf dem Pfad Wurzel → '{self.gate_tree[self.current_node_id].name}')"
            f"{note}"
        )
