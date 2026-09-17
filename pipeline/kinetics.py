"""Per-event ratio calculation and time-binned kinetic summarisation."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd


def compute_ratio(df: pd.DataFrame, num_channel: str, denom_channel: str, min_signal: float = 1.0) -> pd.Series:
    """Per-event ratio num_channel / denom_channel.

    Events where either channel is at or below ``min_signal`` are treated as
    noise (near-zero/negative raw values occur in FCS data) and excluded
    (set to NaN) rather than producing huge or negative ratios.
    """
    num = df[num_channel].to_numpy()
    denom = df[denom_channel].to_numpy()
    valid = (num > min_signal) & (denom > min_signal)
    ratio = np.full(len(df), np.nan)
    ratio[valid] = num[valid] / denom[valid]
    return pd.Series(ratio, index=df.index, name="ratio")


def bin_kinetics(time_s: pd.Series, ratio: pd.Series, bin_width_s: float = 1.0) -> pd.DataFrame:
    """Bin per-event ratios into fixed-width time bins and summarise each bin."""
    valid = ratio.notna()
    t = time_s[valid].to_numpy()
    r = ratio[valid].to_numpy()

    if len(t) == 0:
        return pd.DataFrame(columns=["time_bin_start_s", "n_events", "median_ratio", "mean_ratio", "sem_ratio"])

    max_t = t.max()
    bin_edges = np.arange(0, max_t + bin_width_s, bin_width_s)
    bin_idx = np.digitize(t, bin_edges) - 1

    rows = []
    for b in range(len(bin_edges) - 1):
        sel = bin_idx == b
        n = int(sel.sum())
        if n == 0:
            continue
        vals = r[sel]
        rows.append(
            {
                "time_bin_start_s": bin_edges[b],
                "n_events": n,
                "median_ratio": float(np.median(vals)),
                "mean_ratio": float(np.mean(vals)),
                "sem_ratio": float(np.std(vals, ddof=1) / np.sqrt(n)) if n > 1 else np.nan,
            }
        )
    return pd.DataFrame(rows)


def parse_baseline_duration_s(filename: str) -> float | None:
    """Extract a 'baseline <N>' duration (seconds) from a filename, if present."""
    m = re.search(r"baseline\s*(\d+(?:\.\d+)?)", filename, flags=re.IGNORECASE)
    return float(m.group(1)) if m else None


def value_stats(
    values: pd.Series,
    threshold: float | None,
    n_total: int,
    n_gated: int,
) -> dict:
    """Summary statistics (count, mean/median, and optionally %positive/%negative
    above/below a threshold) for a per-event value series (a raw channel or a
    computed ratio) on an already-gated population.

    ``n_total`` is the event count before gating, ``n_gated`` the count of the
    gated population the values were computed on (values itself may contain
    additional NaNs, e.g. from ratio events excluded by a minimum-signal filter).
    """
    valid = values.dropna()
    n_valid = len(valid)
    result = {
        "n_total": n_total,
        "n_gated": n_gated,
        "pct_gated": 100 * n_gated / n_total if n_total else float("nan"),
        "n_valid": n_valid,
        "mean": float(valid.mean()) if n_valid else float("nan"),
        "median": float(valid.median()) if n_valid else float("nan"),
    }
    if threshold is not None:
        n_pos = int((valid > threshold).sum())
        n_neg = n_valid - n_pos
        result.update(
            {
                "threshold": threshold,
                "n_positive": n_pos,
                "pct_positive": 100 * n_pos / n_valid if n_valid else float("nan"),
                "n_negative": n_neg,
                "pct_negative": 100 * n_neg / n_valid if n_valid else float("nan"),
            }
        )
    return result
