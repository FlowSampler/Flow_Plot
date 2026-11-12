"""Flexible pre-gating (polygon / rectangle / ellipse) with scope resolution
(all files / a group / one file).

Gate-set JSON files (one per gate-set, stored in the gates directory) look like:

{
  "name": "singlets_and_cells",
  "scope": {"type": "all"},
  "steps": [
    {"x_channel": "FSC-A", "y_channel": "SSC-A", "shape": "polygon",
     "vertices": [[x1, y1], [x2, y2], ...]},
    {"x_channel": "FSC-H", "y_channel": "FSC-W", "shape": "rectangle",
     "vertices": [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]},
    {"x_channel": "PerCP-A", "y_channel": "Qdot 655-A", "shape": "ellipse",
     "params": {"cx": 0.0, "cy": 0.0, "rx": 1.0, "ry": 1.0}}
  ]
}

"shape" is one of "polygon", "rectangle" (both use "vertices", a rectangle is
just its 4 corners), "ellipse" (axis-aligned, center cx/cy and radii rx/ry in
"params") or "quadrant" (one quarter of the plane split by two perpendicular
lines crossing at cx/cy, optionally rotated by angle_deg; params also carry
x_sign/y_sign, each "+" or "-", selecting which of the four quarters this
step keeps), "range" (1D gate on x_channel only, params {"min", "max"},
either may be null = unbounded; y_channel is "") or "boolean" (no channels;
params {"op": "and" | "or", "terms": [{"name": str, "not": bool, "steps":
[step dicts]}, ...]} — each term is a population given by its own gate
steps, optionally negated, and the terms are combined with AND / OR; a NOT
gate is "and" with one negated term). Like every step, a boolean step is
ANDed with the steps before it, so it always stays inside its parent.
Omitting "shape" defaults to "polygon" for backward compatibility.

scope.type is one of:
  - "all":   applies to every .fcs file in the input directory
  - "group": applies to every filename containing scope.pattern (case-insensitive substring)
  - "file":  applies only to the exact filename in scope.filename

Several gate-sets may share a NAME with different scopes — these are
variants of the same population, and for each file exactly ONE of them is
used, the most specific one: file > group > all (among several matching
groups the longest pattern wins, so 'AP-2' beats 'AP'). A file with its own
"file" gate therefore uses only that gate, never an intersection with the
"all" gate. Gate-sets with DIFFERENT names are stacked with a logical AND
(each gate-set's steps are themselves ANDed in sequence), e.g. a broad
"all files" singlet gate plus a group-specific debris gate.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath


@dataclass
class GateStep:
    x_channel: str
    y_channel: str
    shape: str = "polygon"  # "polygon" | "rectangle" | "ellipse"
    vertices: list[list[float]] | None = None  # polygon / rectangle
    params: dict | None = None  # ellipse: {"cx", "cy", "rx", "ry"}

    def mask(self, df: pd.DataFrame) -> np.ndarray:
        if self.shape == "boolean":
            return boolean_mask(df, self.params)
        if self.shape == "range":
            x = df[self.x_channel].to_numpy()
            lo, hi = self.params.get("min"), self.params.get("max")
            mask = np.ones(len(x), dtype=bool)
            if lo is not None:
                mask &= x >= lo
            if hi is not None:
                mask &= x < hi
            return mask
        x = df[self.x_channel].to_numpy()
        y = df[self.y_channel].to_numpy()
        if self.shape in ("polygon", "rectangle"):
            points = np.column_stack([x, y])
            path = MplPath(self.vertices)
            return path.contains_points(points)
        if self.shape == "ellipse":
            p = self.params
            return ((x - p["cx"]) / p["rx"]) ** 2 + ((y - p["cy"]) / p["ry"]) ** 2 <= 1.0
        if self.shape == "quadrant":
            p = self.params
            angle = np.deg2rad(p.get("angle_deg", 0.0))
            dx, dy = x - p["cx"], y - p["cy"]
            # s_x==0 is the "x-divider" line (vertical when angle_deg=0), s_y==0
            # is the "y-divider" line (horizontal when angle_deg=0) — rotated
            # together by angle_deg around (cx, cy).
            s_x = dx * np.cos(angle) + dy * np.sin(angle)
            s_y = -dx * np.sin(angle) + dy * np.cos(angle)
            mask = (s_x >= 0) if p["x_sign"] == "+" else (s_x < 0)
            mask &= (s_y >= 0) if p["y_sign"] == "+" else (s_y < 0)
            return mask
        raise ValueError(f"Unknown gate shape: {self.shape!r}")

    @property
    def is_boolean(self) -> bool:
        return self.shape == "boolean"

    def describe(self) -> str:
        if self.shape == "boolean":
            return boolean_description(self.params)
        if self.shape == "range":
            lo, hi = self.params.get("min"), self.params.get("max")
            return f"{self.x_channel}: {'-∞' if lo is None else f'{lo:g}'} … {'∞' if hi is None else f'{hi:g}'}"
        return f"{self.shape} {self.x_channel} / {self.y_channel}"


def step_from_dict(d: dict) -> GateStep:
    return GateStep(
        x_channel=d.get("x_channel", ""), y_channel=d.get("y_channel", ""), shape=d.get("shape", "polygon"),
        vertices=d.get("vertices"), params=d.get("params"),
    )


def boolean_mask(df: pd.DataFrame, params: dict) -> np.ndarray:
    op = params.get("op", "and")
    result = np.ones(len(df), dtype=bool) if op == "and" else np.zeros(len(df), dtype=bool)
    for term in params.get("terms", []):
        m = np.ones(len(df), dtype=bool)
        for s in term.get("steps", []):
            m &= step_from_dict(s).mask(df)
        if term.get("not"):
            m = ~m
        if op == "and":
            result &= m
        else:
            result |= m
    return result


def boolean_description(params: dict) -> str:
    word = " UND " if params.get("op", "and") == "and" else " ODER "
    return word.join(("NICHT " if t.get("not") else "") + t.get("name", "?") for t in params.get("terms", []))


def make_boolean_step(op: str, terms: list[tuple[str, list[GateStep], bool]]) -> GateStep:
    """terms: (population name, its gate steps, negate). op: 'and' | 'or'."""
    if op not in ("and", "or"):
        raise ValueError(f"Unknown boolean operator {op!r}")
    return GateStep(
        x_channel="", y_channel="", shape="boolean",
        params={"op": op, "terms": [
            {"name": name, "not": bool(neg), "steps": [_step_to_dict(s) for s in steps]} for name, steps, neg in terms
        ]},
    )


@dataclass
class GateSet:
    name: str
    scope: dict
    steps: list[GateStep] = field(default_factory=list)
    source_path: str = ""

    def matches(self, filename: str) -> bool:
        scope_type = self.scope.get("type")
        if scope_type == "all":
            return True
        if scope_type == "group":
            pattern = self.scope.get("pattern", "")
            return pattern.lower() in filename.lower()
        if scope_type == "file":
            return os.path.basename(self.scope.get("filename", "")) == filename
        raise ValueError(f"Unknown scope type: {scope_type!r} in gate set {self.name!r}")

    def priority(self) -> int:
        return {"all": 1, "group": 2, "file": 3}[self.scope.get("type")]

    def mask(self, df: pd.DataFrame) -> np.ndarray:
        combined = np.ones(len(df), dtype=bool)
        for step in self.steps:
            combined &= step.mask(df)
        return combined


def load_gate_set(path: str) -> GateSet:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    steps = [step_from_dict(s) for s in raw.get("steps", [])]
    return GateSet(name=raw["name"], scope=raw["scope"], steps=steps, source_path=path)


def load_all_gate_sets(gates_dir: str) -> list[GateSet]:
    if not os.path.isdir(gates_dir):
        return []
    gate_sets = []
    for f in sorted(os.listdir(gates_dir)):
        if f.lower().endswith(".json"):
            gate_sets.append(load_gate_set(os.path.join(gates_dir, f)))
    return gate_sets


def safe_file_stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def gate_set_file_stem(gate_set: GateSet) -> str:
    """One JSON file per (name, scope): 'Lymphocytes', 'Lymphocytes__grp_AP-2',
    'Lymphocytes__AP-2_CD18 - PE' — so a file/group variant never overwrites
    the 'all' variant of the same population."""
    scope = gate_set.scope
    if scope.get("type") == "group":
        suffix = "grp_" + scope.get("pattern", "")
    elif scope.get("type") == "file":
        suffix = os.path.splitext(os.path.basename(scope.get("filename", "")))[0]
    else:
        suffix = ""
    stem = safe_file_stem(gate_set.name) + (f"__{safe_file_stem(suffix)}" if suffix else "")
    if len(stem) > 120:  # keep full paths below the Windows limit
        import hashlib
        stem = stem[:100] + "_" + hashlib.md5(stem.encode()).hexdigest()[:10]
    return stem


def _same_scope(a: dict, b: dict) -> bool:
    if a.get("type") != b.get("type"):
        return False
    if a.get("type") == "group":
        return a.get("pattern", "").lower() == b.get("pattern", "").lower()
    if a.get("type") == "file":
        return os.path.basename(a.get("filename", "")) == os.path.basename(b.get("filename", ""))
    return True


def save_gate_set(gate_set: GateSet, gates_dir: str, file_stem: str | None = None) -> str:
    """Write the gate-set to '<gates_dir>/<stem>.json'. Any OTHER file holding
    the same name with the same scope (e.g. saved by an older version under a
    different file name) is removed, so there is never more than one variant
    per (name, scope)."""
    os.makedirs(gates_dir, exist_ok=True)
    payload = {
        "name": gate_set.name,
        "scope": gate_set.scope,
        "steps": [_step_to_dict(s) for s in gate_set.steps],
    }
    out_path = os.path.join(gates_dir, f"{file_stem or gate_set_file_stem(gate_set)}.json")
    for f in os.listdir(gates_dir):
        p = os.path.join(gates_dir, f)
        if not f.lower().endswith(".json") or os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(out_path)):
            continue
        try:
            other = load_gate_set(p)
        except (OSError, ValueError, KeyError):
            continue
        if other.name == gate_set.name and _same_scope(other.scope, gate_set.scope):
            os.remove(p)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return out_path


def _specificity(gs: GateSet) -> tuple[int, int]:
    """Sort key: scope priority (file > group > all), then group-pattern length."""
    pattern_len = len(gs.scope.get("pattern", "")) if gs.scope.get("type") == "group" else 0
    return gs.priority(), pattern_len


def resolve_gate_sets_for_file(filename: str, gate_sets: list[GateSet]) -> list[GateSet]:
    """The gate-sets that apply to `filename`: per NAME only the most
    specific matching variant (file > group > all, then longest group
    pattern; on a full tie the first one loaded), in first-seen name order."""
    best: dict[str, GateSet] = {}
    for gs in gate_sets:
        if not gs.matches(filename):
            continue
        current = best.get(gs.name)
        if current is None or _specificity(gs) > _specificity(current):
            best[gs.name] = gs
    return list(best.values())


def apply_gates(df: pd.DataFrame, filename: str, gate_sets: list[GateSet]) -> tuple[np.ndarray, list[str]]:
    """Return (boolean mask, list of applied gate-set names) for the given file.

    Per name only the most specific variant is used (see
    resolve_gate_sets_for_file); DIFFERENT names are ANDed. That is right for
    STACKED refinement gates (e.g. an "all files" singlet gate plus a "ZCL278"
    group-specific debris gate), but not for several mutually exclusive
    populations (e.g. "Lebend" and "Dead") — their AND is (nearly) empty. Use
    apply_named_gate_set() to pick exactly one named population.
    """
    applicable = resolve_gate_sets_for_file(filename, gate_sets)
    mask = np.ones(len(df), dtype=bool)
    for gs in applicable:
        mask &= gs.mask(df)
    return mask, [gs.name for gs in applicable]


def list_gate_set_names(gate_sets: list[GateSet]) -> list[str]:
    """Unique gate-set names, in first-seen order."""
    seen = []
    for gs in gate_sets:
        if gs.name not in seen:
            seen.append(gs.name)
    return seen


def dependent_overrides(gate_sets: list[GateSet], override: GateSet, filename: str) -> list[GateSet]:
    """Gate-sets built ON TOP of the population that `override` re-defines.

    Every gate-set stores its full root -> population path. When e.g.
    'Lymphocytes' gets its own variant for one file, 'CD19+' (whose path
    starts with the general Lymphocytes gate) would otherwise keep using the
    old Lymphocytes gate for that file. For each such dependent population
    this returns a new variant with the override's scope whose path is the
    override's steps + the dependent's remaining steps. `filename` is a file
    the override applies to (used to resolve which variants are in effect)."""
    others = [gs for gs in gate_sets if not (gs.name == override.name and _same_scope(gs.scope, override.scope))]
    general = next((gs for gs in resolve_gate_sets_for_file(filename, others) if gs.name == override.name), None)
    if general is None:
        return []
    prefix = [_step_to_dict(s) for s in general.steps]
    new_prefix = [_step_to_dict(s) for s in override.steps]
    if prefix == new_prefix:
        return []
    out = []
    for gs in resolve_gate_sets_for_file(filename, others):
        if gs.name == override.name or len(gs.steps) <= len(prefix):
            continue
        if [_step_to_dict(s) for s in gs.steps[:len(prefix)]] == prefix:
            out.append(GateSet(gs.name, dict(override.scope), list(override.steps) + list(gs.steps[len(prefix):])))
    return out


def apply_named_gate_set(df: pd.DataFrame, filename: str, gate_sets: list[GateSet], name: str) -> tuple[np.ndarray, list[str]]:
    """Apply only the gate-set called `name` — for this file the single most
    specific variant of it (file > group > all)."""
    named = [gs for gs in gate_sets if gs.name == name]
    return apply_gates(df, filename, named)


# --------------------------------------------------------------------------
# Gate hierarchy (tree) used by the interactive GUI while a gate-set is being
# built: a root "all events" node, with child nodes for each drawn gate step.
# Several children under the same parent are siblings at the same hierarchy
# level (e.g. two alternative refinements of "Cells"); navigating into a node
# shows only the population that passed every step from the root down to it.
# --------------------------------------------------------------------------


@dataclass
class GateNode:
    node_id: str
    parent_id: str | None
    step: GateStep | None  # None only for the root node
    name: str = ""
    children: list[str] = field(default_factory=list)


def new_gate_tree() -> dict[str, GateNode]:
    return {"root": GateNode(node_id="root", parent_id=None, step=None, name="Alle Events")}


def node_path(tree: dict[str, GateNode], node_id: str) -> list[str]:
    """Ids from the root down to (and including) node_id."""
    path = []
    nid = node_id
    while nid is not None:
        path.append(nid)
        nid = tree[nid].parent_id
    return list(reversed(path))


def node_steps(tree: dict[str, GateNode], node_id: str) -> list[GateStep]:
    """Ordered GateSteps from the root down to node_id (root itself has none)."""
    return [tree[nid].step for nid in node_path(tree, node_id) if tree[nid].step is not None]


def node_mask(df: pd.DataFrame, tree: dict[str, GateNode], node_id: str, base_mask: np.ndarray | None = None) -> np.ndarray:
    mask = base_mask.copy() if base_mask is not None else np.ones(len(df), dtype=bool)
    for step in node_steps(tree, node_id):
        mask &= step.mask(df)
    return mask


def point_in_step(step: GateStep, x: float, y: float) -> bool:
    """Single-point hit test, used for click/double-click-into-gate navigation."""
    if step.shape == "boolean":
        return False
    if step.shape == "range":
        return bool(step.mask(pd.DataFrame({step.x_channel: [x]}))[0])
    df = pd.DataFrame({step.x_channel: [x], step.y_channel: [y]})
    return bool(step.mask(df)[0])


def add_child_node(
    tree: dict[str, GateNode], parent_id: str, step: GateStep, name: str, node_id_hint: str
) -> str:
    node_id = node_id_hint
    tree[node_id] = GateNode(node_id=node_id, parent_id=parent_id, step=step, name=name)
    tree[parent_id].children.append(node_id)
    return node_id


def remove_subtree(tree: dict[str, GateNode], node_id: str) -> None:
    """Delete node_id and all of its descendants from the tree (root is not removable)."""
    if node_id == "root":
        return
    node = tree.get(node_id)
    if node is None:
        return
    for child_id in list(node.children):
        remove_subtree(tree, child_id)
    parent = tree.get(node.parent_id)
    if parent is not None and node_id in parent.children:
        parent.children.remove(node_id)
    del tree[node_id]


# --------------------------------------------------------------------------
# Whole-tree persistence ("gating strategies"): unlike a GateSet (one linear
# root->node path, exported for Kinetik/Statistik to apply), this saves the
# ENTIRE editable tree — every branch/sibling — so a gating session can be
# closed and reopened later, or so several genuinely different gating
# schemes (e.g. different populations/inputs) can be saved side by side and
# switched between in the GUI.
# --------------------------------------------------------------------------


def _step_to_dict(step: GateStep | None) -> dict | None:
    if step is None:
        return None
    return {
        "x_channel": step.x_channel,
        "y_channel": step.y_channel,
        "shape": step.shape,
        "vertices": step.vertices,
        "params": step.params,
    }


def save_gate_tree(tree: dict[str, GateNode], name: str, strategies_dir: str) -> str:
    os.makedirs(strategies_dir, exist_ok=True)
    nodes_payload = {
        node_id: {
            "parent_id": node.parent_id,
            "name": node.name,
            "step": _step_to_dict(node.step),
            "children": list(node.children),
        }
        for node_id, node in tree.items()
    }
    payload = {"name": name, "nodes": nodes_payload}
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    out_path = os.path.join(strategies_dir, f"{safe_name}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return out_path


def load_gate_tree(path: str) -> tuple[dict[str, GateNode], int]:
    """Returns (tree, next_node_counter) — the counter is derived from the
    highest existing 'node_<i>' id so newly drawn gates keep getting unique
    ids after reloading."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    tree: dict[str, GateNode] = {}
    for node_id, n in raw["nodes"].items():
        step_dict = n.get("step")
        step = step_from_dict(step_dict) if step_dict else None
        tree[node_id] = GateNode(
            node_id=node_id, parent_id=n.get("parent_id"), step=step,
            name=n.get("name", ""), children=list(n.get("children", [])),
        )
    max_idx = -1
    for node_id in tree:
        if node_id.startswith("node_"):
            try:
                max_idx = max(max_idx, int(node_id.split("_", 1)[1]))
            except ValueError:
                pass
    return tree, max_idx + 1


def list_saved_strategies(strategies_dir: str) -> list[tuple[str, str]]:
    """(display_name, path) pairs for every saved strategy, sorted by name."""
    if not os.path.isdir(strategies_dir):
        return []
    out = []
    for f in sorted(os.listdir(strategies_dir)):
        if not f.lower().endswith(".json"):
            continue
        path = os.path.join(strategies_dir, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                name = json.load(fh).get("name") or os.path.splitext(f)[0]
        except (json.JSONDecodeError, OSError):
            name = os.path.splitext(f)[0]
        out.append((name, path))
    out.sort(key=lambda t: t[0].lower())
    return out


def delete_gate_tree(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
