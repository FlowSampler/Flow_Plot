"""Panel export: many statistics for many populations/markers, one row per file.

A *panel* is a list of PanelEntry rows. Each row names
  - a population   (a saved gate-set name, or POP_ALL for ungated events)
  - an optional marker channel
  - which statistics to compute
  - the reference population for "% of reference" (default: the parent gate,
    i.e. the gate-set's path without its last step — so the drawn gate itself
    acts as the positive/negative threshold)
  - an optional fixed threshold on the marker (extra "% > threshold" and the
    MFI of only the cells above it)

The marker statistics are always computed on exactly the chosen population:
pick "CD4+" to get the MFI of every CD4+ cell, pick "CD4+CD107a+" to get the
MFI of only the CD107a+ cells within CD4+.

compute_panel() returns a wide DataFrame (one row per file, one column per
population/marker/statistic) plus a long info table on which gate variants
(all/group/file scope) were used. write_panel_xlsx() writes both into an
Excel workbook with a per-column color-scale heatmap.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from gating import GateSet, resolve_gate_sets_for_file

POP_ALL = "Ungegated (alle Events)"
REF_PARENT = "Eltern-Gate (automatisch)"
REF_ALL = "Alle Events"

# key -> column label. Population-level statistics do not depend on a marker.
POP_STATS = {
    "n": "Anzahl",
    "pct_ref": "% Bezug",
    "pct_total": "% aller Events",
}
MARKER_STATS = {
    "median": "MFI Median",
    "mean": "Mean",
    "geomean": "geom. Mean",
    "cv": "rCV %",
}
STAT_LABELS = {**POP_STATS, **MARKER_STATS}
DEFAULT_STATS = ["n", "pct_ref", "median"]


@dataclass
class PanelEntry:
    population: str
    marker: str | None = None
    reference: str = REF_PARENT
    stats: list[str] = field(default_factory=lambda: list(DEFAULT_STATS))
    threshold: float | None = None

    def stats_text(self) -> str:
        return ", ".join(STAT_LABELS[s] for s in self.stats if s in STAT_LABELS)


# ------------------------------------------------------------------ templates
def save_panel(entries: list[PanelEntry], path: str, name: str = "") -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {"name": name or os.path.splitext(os.path.basename(path))[0],
               "entries": [asdict(e) for e in entries]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def load_panel(path: str) -> list[PanelEntry]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    entries = []
    for e in raw.get("entries", []):
        entries.append(PanelEntry(
            population=e["population"],
            marker=e.get("marker"),
            reference=e.get("reference", REF_PARENT),
            stats=[s for s in e.get("stats", DEFAULT_STATS) if s in STAT_LABELS],
            threshold=e.get("threshold"),
        ))
    return entries


# ------------------------------------------------------------------ statistics
def marker_stat(values: np.ndarray, key: str) -> float:
    """MFI-type statistic on raw per-event intensities.

    - median:  median fluorescence intensity (robust, the usual "MFI")
    - mean:    arithmetic mean (sensitive to outliers / bright tails)
    - geomean: geometric mean exp(mean(log x)), computed on values > 0 only
               (log is undefined for 0/negative events, which occur after
               baseline subtraction/compensation)
    - cv:      robust CV in % = 100 * 0.75 * IQR / median (FlowJo-style rCV)
    """
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return float("nan")
    if key == "median":
        return float(np.median(values))
    if key == "mean":
        return float(np.mean(values))
    if key == "geomean":
        pos = values[values > 0]
        return float(np.exp(np.mean(np.log(pos)))) if len(pos) else float("nan")
    if key == "cv":
        med = np.median(values)
        if med == 0:
            return float("nan")
        q1, q3 = np.percentile(values, [25, 75])
        return float(100 * 0.75 * (q3 - q1) / abs(med))
    raise ValueError(f"Unknown marker statistic {key!r}")


def _pct(num: int, den: int) -> float:
    return 100.0 * num / den if den else float("nan")


class _FileMasks:
    """Per-file cache of population / parent masks."""

    def __init__(self, df: pd.DataFrame, filename: str, gate_sets: list[GateSet]):
        self.df = df
        self.filename = filename
        self.gate_sets = gate_sets
        self._pop: dict[str, tuple[np.ndarray | None, list[GateSet]]] = {}
        self._parent: dict[str, np.ndarray | None] = {}

    def variants(self, name: str) -> list[GateSet]:
        return resolve_gate_sets_for_file(self.filename, [gs for gs in self.gate_sets if gs.name == name])

    def population(self, name: str) -> tuple[np.ndarray | None, list[GateSet]]:
        """(mask, used gate variants); mask is None when no variant of that
        gate-set applies to this file (instead of silently using all events)."""
        if name not in self._pop:
            if name in (POP_ALL, REF_ALL):
                self._pop[name] = (np.ones(len(self.df), dtype=bool), [])
            else:
                variants = self.variants(name)
                if not variants:
                    self._pop[name] = (None, [])
                else:
                    mask = np.ones(len(self.df), dtype=bool)
                    for gs in variants:
                        mask &= gs.mask(self.df)
                    self._pop[name] = (mask, variants)
        return self._pop[name]

    def parent(self, name: str) -> np.ndarray | None:
        """The population one gate step up: every matching variant's steps
        except the last one (a single-step gate-set's parent is all events)."""
        if name not in self._parent:
            if name in (POP_ALL, REF_ALL):
                self._parent[name] = np.ones(len(self.df), dtype=bool)
            else:
                variants = self.variants(name)
                if not variants:
                    self._parent[name] = None
                else:
                    mask = np.ones(len(self.df), dtype=bool)
                    for gs in variants:
                        for step in gs.steps[:-1]:
                            mask &= step.mask(self.df)
                    self._parent[name] = mask
        return self._parent[name]

    def reference(self, entry: PanelEntry) -> np.ndarray | None:
        if entry.reference == REF_PARENT:
            return self.parent(entry.population)
        return self.population(entry.reference)[0]


def _ref_label(entry: PanelEntry) -> str:
    if entry.reference == REF_PARENT:
        return "% Eltern"
    if entry.reference in (REF_ALL, POP_ALL):
        return "% aller Events"
    return f"% von {entry.reference}"


def _fmt_thr(thr: float) -> str:
    return f"{thr:g}"


def entry_columns(entry: PanelEntry, marker_label: str | None = None) -> list[tuple[str, str]]:
    """(column key, column header) pairs this entry produces, in order. The
    key de-duplicates population-level columns shared by several entries."""
    pop = entry.population
    cols = []
    for s in entry.stats:
        if s == "n":
            cols.append((f"{pop}|n", f"{pop} | Anzahl"))
        elif s == "pct_ref":
            cols.append((f"{pop}|ref|{entry.reference}", f"{pop} | {_ref_label(entry)}"))
        elif s == "pct_total":
            cols.append((f"{pop}|ref|{REF_ALL}", f"{pop} | % aller Events"))
    if entry.marker:
        m = marker_label or entry.marker
        for s in entry.stats:
            if s in MARKER_STATS:
                cols.append((f"{pop}|{entry.marker}|{s}", f"{pop} | {m} | {MARKER_STATS[s]}"))
        if entry.threshold is not None:
            t = _fmt_thr(entry.threshold)
            cols.append((f"{pop}|{entry.marker}|>{t}|pct", f"{pop} | {m} > {t} | % positiv"))
            for s in entry.stats:
                if s in MARKER_STATS:
                    cols.append((f"{pop}|{entry.marker}|>{t}|{s}", f"{pop} | {m} > {t} | {MARKER_STATS[s]}"))
    return cols


def compute_panel(
    files: list[tuple[str, pd.DataFrame]],
    gate_sets: list[GateSet],
    entries: list[PanelEntry],
    marker_labels: dict[str, str] | None = None,
    progress=None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Returns (wide table, gate-variant info table, warnings).

    files: (filename, raw event DataFrame) pairs — may be a generator so data
    is loaded lazily. marker_labels: channel -> display name (e.g. 'CD107a').
    progress: optional callback(i, filename)."""
    marker_labels = marker_labels or {}
    headers: dict[str, str] = {}
    for e in entries:
        for key, header in entry_columns(e, marker_labels.get(e.marker or "")):
            headers.setdefault(key, header)

    rows, info_rows, warnings = [], [], []
    for i, (filename, df) in enumerate(files):
        if progress:
            progress(i, filename)
        fm = _FileMasks(df, filename, gate_sets)
        values: dict[str, float] = {}
        seen_pops = set()
        for e in entries:
            pop_mask, variants = fm.population(e.population)
            if e.population not in seen_pops:
                seen_pops.add(e.population)
                info_rows.append({
                    "Datei": filename,
                    "Population": e.population,
                    "Gate-Varianten": "; ".join(_scope_text(gs) for gs in variants)
                    if variants else ("–" if e.population == POP_ALL else "KEIN passendes Gate"),
                    "Ereignisse": int(pop_mask.sum()) if pop_mask is not None else None,
                })
            if pop_mask is None:
                warnings.append(f"{filename}: kein Gate-Set '{e.population}' für diese Datei")
                continue
            n_pop = int(pop_mask.sum())
            for s in e.stats:
                if s == "n":
                    values[f"{e.population}|n"] = n_pop
                elif s in ("pct_ref", "pct_total"):
                    ref = REF_ALL if s == "pct_total" else e.reference
                    ref_mask = fm.reference(PanelEntry(e.population, reference=ref))
                    if ref_mask is None:
                        warnings.append(f"{filename}: Bezug '{ref}' nicht verfügbar")
                        continue
                    values[f"{e.population}|ref|{ref}"] = _pct(int((pop_mask & ref_mask).sum()), int(ref_mask.sum()))
            if not e.marker:
                continue
            if e.marker not in df.columns:
                warnings.append(f"{filename}: Kanal '{e.marker}' fehlt")
                continue
            x = df[e.marker].to_numpy(dtype=float)[pop_mask]
            for s in e.stats:
                if s in MARKER_STATS:
                    values[f"{e.population}|{e.marker}|{s}"] = marker_stat(x, s)
            if e.threshold is not None:
                t = _fmt_thr(e.threshold)
                pos = x[x > e.threshold]
                values[f"{e.population}|{e.marker}|>{t}|pct"] = _pct(len(pos), len(x))
                for s in e.stats:
                    if s in MARKER_STATS:
                        values[f"{e.population}|{e.marker}|>{t}|{s}"] = marker_stat(pos, s)
        row = {"Datei": filename}
        for key, header in headers.items():
            row[header] = values.get(key, np.nan)
        rows.append(row)

    wide = pd.DataFrame(rows, columns=["Datei"] + list(headers.values()))
    info = pd.DataFrame(info_rows, columns=["Datei", "Population", "Gate-Varianten", "Ereignisse"])
    # identical warnings repeat per file only once each
    return wide, info, list(dict.fromkeys(warnings))


def _scope_text(gs: GateSet) -> str:
    t = gs.scope.get("type")
    if t == "group":
        return f"group '{gs.scope.get('pattern', '')}'"
    if t == "file":
        return "file"
    return "all"


def panel_definition_table(entries: list[PanelEntry], marker_labels: dict[str, str] | None = None) -> pd.DataFrame:
    marker_labels = marker_labels or {}
    return pd.DataFrame([{
        "Population": e.population,
        "Marker": (marker_labels.get(e.marker) or e.marker) if e.marker else "",
        "Kanal": e.marker or "",
        "Bezug für %": e.reference,
        "Schwelle": e.threshold if e.threshold is not None else "",
        "Statistiken": e.stats_text(),
    } for e in entries])


# ------------------------------------------------------------------ Excel output
def write_panel_xlsx(
    path: str,
    wide: pd.DataFrame,
    info: pd.DataFrame,
    definition: pd.DataFrame,
    heatmap: bool = True,
) -> None:
    """Sheet 'Daten' = wide table (one row per file) with a per-column
    white->blue color scale, 'Panel' = panel definition, 'Gates' = which gate
    variants were used for each file/population."""
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        wide.to_excel(writer, sheet_name="Daten", index=False)
        definition.to_excel(writer, sheet_name="Panel", index=False)
        info.to_excel(writer, sheet_name="Gates", index=False)

        ws = writer.sheets["Daten"]
        n_rows = len(wide) + 1
        ws.freeze_panes = "B2"
        ws.row_dimensions[1].height = 60
        for col_idx, col in enumerate(wide.columns, start=1):
            letter = get_column_letter(col_idx)
            cell = ws.cell(row=1, column=col_idx)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_idx == 1:
                width = min(60, max(12, int(wide[col].astype(str).str.len().max() or 10) + 2))
                ws.column_dimensions[letter].width = width
                continue
            ws.column_dimensions[letter].width = 16
            fmt = "0" if col.endswith("Anzahl") else ("0.00" if "%" in col.split(" | ")[-1] else "#,##0.0")
            for r in range(2, n_rows + 1):
                ws.cell(row=r, column=col_idx).number_format = fmt
            if heatmap and len(wide) > 1:
                ws.conditional_formatting.add(
                    f"{letter}2:{letter}{n_rows}",
                    ColorScaleRule(start_type="min", start_color="FFFFFF",
                                   mid_type="percentile", mid_value=50, mid_color="9DC3E6",
                                   end_type="max", end_color="2F75B5"),
                )
        for name, df in (("Panel", definition), ("Gates", info)):
            sheet = writer.sheets[name]
            for col_idx, col in enumerate(df.columns, start=1):
                sheet.cell(row=1, column=col_idx).font = Font(bold=True)
                longest = max([len(str(col))] + [len(str(v)) for v in df[col].tolist()])
                sheet.column_dimensions[get_column_letter(col_idx)].width = min(70, longest + 2)
