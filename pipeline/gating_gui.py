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
import os
import tkinter as tk
from tkinter import ttk

from gating import load_all_gate_sets, new_gate_tree
from io_utils import FcsData, find_fcs_files
from version import __version__
from gui.constants import COMP_MODES
from gui.gating_tab import GatingTabMixin
from gui.gating_plot import GatingPlotMixin
from gui.gate_tree import GateTreeMixin
from gui.gate_drawing import GateDrawingMixin
from gui.strategies import StrategyMixin
from gui.comp_tab import CompensationTabMixin
from gui.kinetics_tab import KineticsTabMixin
from gui.stats_tab import StatsTabMixin
from gui.overlay_tab import OverlayTabMixin
from gui.panel_tab import PanelTabMixin
from gui.wsp_dialog import WspImportMixin
from gui.derived_dialog import DerivedParamsMixin
from gui.metadata_dialog import MetadataInspectorMixin
from gui.experiment import ExperimentMixin
from gui.data_access import DataAccessMixin
from gui.common import CommonMixin


class GatingApp(
    GatingTabMixin,
    GatingPlotMixin,
    GateTreeMixin,
    GateDrawingMixin,
    StrategyMixin,
    CompensationTabMixin,
    KineticsTabMixin,
    StatsTabMixin,
    OverlayTabMixin,
    PanelTabMixin,
    WspImportMixin,
    DerivedParamsMixin,
    MetadataInspectorMixin,
    ExperimentMixin,
    DataAccessMixin,
    CommonMixin,
    ttk.Frame,
):
    """Main window: file navigator on the left, tabs on the right.

    The tab logic lives in the mixins from the gui/ package; this class only
    holds the shared state (__init__) and builds the window frame."""

    def __init__(self, master, input_dir: str, gates_dir: str):
        super().__init__(master)
        self.input_dir = input_dir
        self.gates_dir = gates_dir
        self.exports_dir = self._derive_sibling_dir(gates_dir, "exports")
        self.strategies_dir = self._derive_sibling_dir(gates_dir, "strategies")
        self.panels_dir = self._derive_sibling_dir(gates_dir, "panels")
        self.comp_dir = self._derive_sibling_dir(gates_dir, "compensation")
        self._load_comp_settings()
        self._load_excluded_files()
        self._load_derived_params()
        try:
            self.fcs_paths = self._filter_excluded(find_fcs_files(input_dir, recursive=True))
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
        self.ov_linestyles: dict[str, str] = {}  # per-file line style for the overlay tab (path -> OVERLAY_LINESTYLES key)
        self.ov_order: list[str] = []  # overlay list/plot order (paths), reordered by dragging curves in the plot
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
        ttk.Button(left, text="FlowJo-Workspace (.wsp) importieren...", command=self._import_wsp).pack(
            fill="x", padx=4, pady=(0, 2)
        )
        ttk.Label(
            left,
            text="(lädt automatisch alle .fcs-Dateien aus diesem Ordner + Unterordnern; neu hinzugefügte Dateien "
            "erscheinen erst nach 'Dateien neu einlesen', nicht von selbst; Gates und Exporte werden hier in "
            "'gates/' bzw. 'exports/' gespeichert)",
            foreground="#555", wraplength=250
        ).pack(anchor="w", padx=4, pady=(0, 8))

        ttk.Label(left, text="Kompensation", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(0, 0))
        self.comp_mode_var = tk.StringVar(value=COMP_MODES[self.comp_mode])
        comp_combo = ttk.Combobox(
            left, textvariable=self.comp_mode_var, state="readonly", values=list(COMP_MODES.values())
        )
        comp_combo.pack(fill="x", padx=4, pady=(2, 0))
        comp_combo.bind("<<ComboboxSelected>>", self._on_comp_mode_selected)
        self.comp_status_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.comp_status_var, foreground="#555", wraplength=250).pack(
            anchor="w", padx=4, pady=(0, 8)
        )
        self._update_comp_status()

        ttk.Label(left, text="Berechnete Kanäle", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(0, 0))
        self.derived_button = ttk.Button(left, command=self._open_derived_dialog)
        self.derived_button.pack(fill="x", padx=4, pady=(2, 0))
        self._update_derived_button()
        ttk.Label(
            left, text="(Formeln pro Zelle, z.B. Ratio oder GP — erscheinen als neue Kanäle in allen Tabs)",
            foreground="#555", wraplength=250
        ).pack(anchor="w", padx=4, pady=(0, 8))

        ttk.Label(left, text="FCS Dateien", font=("", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        # extended: Strg/Shift-Klick marks several files (to hide them at once)
        self.file_listbox = tk.Listbox(left, selectmode="extended", exportselection=False)
        for p in self.fcs_paths:
            self.file_listbox.insert("end", os.path.relpath(p, self.input_dir))
        self.file_listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_selected)
        self.file_listbox.bind("<Delete>", lambda _e: self._exclude_selected_files())
        self.file_listbox.bind("<Double-Button-1>", self._open_metadata_inspector)
        self.master.bind_all("<Control-i>", self._open_metadata_inspector)

        nav = ttk.Frame(left)
        nav.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(nav, text="<< Prev", command=self._prev_file).pack(side="left", expand=True, fill="x")
        ttk.Button(nav, text="Next >>", command=self._next_file).pack(side="left", expand=True, fill="x")
        ttk.Button(
            left, text="Metadaten / Spannungen anzeigen (Strg+I)...", command=self._open_metadata_inspector
        ).pack(fill="x", padx=4, pady=(0, 4))

        excl = ttk.Frame(left)
        excl.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(excl, text="Markierte entfernen (Entf)", command=self._exclude_selected_files).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(excl, text="Ordner ausblenden...", command=self._exclude_folders).pack(
            side="left", expand=True, fill="x"
        )
        self.restore_excluded_button = ttk.Button(left, command=self._restore_excluded_files)
        self.restore_excluded_button.pack(fill="x", padx=4, pady=(0, 4))
        self._update_restore_excluded_button()

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
        comp_tab = ttk.Frame(self.notebook)
        kinetics_tab = ttk.Frame(self.notebook)
        stats_tab = ttk.Frame(self.notebook)
        overlay_tab = ttk.Frame(self.notebook)
        panel_tab = ttk.Frame(self.notebook)
        # tab widget path -> redraw callback for "the visible tab follows the selected file"
        self._tab_redraw = {
            str(comp_tab): self._redraw_comp,
            str(kinetics_tab): self._redraw_kinetics,
            str(stats_tab): self._redraw_stats,
            str(overlay_tab): self._redraw_overlay,
        }
        self.notebook.add(gating_tab, text="Gating")
        self.notebook.add(comp_tab, text="Kompensation")
        self.notebook.add(kinetics_tab, text="Kinetik")
        self.notebook.add(stats_tab, text="Statistik")
        self.notebook.add(overlay_tab, text="Histogramm-Overlay")
        self.notebook.add(panel_tab, text="Panel-Export")

        self._build_gating_tab(gating_tab)
        self._build_comp_tab(comp_tab)
        self._build_kinetics_tab(kinetics_tab)
        self._build_stats_tab(stats_tab)
        self._build_overlay_tab(overlay_tab)
        self._build_panel_tab(panel_tab)
        self._refresh_gate_source_combos()

    def _on_tab_changed(self, _event):
        self._redraw_visible_tab()

    def _redraw_visible_tab(self):
        redraw = self._tab_redraw.get(self.notebook.select())
        if redraw is not None:
            redraw()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", default=os.path.join(os.path.dirname(__file__), "..", "Ca2+_kinetic"))
    parser.add_argument("--gates-dir", default=os.path.join(os.path.dirname(__file__), "..", "gates"))
    args = parser.parse_args()

    root = tk.Tk()
    root.title(f"Ca2+ Kinetik - Gate Editor  (Flow_Plot {__version__})")
    root.geometry("1280x800")
    GatingApp(root, input_dir=args.input_dir, gates_dir=args.gates_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
