"""Spillover / compensation matrices.

BD FACSDiva (and most acquisition software) stores the event data in the
.fcs file UNCOMPENSATED and only attaches the spillover matrix as the
$SPILLOVER / SPILL keyword ("APPLY COMPENSATION=TRUE" merely tells analysis
software to apply it). Analysis software then computes

    compensated = raw @ inv(S)

where S is the N x N spillover matrix: row i = fluorochrome/source channel,
column j = detector, S[i, j] = fraction of channel i's signal that spills
into detector j (diagonal = 1). Only the N listed channels are changed; all
others (FSC, SSC, Time, ...) stay as they are.

Matrices are stored as JSON ({"name", "channels", "spill"}) and can be
exchanged as CSV (first row = detector names, first column = source names,
values as fractions; a CSV in percent — diagonal 100 — is recognized too).
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# metadata keys (flowkit lower-cases and strips '$') that may hold the matrix
_SPILL_KEYS = ("spillover", "spill", "spillovermatrix", "comp")


@dataclass
class CompMatrix:
    channels: list[str]
    spill: np.ndarray
    name: str = ""
    source: str = field(default="", compare=False)  # where it came from (file name / path), informational

    def __post_init__(self):
        self.spill = np.asarray(self.spill, dtype=float)
        n = len(self.channels)
        if self.spill.shape != (n, n):
            raise ValueError(f"Spillover-Matrix hat Form {self.spill.shape}, erwartet {(n, n)}")

    def copy(self) -> "CompMatrix":
        return CompMatrix(list(self.channels), self.spill.copy(), self.name, self.source)

    def is_identity(self) -> bool:
        return np.allclose(self.spill, np.eye(len(self.channels)))

    def same_values(self, other: "CompMatrix | None") -> bool:
        return (other is not None and self.channels == other.channels
                and np.allclose(self.spill, other.spill, atol=1e-12))

    def restricted_to(self, available: list[str]) -> "CompMatrix | None":
        """Sub-matrix of the channels that exist in a file (None if none do)."""
        idx = [i for i, c in enumerate(self.channels) if c in available]
        if not idx:
            return None
        return CompMatrix([self.channels[i] for i in idx], self.spill[np.ix_(idx, idx)], self.name, self.source)


def parse_spill(metadata: dict, source: str = "") -> CompMatrix | None:
    """The spillover matrix stored in an FCS file's TEXT segment, if any."""
    for key in _SPILL_KEYS:
        raw = metadata.get(key)
        if not raw:
            continue
        parts = [p.strip() for p in str(raw).split(",")]
        try:
            n = int(float(parts[0]))
            channels = parts[1:1 + n]
            values = np.array([float(v) for v in parts[1 + n:1 + n + n * n]]).reshape(n, n)
        except (ValueError, IndexError):
            continue
        if n > 0:
            return CompMatrix(channels, values, name=f"Datei ({key.upper()})", source=source)
    return None


def compensate(df: pd.DataFrame, comp: CompMatrix | None) -> pd.DataFrame:
    """Return a compensated copy of df (unchanged df if comp is None)."""
    if comp is None:
        return df
    sub = comp.restricted_to(list(df.columns))
    if sub is None or sub.is_identity():
        return df
    out = df.copy()
    out[sub.channels] = df[sub.channels].to_numpy(dtype=float) @ np.linalg.inv(sub.spill)
    return out


# ------------------------------------------------------------------ persistence
def save_matrix(comp: CompMatrix, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {"name": comp.name, "channels": comp.channels, "spill": comp.spill.tolist()}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def load_matrix(path: str) -> CompMatrix:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return CompMatrix(raw["channels"], np.array(raw["spill"], dtype=float), raw.get("name", ""), source=path)


def export_matrix_csv(comp: CompMatrix, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([""] + comp.channels)
        for ch, row in zip(comp.channels, comp.spill):
            w.writerow([ch] + [f"{v:.8g}" for v in row])


def import_matrix_csv(path: str) -> CompMatrix:
    """Accepts the layout written by export_matrix_csv (row labels optional).
    Delimiter (',', ';' or tab) and decimal comma are auto-detected. Values
    > 1.5 off the diagonal are taken as percent."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    rows = [r for r in csv.reader(text.splitlines(), dialect) if any(c.strip() for c in r)]
    header = [c.strip() for c in rows[0]]
    has_row_labels = header[0] == "" or len(rows[1]) == len(header) and not _is_number(rows[1][0])
    channels = header[1:] if has_row_labels else header
    values = []
    for r in rows[1:1 + len(channels)]:
        cells = r[1:] if has_row_labels else r
        values.append([float(c.strip().replace(",", ".")) for c in cells[:len(channels)]])
    spill = np.array(values, dtype=float)
    if np.nanmean(np.diag(spill)) > 1.5:  # diagonal written as 100 -> values are percent
        spill = spill / 100.0
    return CompMatrix(channels, spill, name=os.path.splitext(os.path.basename(path))[0], source=path)


def _is_number(s: str) -> bool:
    try:
        float(s.strip().replace(",", "."))
        return True
    except ValueError:
        return False
