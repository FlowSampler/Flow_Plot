"""Data access: loading/caching FCS files, active compensation, population masks."""
from __future__ import annotations

import dataclasses
import json
import os
import tkinter as tk
from tkinter import messagebox

import numpy as np

from gating import apply_named_gate_set, list_gate_set_names, node_mask, resolve_gate_sets_for_file
from compensation import CompMatrix, compensate, load_matrix
from derived import apply_derived
from io_utils import FcsData, load_fcs
from gui.constants import GATE_SOURCE_LIVE, GATE_SOURCE_NONE, COMP_MODES, COMP_MATRIX_FILE, COMP_SETTINGS_FILE


class DataAccessMixin:
    """Loads FCS data (raw + compensated), manages the active compensation mode and computes the event masks of the selected populations."""

    def _population_mask(self, df, filename: str, source: str) -> tuple[np.ndarray, str]:
        """Resolve the 'Population:' selector to a mask. Returns (mask, description)."""
        if source == GATE_SOURCE_LIVE:
            return self._cumulative_pending_mask(df), self.gate_tree[self.current_node_id].name
        if source == GATE_SOURCE_NONE:
            return np.ones(len(df), dtype=bool), "ungegated"
        mask, applied = apply_named_gate_set(df, filename, self.existing_gate_sets, source)
        return mask, (applied[0] if applied else f"{source} (kein Treffer für diese Datei)")

    def _get_data(self, path: str):
        """The file's events with the ACTIVE compensation applied — every tab
        (gating, kinetics, statistics, overlay, panel export) reads through
        here, so switching the compensation affects all of them."""
        if path not in self.data_cache:
            raw = self._get_raw_data(path)
            matrix = self._comp_matrix_for(raw)
            if matrix is None:
                data = raw
            else:
                data = dataclasses.replace(raw, df=compensate(raw.df, matrix), compensation=matrix.name or "Matrix")
            # Derived parameters (formulas) are computed from the COMPENSATED
            # values and appended as extra columns, so every tab sees them
            # like ordinary channels.
            params = getattr(self, "derived_params", [])
            if params:
                df, errors = apply_derived(data.df, params, data.marker_names)
                data = dataclasses.replace(data, df=df)
                if errors and path == self.current_path and hasattr(self, "status_var"):
                    self.status_var.set(
                        f"{data.filename}: abgeleitete(r) Parameter nicht berechnet — "
                        + "; ".join(f"{n}: {msg}" for n, msg in errors.items())
                    )
            self.data_cache[path] = data
        return self.data_cache[path]

    def _get_raw_data(self, path: str) -> FcsData:
        """Uncompensated data. Only the last few files are kept (the
        compensated copies in data_cache are what the tabs use); with
        compensation off and no derived parameters both caches share the
        same object anyway."""
        if path in self.data_cache and self.data_cache[path].compensation == "keine" \
                and not getattr(self, "derived_params", []):
            return self.data_cache[path]
        if path not in self._raw_cache:
            if len(self._raw_cache) >= 3:
                self._raw_cache.pop(next(iter(self._raw_cache)))
            self._raw_cache[path] = load_fcs(path)
        return self._raw_cache[path]

    def _load_comp_settings(self):
        """Experiment matrix + chosen mode from '<experiment>/compensation/'.
        Default: the experiment matrix if one was saved, else the file's own."""
        self._raw_cache: dict[str, FcsData] = getattr(self, "_raw_cache", {})
        self.experiment_comp: CompMatrix | None = None
        matrix_path = os.path.join(self.comp_dir, COMP_MATRIX_FILE)
        if os.path.isfile(matrix_path):
            try:
                self.experiment_comp = load_matrix(matrix_path)
            except (OSError, ValueError, KeyError) as exc:
                print(f"Experiment-Matrix konnte nicht gelesen werden: {exc}")
        mode = "experiment" if self.experiment_comp is not None else "file"
        try:
            with open(os.path.join(self.comp_dir, COMP_SETTINGS_FILE), "r", encoding="utf-8") as fh:
                saved = json.load(fh).get("mode")
            if saved in COMP_MODES and (saved != "experiment" or self.experiment_comp is not None):
                mode = saved
        except (OSError, ValueError):
            pass
        self.comp_mode = mode

    def _save_comp_settings(self):
        try:
            os.makedirs(self.comp_dir, exist_ok=True)
            with open(os.path.join(self.comp_dir, COMP_SETTINGS_FILE), "w", encoding="utf-8") as fh:
                json.dump({"mode": self.comp_mode}, fh)
        except OSError:
            pass  # read-only experiment folder: the mode just isn't remembered

    def _comp_matrix_for(self, fcs: FcsData) -> CompMatrix | None:
        if self.comp_mode == "file":
            return fcs.spill
        if self.comp_mode == "experiment":
            return self.experiment_comp
        return None

    def _on_comp_mode_selected(self, _event=None):
        label = self.comp_mode_var.get()
        mode = next(k for k, v in COMP_MODES.items() if v == label)
        if mode == "experiment" and self.experiment_comp is None:
            messagebox.showinfo(
                "Keine Experiment-Matrix",
                "Für dieses Experiment wurde noch keine Matrix gespeichert. Im Tab 'Kompensation' anpassen und "
                "'Speichern & anwenden' klicken."
            )
            self.comp_mode_var.set(COMP_MODES[self.comp_mode])
            return
        self._set_comp_mode(mode)

    def _set_comp_mode(self, mode: str):
        self.comp_mode = mode
        self.comp_mode_var.set(COMP_MODES[mode])
        self._save_comp_settings()
        self._invalidate_compensated_data()

    def _invalidate_compensated_data(self):
        """Drop every compensated copy and redraw the visible tab."""
        self.data_cache.clear()
        self.cohort_df = None
        self._clear_panel_result()
        self._update_comp_status()
        if self.current_path:
            self._select_file_index(self.fcs_paths.index(self.current_path))

    def _update_comp_status(self):
        if not hasattr(self, "comp_status_var"):
            return
        if self.comp_mode == "experiment" and self.experiment_comp is not None:
            text = f"{len(self.experiment_comp.channels)} Kanäle, gespeichert in compensation/{COMP_MATRIX_FILE}"
        elif self.comp_mode == "file":
            text = "wie in FACSDiva/FlowJo angezeigt"
        else:
            text = "Achtung: Werte ohne Kompensation"
        self.comp_status_var.set(text)

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
        if hasattr(self, "panel_pop_combo"):
            self._refresh_panel_gate_combos()

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
        skipped = []
        for gs in applicable:
            missing = gs.missing_channels(df.columns)
            if missing:
                # e.g. a group gate whose pattern accidentally matches a file of another
                # panel — skip it in the preview instead of crashing on a KeyError
                skipped.append(f"{gs.name} (fehlt: {', '.join(missing)})")
                continue
            mask &= gs.mask(df)
        if skipped:
            self.status_var.set(
                f"{filename}: Gate-Set(s) übersprungen, Kanal nicht in der Datei: {'; '.join(skipped)}"
            )
        return mask

    def _cumulative_pending_mask(self, df) -> np.ndarray:
        """Mask for the currently viewed node: saved gate-sets (preview) ANDed
        with every step from the root down to the current node in the tree."""
        return node_mask(df, self.gate_tree, self.current_node_id, base_mask=self._base_mask(df))

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
