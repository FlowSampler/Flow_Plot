"""Import a FlowJo workspace (.wsp): gates, gate hierarchy and compensation.

FlowKit parses the workspace (FlowJo's Gating-ML flavour). This module turns
its per-sample gating strategies into this tool's model:

  - every population becomes a GateSet (root -> population path of GateSteps)
    with scope "all" if all samples share the identical gate definition,
    "group" if a FlowJo group's samples share it (and the group name appears
    in their file names), otherwise one "file"-scoped variant per sample;
  - every distinct gate tree becomes a gating strategy (whole editable tree);
  - the compensation matrix FlowJo used for the samples becomes the
    experiment matrix.

Geometry: FlowJo stores gate coordinates on the compensated, UNtransformed
scale, but draws (and gates) polygon edges as straight lines in the
transformed (e.g. biex) display. FlowKit converts the vertices into that
transformed space; here each polygon edge is densified there and mapped back,
so the curved edge on the raw scale is reproduced. Vertices on the edge of a
transform's display range (where FlowJo piles up off-scale events) are pushed
out to +/- infinity so those events stay inside, as in FlowJo. Rectangles are
exact (axis-aligned), 1D rectangles become "range" gates, ellipses arrive as
polygons (FlowKit converts them), Boolean gates become "boolean" steps and
"events outside" gates a negated boolean.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from compensation import CompMatrix
from gating import GateNode, GateSet, GateStep, _step_to_dict, make_boolean_step, safe_file_stem

BIG = 1e12
EDGE_POINTS = 24  # points per polygon edge when the axes are non-linear

Path = tuple  # population path from root, without 'root', e.g. ('singlets', 'live')


@dataclass
class WspSample:
    name: str
    populations: dict = field(default_factory=dict)  # Path -> list[GateStep] (root .. population), parents first
    flowjo_counts: dict = field(default_factory=dict)  # Path -> int (event count FlowJo stored)


@dataclass
class WspWorkspace:
    path: str
    samples: dict  # sample name (FCS file name) -> WspSample
    groups: dict  # FlowJo group name -> [sample names]
    comp: CompMatrix | None
    comp_note: str
    warnings: list = field(default_factory=list)


# ------------------------------------------------------------------ geometry
class _Axis:
    def __init__(self, xform):
        self.xform = xform
        self.nonlinear = xform is not None and type(xform).__name__ != "LinearTransform"
        if self.nonlinear:
            lo, hi = xform.apply(np.array([[-BIG], [BIG]])).ravel()
            self.lo, self.hi = float(lo), float(hi)
            self.eps = 1e-6 * max(abs(self.hi - self.lo), 1.0)

    def inverse(self, values) -> np.ndarray:
        v = np.asarray(values, dtype=float).ravel()
        if self.xform is None:
            return v
        out = np.asarray(self.xform.inverse(v.reshape(-1, 1)), dtype=float).ravel()
        if self.nonlinear:
            out[v <= self.lo + self.eps] = -BIG
            out[v >= self.hi - self.eps] = BIG
        return out

    def inverse_bound(self, value):
        return None if value is None else float(self.inverse([float(value)])[0])


def _axes(gate, strategy) -> list[_Axis]:
    axes = []
    for dim in gate.dimensions:
        ref = getattr(dim, "transformation_ref", None)
        xform = None
        if ref:
            try:
                xform = strategy.get_transform(ref)
            except Exception:
                xform = None
        axes.append(_Axis(xform))
    return axes


def _clean(v: float) -> float:
    return float(f"{float(v):.10g}")


def _convert_geometric(gate, strategy) -> GateStep:
    kind = type(gate).__name__
    dims = [d.id for d in gate.dimensions]
    axes = _axes(gate, strategy)
    if kind == "PolygonGate":
        if len(dims) != 2:
            raise ValueError(f"Polygon mit {len(dims)} Dimensionen")
        verts = np.array(gate.vertices, dtype=float)
        if axes[0].nonlinear or axes[1].nonlinear:
            closed = np.vstack([verts, verts[:1]])
            pts = []
            for a, b in zip(closed[:-1], closed[1:]):
                t = np.linspace(0, 1, EDGE_POINTS, endpoint=False)[:, None]
                pts.append(a + (b - a) * t)
            verts = np.vstack(pts)
        raw = np.column_stack([axes[0].inverse(verts[:, 0]), axes[1].inverse(verts[:, 1])])
        step = GateStep(dims[0], dims[1], "polygon", vertices=[[_clean(x), _clean(y)] for x, y in raw])
    elif kind == "RectangleGate":
        bounds = [(axes[i].inverse_bound(d.min), axes[i].inverse_bound(d.max)) for i, d in enumerate(gate.dimensions)]
        if len(dims) == 1:
            lo, hi = bounds[0]
            step = GateStep(dims[0], "", "range", params={
                "min": None if lo is None or lo <= -BIG else _clean(lo),
                "max": None if hi is None or hi >= BIG else _clean(hi),
            })
        elif len(dims) == 2:
            (x0, x1), (y0, y1) = [(-BIG if lo is None else lo, BIG if hi is None else hi) for lo, hi in bounds]
            step = GateStep(dims[0], dims[1], "rectangle",
                            vertices=[[_clean(x0), _clean(y0)], [_clean(x1), _clean(y0)],
                                      [_clean(x1), _clean(y1)], [_clean(x0), _clean(y1)]])
        else:
            raise ValueError(f"Rechteck mit {len(dims)} Dimensionen")
    else:
        raise ValueError(f"Gate-Typ {kind} wird nicht unterstützt")
    if getattr(gate, "use_complement", False):
        step = make_boolean_step("and", [(f"außerhalb {gate.gate_name}", [step], True)])
    return step


# ------------------------------------------------------------------ reading
def _flowjo_counts(wsp_path: str) -> dict:
    """sample name -> {population path: count} from the counts FlowJo saved."""
    from lxml import etree

    out = {}
    root = etree.parse(wsp_path).getroot()
    pop_tags = {"Population", "AndNode", "OrNode", "NotNode"}

    def walk(el, prefix, counts):
        subs = el.find("Subpopulations")
        if subs is None:
            return
        for child in subs:
            tag = etree.QName(child).localname if isinstance(child.tag, str) else ""
            if tag not in pop_tags:
                continue
            path = prefix + (child.get("name"),)
            try:
                counts[path] = int(child.get("count"))
            except (TypeError, ValueError):
                pass
            walk(child, path, counts)

    for node in root.iter("SampleNode"):
        counts = {}
        walk(node, (), counts)
        out[node.get("name")] = counts
    return out


def read_workspace(wsp_path: str) -> WspWorkspace:
    from flowkit._utils import wsp_utils

    parsed = wsp_utils.parse_wsp(wsp_path)
    warnings: list[str] = []
    try:
        counts = _flowjo_counts(wsp_path)
    except Exception as exc:  # counts are only used for the plausibility check
        counts = {}
        warnings.append(f"FlowJo-Zählungen nicht lesbar: {exc}")

    samples: dict[str, WspSample] = {}
    comps = []
    for sid, sdict in parsed["samples"].items():
        uri = sdict.get("sample_uri") or ""
        name = sid if sid.lower().endswith(".fcs") or not uri else os.path.basename(uri.replace("\\", "/"))
        strategy = sdict["gating_strategy"]
        ws = WspSample(name=name, flowjo_counts=counts.get(sid, {}))
        pending = list(strategy.get_gate_ids())
        while pending:
            progressed = []
            for gname, gpath in pending:
                parent = tuple(gpath[1:])
                if parent and parent not in ws.populations:
                    continue
                gate = strategy.get_gate(gname, gpath)
                path = parent + (gname,)
                try:
                    if type(gate).__name__ == "BooleanGate":
                        terms = []
                        for ref in gate.gate_refs:
                            ref_path = tuple(ref["path"][1:]) + (ref["ref"],)
                            if ref_path not in ws.populations:
                                raise KeyError(ref_path)
                            negate = bool(ref.get("complement")) ^ (gate.type == "not")
                            terms.append(("/".join(ref_path), ws.populations[ref_path], negate))
                        step = make_boolean_step("or" if gate.type == "or" else "and", terms)
                    else:
                        step = _convert_geometric(gate, strategy)
                except KeyError:
                    continue  # referenced population not converted yet -> next round
                except Exception as exc:
                    warnings.append(f"{name}: Gate '{'/'.join(path)}' übersprungen ({exc})")
                    progressed.append((gname, gpath))
                    continue
                ws.populations[path] = list(ws.populations.get(parent, [])) + [step]
                progressed.append((gname, gpath))
            if not progressed:
                for gname, gpath in pending:
                    warnings.append(f"{name}: Gate '{'/'.join(tuple(gpath[1:]) + (gname,))}' nicht auflösbar")
                break
            pending = [p for p in pending if p not in progressed]
        samples[name] = ws
        comp = sdict.get("compensation")
        if isinstance(comp, dict) and comp.get("matrix_array") is not None:
            comps.append((comp.get("matrix_name") or "FlowJo", tuple(comp["detectors"]),
                          np.asarray(comp["matrix_array"], dtype=float)))

    comp_matrix, comp_note = None, "Keine Kompensation im Workspace."
    if comps:
        keyed = collections.Counter((n, d, m.tobytes()) for n, d, m in comps)
        (name, dets, _), n_same = keyed.most_common(1)[0]
        arr = next(m for n, d, m in comps if (n, d) == (name, dets) and m.tobytes() == _)
        comp_matrix = CompMatrix(list(dets), arr, name=f"FlowJo: {name}", source=os.path.basename(wsp_path))
        if len(keyed) == 1:
            comp_note = f"'{name}' ({len(dets)} Kanäle), für alle {n_same} Proben gleich."
        else:
            comp_note = (f"'{name}' ({len(dets)} Kanäle) — die häufigste von {len(keyed)} verschiedenen Matrizen "
                         f"({n_same} von {len(comps)} Proben). Dieses Tool nutzt EINE Matrix pro Experiment.")
            warnings.append("Proben verwenden unterschiedliche Kompensationsmatrizen — übernommen wird die häufigste.")

    groups = {g: [_sample_name(parsed, s) for s in v.get("samples", [])] for g, v in parsed.get("groups", {}).items()}
    return WspWorkspace(wsp_path, samples, groups, comp_matrix, comp_note, warnings)


def _sample_name(parsed, sid):
    sdict = parsed["samples"].get(sid, {})
    uri = sdict.get("sample_uri") or ""
    return sid if sid.lower().endswith(".fcs") or not uri else os.path.basename(uri.replace("\\", "/"))


# ------------------------------------------------------------------ conversion to gate-sets / strategies
def _def_key(steps: list[GateStep]) -> str:
    return json.dumps([_step_to_dict(s) for s in steps], sort_keys=True)


def population_names(paths: list[Path]) -> dict:
    """Shortest unique 'parent/child' suffix for every population path, e.g.
    'CD3+CD56+/Comp-APC-A+' when 'Comp-APC-A+' exists under several parents."""
    names = {}
    for p in paths:
        for k in range(1, len(p) + 1):
            suffix = p[-k:]
            if not any(q != p and q[-k:] == suffix for q in paths):
                break
        names[p] = "/".join(p[-k:])
    return names


def _stem(name: str, suffix: str = "") -> str:
    stem = safe_file_stem(name) + (f"__{safe_file_stem(suffix)}" if suffix else "")
    if len(stem) > 120:
        stem = stem[:100] + "_" + hashlib.md5(stem.encode()).hexdigest()[:10]
    return stem


@dataclass
class ImportPlan:
    gate_sets: list  # (GateSet, file stem)
    strategies: list  # (name, tree dict)
    matched: list  # sample names that have an FCS file in the experiment
    unmatched: list
    names: dict  # Path -> gate-set name
    scope_summary: collections.Counter


def plan_import(ws: WspWorkspace, experiment_files: list[str]) -> ImportPlan:
    files = set(experiment_files)
    matched = [s for s in ws.samples if s in files]
    unmatched = [s for s in ws.samples if s not in files]
    use = matched or list(ws.samples)

    all_paths: list[Path] = []
    for s in use:
        for p in ws.samples[s].populations:
            if p not in all_paths:
                all_paths.append(p)
    names = population_names(all_paths)

    candidate_groups = [(g, [s for s in members if s in use]) for g, members in ws.groups.items()]
    candidate_groups = [(g, m) for g, m in candidate_groups if m and len(m) < len(use)]

    gate_sets, scopes = [], collections.Counter()
    for p in all_paths:
        defs = collections.OrderedDict()
        for s in use:
            steps = ws.samples[s].populations.get(p)
            if steps is not None:
                defs.setdefault(_def_key(steps), (steps, []))[1].append(s)
        name = names[p]
        if len(defs) == 1 and len(next(iter(defs.values()))[1]) == len(use):
            steps, _ = next(iter(defs.values()))
            gate_sets.append((GateSet(name, {"type": "all"}, steps), _stem(name)))
            scopes["all"] += 1
            continue
        for steps, members in defs.values():
            group = _matching_group(members, use, candidate_groups)
            if group:
                gate_sets.append((GateSet(name, {"type": "group", "pattern": group}, steps), _stem(name, "grp_" + group)))
                scopes["group"] += 1
            else:
                for s in members:
                    gate_sets.append((GateSet(name, {"type": "file", "filename": s}, steps),
                                      _stem(name, os.path.splitext(s)[0])))
                    scopes["file"] += 1

    strategies = _plan_strategies(ws, use, all_paths)
    return ImportPlan(gate_sets, strategies, matched, unmatched, names, scopes)


def _matching_group(members: list[str], use: list[str], groups) -> str | None:
    """A FlowJo group whose samples are exactly `members` and whose name, used
    as a file-name pattern, selects exactly those samples."""
    mset = set(members)
    for g, gmembers in groups:
        if set(gmembers) != mset:
            continue
        hits = {s for s in use if g.lower() in s.lower()}
        if hits == mset:
            return g
    return None


def _plan_strategies(ws: WspWorkspace, use: list[str], all_paths: list[Path]) -> list:
    clusters = collections.OrderedDict()
    for s in use:
        pops = ws.samples[s].populations
        key = json.dumps([(list(p), _def_key(pops[p])) for p in all_paths if p in pops])
        clusters.setdefault(key, []).append(s)
    base = os.path.splitext(os.path.basename(ws.path))[0]
    out = []
    for i, members in enumerate(clusters.values()):
        if len(clusters) == 1:
            label = f"FlowJo {base}"
        else:
            group = next((g for g, m in ws.groups.items() if set(m) & set(use) == set(members)), None)
            label = f"FlowJo {base} – {group}" if group else (
                f"FlowJo {base} – {os.path.splitext(members[0])[0]}" + (f" (+{len(members) - 1})" if len(members) > 1 else "")
            )
        out.append((label, build_tree(ws.samples[members[0]].populations)))
    return out


def build_tree(populations: dict) -> dict:
    """GUI gate tree (node id -> GateNode) from a sample's populations."""
    tree = {"root": GateNode(node_id="root", parent_id=None, step=None, name="Alle Events")}
    ids = {(): "root"}
    for i, (path, steps) in enumerate(populations.items()):
        parent_id = ids.get(path[:-1])
        if parent_id is None:
            continue
        node_id = f"node_{i}"
        tree[node_id] = GateNode(node_id=node_id, parent_id=parent_id, step=steps[-1], name=path[-1])
        tree[parent_id].children.append(node_id)
        ids[path] = node_id
    return tree


# ------------------------------------------------------------------ plausibility check
def compare_counts(sample: WspSample, df: pd.DataFrame) -> pd.DataFrame:
    """Our event count per population vs. the count FlowJo stored."""
    masks = {(): np.ones(len(df), dtype=bool)}
    rows = []
    for path, steps in sample.populations.items():
        parent = masks.get(path[:-1])
        if parent is None:
            continue
        try:
            m = parent & steps[-1].mask(df)
        except KeyError as exc:
            rows.append({"Population": "/".join(path), "FlowJo": sample.flowjo_counts.get(path),
                         "Dieses Tool": None, "Abweichung %": None, "Hinweis": f"Kanal fehlt: {exc}"})
            continue
        masks[path] = m
        ours, fj = int(m.sum()), sample.flowjo_counts.get(path)
        dev = (100.0 * (ours - fj) / fj) if fj else (0.0 if fj == 0 and ours == 0 else None)
        rows.append({"Population": "/".join(path), "FlowJo": fj, "Dieses Tool": ours,
                     "Abweichung %": dev, "Hinweis": ""})
    return pd.DataFrame(rows)
