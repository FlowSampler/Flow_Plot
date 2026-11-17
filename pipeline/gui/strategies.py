"""Gating strategies: saving/loading whole gate trees."""
from __future__ import annotations

from tkinter import messagebox, simpledialog

from gating import delete_gate_tree, list_saved_strategies, load_gate_tree, new_gate_tree, save_gate_tree


class StrategyMixin:
    """New/save/save-as/load/delete of gating strategies."""

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
