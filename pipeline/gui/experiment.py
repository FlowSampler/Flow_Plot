"""Experiment folder, file list, hidden (excluded) files and file navigation."""
from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gating import load_all_gate_sets, new_gate_tree
from io_utils import find_fcs_files
from gui.constants import COMP_MODES, EXCLUDED_FILES_FILE


class ExperimentMixin:
    """Choosing/rescanning the experiment folder, hiding files/folders and stepping through the file list."""

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
            fcs_paths = self._filter_excluded(find_fcs_files(self.input_dir, recursive=True))
        except FileNotFoundError as exc:
            messagebox.showwarning("Keine Dateien gefunden", str(exc))
            return
        self._apply_file_list(fcs_paths)

    def _apply_file_list(self, fcs_paths: list[str]):
        """Swap in a new file list (after a rescan, hiding or restoring files)
        while keeping the gate tree, strategy and the current file if it's
        still in the list."""
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
            prev_idx = getattr(self, "_removed_index_hint", 0)
            self._select_file_index(min(prev_idx, len(self.fcs_paths) - 1))
        self._removed_index_hint = 0
        if added or removed:
            self.cohort_df = None  # cohort statistics covered the old file set
        self._refresh_overlay_file_list()
        self._update_restore_excluded_button()

        hidden = f", {len(self.excluded_files)} ausgeblendet" if self.excluded_files else ""
        self.status_var.set(
            f"Dateiliste aktualisiert: {len(added)} neu, {len(removed)} entfernt, "
            f"{len(self.fcs_paths)} gesamt{hidden}."
        )

    # Files the user removed from the list. They stay on disk; only the app
    # ignores them (file list, overlay, batch exports, statistics). Stored as
    # paths relative to the experiment folder in '<experiment>/excluded_files.json',
    # so the choice survives restarts and rescans.
    def _excluded_files_path(self) -> str:
        return os.path.join(self.input_dir, EXCLUDED_FILES_FILE)

    def _load_excluded_files(self):
        self.excluded_files: set[str] = set()
        try:
            with open(self._excluded_files_path(), "r", encoding="utf-8") as fh:
                self.excluded_files = {str(p).replace("\\", "/") for p in json.load(fh).get("excluded", [])}
        except (OSError, ValueError, AttributeError):
            pass

    def _save_excluded_files(self):
        path = self._excluded_files_path()
        try:
            if self.excluded_files:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"excluded": sorted(self.excluded_files)}, fh, indent=2)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass  # read-only experiment folder: hidden only for this session

    # Entries are relative paths with '/' separators: a file ('sub/a.fcs') or
    # a whole folder, marked by a trailing '/' ('sub/day1/'). A hidden folder
    # also hides files copied into it later.
    def _rel_entry(self, path: str) -> str:
        return os.path.relpath(path, self.input_dir).replace(os.sep, "/")

    def _is_excluded(self, path: str) -> bool:
        rel = os.path.normcase(self._rel_entry(path))
        for entry in self.excluded_files:
            e = os.path.normcase(entry)
            if e.endswith(("/", os.sep)):
                if rel.startswith(e) or rel.startswith(e.rstrip("/" + os.sep) + os.sep):
                    return True
            elif rel == e:
                return True
        return False

    def _filter_excluded(self, paths: list[str]) -> list[str]:
        if not self.excluded_files:
            return paths
        return [p for p in paths if not self._is_excluded(p)]

    def _update_restore_excluded_button(self):
        if not hasattr(self, "restore_excluded_button"):
            return
        n = len(self.excluded_files)
        self.restore_excluded_button.configure(
            text=f"Ausgeblendete einblenden ({n})...", state="normal" if n else "disabled"
        )

    def _hide_entries(self, entries: list[str], what: str):
        """Add file/folder entries to the hidden list and drop the matching
        files from the visible list (refusing if nothing would be left)."""
        new_excluded = self.excluded_files | set(entries)
        old, self.excluded_files = self.excluded_files, new_excluded
        remaining = self._filter_excluded(self.fcs_paths)
        if not remaining:
            self.excluded_files = old
            messagebox.showinfo(
                "Nicht möglich", "Damit wären alle Dateien ausgeblendet — mindestens eine muss in der Liste bleiben."
            )
            return
        removed = [p for p in self.fcs_paths if p not in remaining]
        self._cancel_drawing()
        self._save_excluded_files()
        for p in removed:
            self.ov_colors.pop(p, None)
            self.ov_linestyles.pop(p, None)
        sel = self.file_listbox.curselection()
        if sel:  # select the file that moves up into the first removed slot
            self._removed_index_hint = sum(1 for p in self.fcs_paths[: sel[0]] if p in remaining)
        self._apply_file_list(remaining)
        self.status_var.set(
            f"{what}: {len(removed)} Datei(en) aus der Liste entfernt (bleiben auf der Festplatte). "
            f"Wiederherstellen über 'Ausgeblendete einblenden'."
        )

    def _exclude_selected_files(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        paths = [self.fcs_paths[i] for i in sel if i < len(self.fcs_paths)]
        what = f"'{os.path.basename(paths[0])}'" if len(paths) == 1 else f"{len(paths)} markierte Dateien"
        self._hide_entries([self._rel_entry(p) for p in paths], what)

    def _exclude_folders(self):
        """Dialog listing every subfolder that holds visible files; the
        folder(s) of the currently marked file(s) are preselected."""
        counts: dict[str, int] = {}
        for p in self.fcs_paths:
            parts = self._rel_entry(p).split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                key = "/".join(parts[:depth]) + "/"
                counts[key] = counts.get(key, 0) + 1
        if not counts:
            messagebox.showinfo(
                "Keine Unterordner", "Alle Dateien liegen direkt im Experiment-Ordner — es gibt keine Unterordner."
            )
            return
        folders = sorted(counts)
        marked = {
            self._rel_entry(os.path.dirname(self.fcs_paths[i])) + "/"
            for i in self.file_listbox.curselection() if i < len(self.fcs_paths)
        }
        labels = [f"{'    ' * (f.count('/') - 1)}{f}   ({counts[f]} Datei(en))" for f in folders]
        result = self._pick_items(
            "Ordner ausblenden",
            "Ordner wählen, deren .fcs-Dateien (inkl. Unterordner) ausgeblendet werden sollen\n"
            "(Strg/Shift-Klick für mehrere):",
            labels, preselect=[i for i, f in enumerate(folders) if f in marked],
            buttons=[("hide", "Ausblenden"), ("cancel", "Abbrechen")],
        )
        if result is None or result[0] != "hide" or not result[1]:
            return
        chosen = [folders[i] for i in result[1]]
        what = f"Ordner '{chosen[0]}'" if len(chosen) == 1 else f"{len(chosen)} Ordner"
        self._hide_entries(chosen, what)

    def _restore_excluded_files(self):
        if not self.excluded_files:
            return
        entries = sorted(self.excluded_files, key=lambda e: (not e.endswith("/"), e.lower()))
        labels = [f"[Ordner]  {e}" if e.endswith("/") else e for e in entries]
        result = self._pick_items(
            "Ausgeblendete einblenden",
            "Ausgeblendete Dateien/Ordner — markierte wieder in die Liste aufnehmen:",
            labels, preselect=[],
            buttons=[("selected", "Markierte einblenden"), ("all", "Alle einblenden"), ("cancel", "Abbrechen")],
        )
        if result is None or result[0] == "cancel":
            return
        if result[0] == "all":
            self.excluded_files.clear()
        else:
            if not result[1]:
                return
            self.excluded_files -= {entries[i] for i in result[1]}
        self._save_excluded_files()
        try:
            fcs_paths = self._filter_excluded(find_fcs_files(self.input_dir, recursive=True))
        except FileNotFoundError as exc:
            messagebox.showwarning("Keine Dateien gefunden", str(exc))
            return
        self._apply_file_list(fcs_paths)

    def _pick_items(self, title: str, prompt: str, labels: list[str], preselect: list[int],
                    buttons: list[tuple[str, str]]) -> tuple[str, list[int]] | None:
        """Small modal multi-select list. Returns (button key, selected
        indices) or None if the window was closed."""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self.winfo_toplevel())
        win.geometry("560x420")
        ttk.Label(win, text=prompt, justify="left").pack(anchor="w", padx=8, pady=(8, 4))
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8)
        lb = tk.Listbox(frame, selectmode="extended", exportselection=False)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for text in labels:
            lb.insert("end", text)
        for i in preselect:
            lb.selection_set(i)
        if preselect:
            lb.see(preselect[0])
        result: list = [None]

        def finish(key):
            result[0] = (key, list(lb.curselection()))
            win.destroy()

        row = ttk.Frame(win)
        row.pack(fill="x", padx=8, pady=8)
        for key, text in reversed(buttons):
            ttk.Button(row, text=text, command=lambda k=key: finish(k)).pack(side="right", padx=(4, 0))
        win.bind("<Escape>", lambda _e: win.destroy())
        win.grab_set()
        lb.focus_set()
        self.wait_window(win)
        return result[0]

    def _load_experiment(self, root_dir: str) -> bool:
        """Point the whole app at a new experiment folder: .fcs files are
        picked up recursively from it, gate-sets are read from/saved to its
        'gates' subfolder, and exports default into its 'exports' subfolder —
        so one folder holds everything for one experiment."""
        try:
            fcs_paths = find_fcs_files(root_dir, recursive=True)
        except FileNotFoundError as exc:
            messagebox.showerror("Keine FCS-Dateien gefunden", str(exc))
            return False

        self._cancel_drawing()
        win = getattr(self, "_derived_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()  # its formulas belong to the previous experiment
        self._derived_win = None
        self.input_dir = root_dir
        self._load_excluded_files()
        self._load_derived_params()
        fcs_paths = self._filter_excluded(fcs_paths)
        if not fcs_paths:  # everything hidden: show all again rather than an empty, broken view
            self.excluded_files.clear()
            fcs_paths = find_fcs_files(root_dir, recursive=True)
        self._update_restore_excluded_button()
        self.gates_dir = os.path.join(root_dir, "gates")
        self.exports_dir = os.path.join(root_dir, "exports")
        self.strategies_dir = os.path.join(root_dir, "strategies")
        self.panels_dir = os.path.join(root_dir, "panels")
        self.comp_dir = os.path.join(root_dir, "compensation")
        self._load_comp_settings()
        self.comp_mode_var.set(COMP_MODES[self.comp_mode])
        self._update_comp_status()
        self.comp_work = None  # re-initialized from the new experiment's matrix
        self._comp_undo.clear()
        self.fcs_paths = fcs_paths
        self.data_cache.clear()
        self._raw_cache.clear()
        self.existing_gate_sets = load_all_gate_sets(self.gates_dir)
        self.gate_tree = new_gate_tree()
        self.current_node_id = "root"
        self._node_counter = 0
        self.current_strategy_name = None
        self.current_strategy_path = None
        self._strategy_dirty = False
        self.cohort_df = None
        self._clear_panel_result()

        self.experiment_dir_var.set(self.input_dir)
        self.file_listbox.delete(0, "end")
        for p in self.fcs_paths:
            self.file_listbox.insert("end", os.path.relpath(p, self.input_dir))
        self._refresh_existing_listbox()
        self._refresh_strategy_combo()
        self.ov_colors.clear()
        self.ov_linestyles.clear()
        self.ov_order = []
        self._refresh_overlay_file_list(select_paths=set())
        self._select_file_index(0)
        self.status_var.set(
            f"Experiment geladen: {len(self.fcs_paths)} FCS-Datei(en) aus '{root_dir}'. "
            f"Gates: '{self.gates_dir}', Exporte: '{self.exports_dir}', Strategien: '{self.strategies_dir}'."
        )
        return True

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

        self._refresh_panel_channels(channels, fcs.marker_names or {})

        self.ov_channel_a_combo["values"] = channels
        self.ov_channel_b_combo["values"] = channels
        if self.ov_channel_a_var.get() not in channels:
            self.ov_channel_a_var.set(channels[0])
        if self.ov_channel_b_var.get() not in channels:
            self.ov_channel_b_var.set(channels[1] if len(channels) > 1 else channels[0])

        self._refresh_comp_channels(channels)
        self._redraw_visible_tab()
        self._sync_metadata_inspector()

    def _on_file_selected(self, _event):
        sel = self.file_listbox.curselection()
        if len(sel) > 1:
            # Several files marked (Strg/Shift-Klick): keep the marking for
            # 'Markierte entfernen' and leave the displayed file as it is.
            self.status_var.set(f"{len(sel)} Dateien markiert — 'Markierte entfernen' (Entf) blendet sie aus.")
            return
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
