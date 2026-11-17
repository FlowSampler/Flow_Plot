"""Derived parameters: new per-event channels computed from a formula.

Like FlowJo's "Derive Parameters": e.g. a ratio, or the generalized
polarization of every cell after subtracting the autofluorescence

    GP = ([grün] - AF_g - G*([rot] - AF_r)) / ([grün] - AF_g + G*([rot] - AF_r))

Formula syntax (kept deliberately small):
  * channels in square brackets: [FITC-A], [Comp-PE-A] — the marker name
    ($PnS, e.g. [CD107a]) works too, as does the name of a derived parameter
    defined further up in the list
  * constants by name (G, AF_g, ...), defined per parameter as numbers
  * + - * / ** ( ), numbers like 1.5 or 2e3
  * functions: log (=ln), ln, log10, log2, sqrt, exp, abs, min, max

The formula is parsed with Python's ``ast`` and only the node types above are
allowed — no eval() of arbitrary code. Divisions by zero or log of negatives
give NaN for that event (shown as "ungültig"), not an error.
"""
from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

DERIVED_FILE = "derived_parameters.json"  # in the experiment folder

_CHANNEL_RE = re.compile(r"\[([^\[\]]+)\]")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_FUNCTIONS = {
    "log": np.log, "ln": np.log, "log10": np.log10, "log2": np.log2,
    "sqrt": np.sqrt, "exp": np.exp, "abs": np.abs,
    "min": np.minimum, "max": np.maximum,
}
_BINOPS = {
    ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
    ast.Div: np.divide, ast.Pow: np.power,
}


class FormulaError(ValueError):
    """Formula can't be parsed / uses something not allowed / unknown names."""


@dataclass
class DerivedParam:
    name: str
    formula: str
    constants: dict[str, float] = field(default_factory=dict)
    axis_min: float | None = None  # fixed axis range for plots (None = automatic)
    axis_max: float | None = None

    @staticmethod
    def from_dict(d: dict) -> "DerivedParam":
        return DerivedParam(
            name=str(d["name"]), formula=str(d["formula"]),
            constants={str(k): float(v) for k, v in (d.get("constants") or {}).items()},
            axis_min=None if d.get("axis_min") is None else float(d["axis_min"]),
            axis_max=None if d.get("axis_max") is None else float(d["axis_max"]),
        )


# ---- templates ------------------------------------------------------------
# Shown in the dialog: pick a template + channel A/B, the formula, constants
# and axis range are filled in and can then be edited freely.
TEMPLATES: dict[str, dict] = {
    "GP (generalisierte Polarisation)": {
        "formula": "(([A] - AF_A) - G * ([B] - AF_B)) / (([A] - AF_A) + G * ([B] - AF_B))",
        "constants": {"G": 1.0, "AF_A": 0.0, "AF_B": 0.0}, "axis": (-1.0, 1.0), "name": "GP",
        "hint": "A = grün (I grün), B = rot (I rot); AF_A / AF_B = Autofluoreszenz (z.B. Median der "
                "ungefärbten Kontrolle), G = G-Faktor",
    },
    "Ratio A / B": {
        "formula": "[A] / [B]", "constants": {}, "axis": (None, None), "name": "{A}/{B}",
    },
    "Ratio nach Abzug Autofluoreszenz (A − AF_A) / (B − AF_B)": {
        "formula": "([A] - AF_A) / ([B] - AF_B)", "constants": {"AF_A": 0.0, "AF_B": 0.0},
        "axis": (None, None), "name": "{A}/{B} korr.",
        "hint": "AF_A / AF_B = Autofluoreszenz (z.B. Median der ungefärbten Kontrolle)",
    },
    "Anteil A / (A + B)": {
        "formula": "[A] / ([A] + [B])", "constants": {}, "axis": (0.0, 1.0), "name": "{A} Anteil",
    },
    "Differenz A − B": {
        "formula": "[A] - [B]", "constants": {}, "axis": (None, None), "name": "{A}-{B}",
    },
    "log10(A / B)": {
        "formula": "log10([A] / [B])", "constants": {}, "axis": (None, None), "name": "log {A}/{B}",
    },
}


def fill_template(key: str, channel_a: str, channel_b: str) -> tuple[str, str, dict[str, float], tuple]:
    """(suggested name, formula, constants, (axis_min, axis_max))."""
    t = TEMPLATES[key]
    formula = t["formula"].replace("[A]", f"[{channel_a}]").replace("[B]", f"[{channel_b}]")
    name = t["name"].replace("{A}", channel_a).replace("{B}", channel_b)
    return name, formula, dict(t["constants"]), t["axis"]


# ---- parsing / evaluation -------------------------------------------------
def _compile(formula: str) -> tuple[ast.Expression, list[str]]:
    """Replace each [channel] by a placeholder variable and parse. Returns the
    AST and the referenced channel names (in placeholder order)."""
    channels: list[str] = []

    def repl(m):
        name = m.group(1).strip()
        if name not in channels:
            channels.append(name)
        return f"__ch{channels.index(name)}"

    text = _CHANNEL_RE.sub(repl, formula.replace("×", "*").replace("÷", "/").replace("−", "-"))
    if "[" in text or "]" in text:
        raise FormulaError("Eckige Klammern passen nicht zusammen — Kanäle als [Kanalname] schreiben.")
    if not text.strip():
        raise FormulaError("Formel ist leer.")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Formel ist kein gültiger Ausdruck ({exc.msg}).") from None
    return tree, channels


def referenced_channels(formula: str) -> list[str]:
    return _compile(formula)[1]


def used_constant_names(formula: str) -> set[str]:
    tree, _ = _compile(formula)
    return {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and not n.id.startswith("__ch") and n.id not in _FUNCTIONS
    }


def _resolve_channel(name: str, df: pd.DataFrame, marker_names: dict[str, str]) -> str:
    if name in df.columns:
        return name
    matches = [ch for ch, marker in (marker_names or {}).items() if marker == name and ch in df.columns]
    if len(matches) == 1:
        return matches[0]
    lower = {c.lower(): c for c in df.columns}
    if name.lower() in lower:
        return lower[name.lower()]
    raise FormulaError(f"Kanal [{name}] gibt es in dieser Datei nicht.")


def evaluate(param: DerivedParam, df: pd.DataFrame, marker_names: dict[str, str] | None = None) -> np.ndarray:
    """Per-event values of the formula. Raises FormulaError for problems with
    the formula itself (syntax, unknown constant/channel)."""
    tree, channels = _compile(param.formula)
    values = {
        f"__ch{i}": df[_resolve_channel(ch, df, marker_names or {})].to_numpy(dtype=float)
        for i, ch in enumerate(channels)
    }

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in values:
                return values[node.id]
            if node.id in param.constants:
                return float(param.constants[node.id])
            if node.id in _FUNCTIONS:
                raise FormulaError(f"'{node.id}' ist eine Funktion — so verwenden: {node.id}(...)")
            raise FormulaError(
                f"Unbekannter Name '{node.id}'. Kanäle in eckige Klammern setzen ([{node.id}]) "
                f"oder '{node.id}' unten als Konstante definieren."
            )
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS \
                and not node.keywords:
            fn = node.func.id
            args = [ev(a) for a in node.args]
            if fn in ("min", "max"):
                if len(args) < 2:
                    raise FormulaError(f"{fn}() braucht mindestens zwei Argumente.")
                out = args[0]
                for a in args[1:]:
                    out = _FUNCTIONS[fn](out, a)
                return out
            if len(args) != 1:
                raise FormulaError(f"{fn}() braucht genau ein Argument.")
            return _FUNCTIONS[fn](args[0])
        raise FormulaError("Nicht erlaubter Ausdruck — erlaubt sind + - * / ** ( ), Zahlen, [Kanäle], "
                           "Konstanten und log/ln/log10/log2/sqrt/exp/abs/min/max.")

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        result = ev(tree)
    result = np.broadcast_to(np.asarray(result, dtype=float), (len(df),)).copy()
    result[~np.isfinite(result)] = np.nan
    return result


def validate_name(name: str, others: list[str], channels: list[str]) -> str | None:
    """Error message for an unusable parameter name, else None."""
    name = name.strip()
    if not name:
        return "Bitte einen Namen eingeben."
    if "[" in name or "]" in name:
        return "Der Name darf keine eckigen Klammern enthalten."
    if name in others:
        return f"Es gibt schon einen abgeleiteten Parameter '{name}'."
    if name in channels:
        return f"'{name}' ist bereits ein Kanal der FCS-Datei."
    return None


def valid_constant_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and not name.startswith("__") and name not in _FUNCTIONS


def apply_derived(df: pd.DataFrame, params: list[DerivedParam], marker_names: dict[str, str] | None = None
                  ) -> tuple[pd.DataFrame, dict[str, str]]:
    """df with one extra column per derived parameter (in list order, so a
    later formula may use an earlier result). Parameters whose formula can't
    be evaluated for this file (e.g. channel missing) are left out; their
    error messages are returned as {name: message}."""
    if not params:
        return df, {}
    df = df.copy()
    errors: dict[str, str] = {}
    for p in params:
        try:
            df[p.name] = evaluate(p, df, marker_names)
        except FormulaError as exc:
            errors[p.name] = str(exc)
        except Exception as exc:  # e.g. numpy shape problems — never break loading the file
            errors[p.name] = f"{type(exc).__name__}: {exc}"
    return df, errors


def load_derived(experiment_dir: str) -> list[DerivedParam]:
    path = os.path.join(experiment_dir, DERIVED_FILE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return [DerivedParam.from_dict(d) for d in json.load(fh).get("parameters", [])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_derived(experiment_dir: str, params: list[DerivedParam]) -> None:
    path = os.path.join(experiment_dir, DERIVED_FILE)
    if not params:
        if os.path.isfile(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"parameters": [asdict(p) for p in params]}, fh, indent=2, ensure_ascii=False)
