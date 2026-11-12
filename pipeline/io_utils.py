"""Loading helpers for FCS files."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import flowkit as fk
import pandas as pd

from compensation import CompMatrix, parse_spill


@dataclass
class FcsData:
    path: str
    filename: str
    df: pd.DataFrame  # raw channel values, one column per PnN channel
    channel_ranges: dict[str, float] = None  # $PnR instrument range per channel, if available
    marker_names: dict[str, str] = None  # $PnS marker/stain name per channel (e.g. 'CD107a'), if set
    spill: CompMatrix | None = None  # spillover matrix stored in the file ($SPILLOVER/SPILL), if any
    compensation: str = "keine"  # which compensation df currently has applied (description for the UI)


def load_fcs(path: str) -> FcsData:
    """Load a single FCS file and return its raw (uncompensated, untransformed)
    event data. The file's own spillover matrix is parsed into .spill but NOT
    applied — use compensation.compensate() for that."""
    sample = fk.Sample(path)
    df = sample.as_dataframe(source="raw")
    # flatten MultiIndex columns (pnn, pns) -> pnn only
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    meta = sample.metadata
    channel_ranges = {}
    for i, channel in enumerate(sample.pnn_labels, start=1):
        raw_range = meta.get(f"p{i}r")
        if raw_range is not None:
            try:
                channel_ranges[channel] = float(raw_range)
            except (TypeError, ValueError):
                pass

    marker_names = {}
    for pnn, pns in zip(sample.pnn_labels, getattr(sample, "pns_labels", []) or []):
        if pns and str(pns).strip() and str(pns).strip() != pnn:
            marker_names[pnn] = str(pns).strip()

    return FcsData(
        path=path, filename=os.path.basename(path), df=df,
        channel_ranges=channel_ranges, marker_names=marker_names,
        spill=parse_spill(meta, source=os.path.basename(path)),
    )


_RESERVED_SUBDIRS = {"gates", "exports", "panels", "strategies", "compensation"}

_NUM_RE = re.compile(r"(\d+)")


def natural_sort_key(s: str):
    """Sort key that orders embedded numbers numerically ('A2' < 'A10'),
    case-insensitively, instead of character by character."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in _NUM_RE.split(s)]


def find_fcs_files(input_dir: str, recursive: bool = False) -> list[str]:
    """List .fcs files directly in input_dir, or (recursive=True) anywhere
    under it — e.g. an experiment folder with one subfolder per condition.
    The 'gates' and 'exports' subfolders are skipped since those hold
    gate-set JSON / export output, never raw data."""
    if recursive:
        files = []
        for root, dirnames, filenames in os.walk(input_dir):
            dirnames[:] = [d for d in dirnames if d not in _RESERVED_SUBDIRS]
            files.extend(
                os.path.join(root, f) for f in filenames if f.lower().endswith(".fcs")
            )
        files.sort(key=natural_sort_key)
    else:
        files = [
            os.path.join(input_dir, f)
            for f in sorted(os.listdir(input_dir), key=natural_sort_key)
            if f.lower().endswith(".fcs")
        ]
    if not files:
        raise FileNotFoundError(f"No .fcs files found in {input_dir}" + (" (recursively)" if recursive else ""))
    return files
