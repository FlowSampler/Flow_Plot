"""FlowJo workspace (.wsp) import dialog."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gating import load_all_gate_sets, save_gate_set, save_gate_tree
from compensation import save_matrix
from wsp_import import compare_counts, count_matching, locate_data_folder, plan_import, read_workspace
from gui.constants import COMP_MODES, COMP_MATRIX_FILE


class WspImportMixin:
    """Reads a .wsp file, asks which parts to import and applies them."""

    def _import_wsp(self):
        path = filedialog.askopenfilename(
            title="FlowJo-Workspace importieren", filetypes=[("FlowJo-Workspace", "*.wsp"), ("Alle", "*.*")],
            initialdir=self.input_dir if os.path.isdir(self.input_dir) else os.getcwd(),
        )
        if not path:
            return
        self.config(cursor="watch")
        self.status_var.set("Lese FlowJo-Workspace ...")
        self.update_idletasks()
        try:
            ws = read_workspace(path)
        except Exception as exc:
            self.config(cursor="")
            messagebox.showerror("Import fehlgeschlagen", f"Der Workspace konnte nicht gelesen werden:\n{exc}")
            self.status_var.set("Workspace-Import fehlgeschlagen.")
            return

        # open the experiment folder holding the workspace's .fcs files, unless
        # the current one already has them
        folder_note = ""
        current = {os.path.basename(p) for p in self.fcs_paths}
        if not any(s in current for s in ws.samples):
            self.status_var.set("Suche die .fcs-Dateien des Workspaces ...")
            self.update_idletasks()
            search = locate_data_folder(ws)
            self.config(cursor="")
            folder = search.folder or self._ask_wsp_data_folder(ws, search)
            if not folder:
                self.status_var.set("Workspace-Import abgebrochen.")
                return
            if not self._confirm_discard_current_strategy() or not self._load_experiment(folder):
                self.status_var.set("Workspace-Import abgebrochen.")
                return
            how = search.how if search.folder else "manuell gewählt"
            folder_note = f"Experiment-Ordner automatisch geöffnet ({how}): {folder}"
            self.config(cursor="watch")

        try:
            plan = plan_import(ws, [os.path.basename(p) for p in self.fcs_paths])
        except Exception as exc:
            messagebox.showerror("Import fehlgeschlagen", f"Der Workspace konnte nicht gelesen werden:\n{exc}")
            self.status_var.set("Workspace-Import fehlgeschlagen.")
            return
        finally:
            self.config(cursor="")
        self._show_wsp_import_dialog(ws, plan, folder_note)

    def _ask_wsp_data_folder(self, ws, search) -> str | None:
        """The .fcs files were neither at the stored path nor next to the .wsp:
        explain why and let the user pick the folder. Returns None on cancel."""
        win = tk.Toplevel(self)
        win.title(".fcs-Dateien nicht gefunden")
        win.transient(self.winfo_toplevel())
        win.grab_set()

        n = len(ws.samples)
        stored = search.stored_folder
        if stored and not os.path.isdir(stored):
            reason = (f"Der im Workspace hinterlegte Ordner existiert auf diesem Rechner nicht:\n    {stored}\n"
                      "(z. B. Workspace von einem anderen PC, Daten verschoben/umbenannt oder Netzlaufwerk "
                      "nicht verbunden.)")
        elif stored:
            reason = (f"Der im Workspace hinterlegte Ordner existiert, enthält aber keine der Proben:\n    {stored}\n"
                      "(z. B. Dateien verschoben oder umbenannt.)")
        else:
            reason = "Der Workspace enthält keinen Speicherort für die .fcs-Dateien."
        text = (
            f"Die .fcs-Dateien zum Workspace '{os.path.basename(ws.path)}' ({n} Probe(n)) wurden nicht gefunden.\n\n"
            f"{reason}\n\n"
            f"Auch im Ordner der .wsp-Datei liegen keine passenden Dateien:\n"
            f"    {os.path.dirname(os.path.abspath(ws.path))}\n\n"
            "Bitte den Ordner mit den .fcs-Dateien manuell wählen (Unterordner werden mit durchsucht). "
            "Zugeordnet wird über den Dateinamen — die Dateien dürfen seit dem FlowJo-Export nicht umbenannt "
            "worden sein."
        )
        ttk.Label(win, text=text, justify="left", wraplength=620).pack(anchor="w", padx=12, pady=(12, 6))
        error_var = tk.StringVar()
        ttk.Label(win, textvariable=error_var, foreground="#a33", justify="left", wraplength=620).pack(
            anchor="w", padx=12
        )
        result = {"folder": None}

        def choose():
            chosen = filedialog.askdirectory(
                parent=win, title="Ordner mit den .fcs-Dateien des Workspaces wählen",
                initialdir=self.input_dir if os.path.isdir(self.input_dir or "") else os.getcwd(),
            )
            if not chosen:
                return
            found = count_matching(ws, chosen)
            if not found:
                error_var.set(f"Im gewählten Ordner '{chosen}' liegt keine der {n} Proben des Workspaces. "
                              "Bitte einen anderen Ordner wählen.")
                return
            result["folder"] = chosen
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(anchor="e", padx=12, pady=12)
        ttk.Button(btns, text="Ordner wählen...", command=choose).pack(side="left", padx=4)
        ttk.Button(btns, text="Abbrechen", command=win.destroy).pack(side="left")
        win.wait_window()
        return result["folder"]

    def _show_wsp_import_dialog(self, ws, plan, folder_note: str = ""):
        win = tk.Toplevel(self)
        win.title("FlowJo-Workspace importieren")
        win.transient(self.winfo_toplevel())
        win.grab_set()

        new_names = {gs.name for gs, _ in plan.gate_sets}
        replaced = sorted({gs.name for gs in self.existing_gate_sets if gs.name in new_names})
        n_pops = len(plan.names)
        scope_txt = ", ".join(f"{n}× {k}" for k, n in plan.scope_summary.items())
        lines = [
            f"Workspace: {os.path.basename(ws.path)}",
            f"Proben: {len(ws.samples)} — davon {len(plan.matched)} mit passender .fcs-Datei in diesem Experiment"
            + (f" (ohne Datei: {', '.join(plan.unmatched[:5])}{' …' if len(plan.unmatched) > 5 else ''})"
               if plan.unmatched else ""),
            f"Populationen: {n_pops}  →  {len(plan.gate_sets)} Gate-Set-Datei(en) ({scope_txt})",
            f"Gating-Strategie(n): {', '.join(n for n, _ in plan.strategies)}",
            f"Kompensation: {ws.comp_note}",
        ]
        if folder_note:
            lines.insert(1, folder_note)
        if not plan.matched:
            lines.append("ACHTUNG: keine Probe passt zu einer .fcs-Datei im aktuellen Experiment-Ordner.")
        ttk.Label(win, text="\n".join(lines), justify="left", wraplength=640).pack(anchor="w", padx=10, pady=(10, 6))

        do_gates = tk.BooleanVar(value=True)
        do_strategy = tk.BooleanVar(value=True)
        do_comp = tk.BooleanVar(value=ws.comp is not None)
        ttk.Checkbutton(
            win, text="Gate-Sets übernehmen (in Statistik / Kinetik / Panel-Export als Populationen wählbar)",
            variable=do_gates,
        ).pack(anchor="w", padx=10)
        if replaced:
            ttk.Label(
                win, text=f"   ersetzt {len(replaced)} vorhandene Gate-Set(s) gleichen Namens: "
                + ", ".join(replaced[:8]) + (" …" if len(replaced) > 8 else ""),
                foreground="#a33", wraplength=620, justify="left",
            ).pack(anchor="w", padx=10)
        ttk.Checkbutton(
            win, text="Gate-Hierarchie als Gating-Strategie speichern und im Gating-Tab öffnen", variable=do_strategy
        ).pack(anchor="w", padx=10)
        comp_cb = ttk.Checkbutton(
            win, text="Kompensationsmatrix als Experiment-Matrix übernehmen und aktivieren (nötig, damit die "
            "Gates zu den Daten passen)", variable=do_comp,
        )
        comp_cb.pack(anchor="w", padx=10)
        if ws.comp is None:
            comp_cb.state(["disabled"])
        if ws.warnings:
            ttk.Label(
                win, text="Hinweise:\n" + "\n".join(ws.warnings[:10])
                + (f"\n… und {len(ws.warnings) - 10} weitere" if len(ws.warnings) > 10 else ""),
                foreground="#a33", justify="left", wraplength=640,
            ).pack(anchor="w", padx=10, pady=(6, 0))
        ttk.Label(
            win, text="Gate-Kanten werden wie in FlowJo auf der Biex-/Logicle-Skala gerade gezogen. Kleine Abweichungen "
            "zu FlowJos Zählungen (meist < 0,5 %) entstehen, weil FlowJos Biex-Berechnung nicht offengelegt ist.",
            foreground="#555", wraplength=640, justify="left",
        ).pack(anchor="w", padx=10, pady=(6, 0))

        def run():
            win.destroy()
            self._apply_wsp_import(ws, plan, do_gates.get(), do_strategy.get(), do_comp.get(), replaced)

        btns = ttk.Frame(win)
        btns.pack(anchor="e", padx=10, pady=10)
        ttk.Button(btns, text="Importieren", command=run).pack(side="left", padx=4)
        ttk.Button(btns, text="Abbrechen", command=win.destroy).pack(side="left")

    def _apply_wsp_import(self, ws, plan, do_gates: bool, do_strategy: bool, do_comp: bool, replaced: list[str]):
        done = []
        try:
            if do_comp and ws.comp is not None:
                matrix = ws.comp.copy()
                save_matrix(matrix, os.path.join(self.comp_dir, COMP_MATRIX_FILE))
                self.experiment_comp = matrix
                self.comp_work = None
                self.comp_mode = "experiment"
                self.comp_mode_var.set(COMP_MODES["experiment"])
                self._save_comp_settings()
                done.append(f"Kompensation '{matrix.name}' als Experiment-Matrix aktiv")
            if do_gates:
                for gs in self.existing_gate_sets:
                    if gs.name in replaced and gs.source_path and os.path.exists(gs.source_path):
                        os.remove(gs.source_path)
                for gs, stem in plan.gate_sets:
                    save_gate_set(gs, self.gates_dir, file_stem=stem)
                self.existing_gate_sets = load_all_gate_sets(self.gates_dir)
                self._refresh_existing_listbox()
                # the preview ANDs every saved gate-set — with mutually exclusive imported
                # populations (e.g. 'Comp-PE-A+' / 'Comp-PE-A-') that would be empty
                self.show_existing_var.set(False)
                done.append(f"{len(plan.gate_sets)} Gate-Set-Datei(en) in gates/")
            strategy_paths = []
            if do_strategy:
                for name, tree in plan.strategies:
                    strategy_paths.append((save_gate_tree(tree, name, self.strategies_dir), name))
                self._refresh_strategy_combo()
                done.append(f"{len(strategy_paths)} Gating-Strategie(n)")
        except OSError as exc:
            messagebox.showerror("Import unvollständig", f"Schreiben fehlgeschlagen:\n{exc}")
            return

        self._invalidate_compensated_data()
        if strategy_paths and self._confirm_discard_current_strategy():
            path, name = strategy_paths[0]
            self._load_strategy(path, name)
            self.strategy_var.set(name)

        report = self._wsp_count_report(ws)
        messagebox.showinfo("Import abgeschlossen", "Übernommen:\n– " + "\n– ".join(done or ["nichts"]) + report)
        self.status_var.set("FlowJo-Workspace importiert: " + "; ".join(done))

    def _wsp_count_report(self, ws) -> str:
        """Plausibility check on the currently shown file: our counts vs FlowJo's."""
        if not self.current_path:
            return ""
        name = os.path.basename(self.current_path)
        sample = ws.samples.get(name) or next(iter(ws.samples.values()), None)
        if sample is None or sample.name not in [os.path.basename(p) for p in self.fcs_paths]:
            return ""
        path = next(p for p in self.fcs_paths if os.path.basename(p) == sample.name)
        try:
            table = compare_counts(sample, self._get_data(path).df)
        except Exception as exc:
            return f"\n\nAbgleich mit FlowJo nicht möglich: {exc}"
        dev = table.dropna(subset=["Abweichung %"])
        dev = dev[dev["FlowJo"] >= 100]
        if dev.empty:
            return ""
        worst = dev.loc[dev["Abweichung %"].abs().idxmax()]
        return (
            f"\n\nAbgleich mit FlowJos Zählungen für '{sample.name}' ({len(dev)} Populationen ≥ 100 Events):\n"
            f"mittlere Abweichung {dev['Abweichung %'].abs().mean():.2f} %, größte {worst['Abweichung %']:+.2f} % "
            f"({worst['Population']}: FlowJo {int(worst['FlowJo'])}, hier {int(worst['Dieses Tool'])})"
        )
