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


KIN_DISPLAY_MEDIAN = "Median pro Bin (± SEM)"
KIN_DISPLAY_MEAN = "Mittelwert pro Bin (± SEM)"
KIN_DISPLAY_EVENTS = "Einzel-Events (Punkte)"
KIN_DISPLAY_DENSITY = "Einzel-Events (Dichte)"
KIN_DISPLAY_RIDGE = "Ridgeline (Verteilung pro Zeitfenster)"
KIN_DISPLAYS = [KIN_DISPLAY_MEDIAN, KIN_DISPLAY_MEAN, KIN_DISPLAY_EVENTS, KIN_DISPLAY_DENSITY, KIN_DISPLAY_RIDGE]
KIN_EVENT_DISPLAYS = (KIN_DISPLAY_EVENTS, KIN_DISPLAY_DENSITY)

RIDGE_SCHEME_STIM = "Vor / nach Stimulus"


def ridge_color_schemes() -> list[str]:
    from histogram_overlay import COLORMAP_SCHEMES, SCHEME_SINGLE

    return [RIDGE_SCHEME_STIM, *COLORMAP_SCHEMES, SCHEME_SINGLE]


def ridge_window_edges(t_lo: float, t_hi: float, window_s: float, anchor_s: float | None = None) -> np.ndarray:
    """Edges of consecutive time windows covering [t_lo, t_hi]. With an
    anchor (the stimulus time) inside the range, one edge falls exactly on
    it, so no window mixes pre- and post-stimulus events; the first window
    is then shortened to start at t_lo."""
    window_s = max(float(window_s), 1e-6)
    if t_hi <= t_lo:
        t_hi = t_lo + window_s
    start = t_lo
    if anchor_s is not None and t_lo < anchor_s < t_hi:
        start = anchor_s - np.ceil((anchor_s - t_lo) / window_s) * window_s
    n = max(int(np.ceil((t_hi - start) / window_s - 1e-9)), 1)
    edges = start + window_s * np.arange(n + 1, dtype=float)
    edges[0] = max(edges[0], t_lo)
    return edges


def draw_kinetics_ridgeline(
    ax,
    t: np.ndarray,
    v: np.ndarray,
    tr,
    window_s: float = 10.0,
    value_limits: tuple[float | None, float | None] = (None, None),
    time_limits: tuple[float | None, float | None] = (None, None),
    normalization: str | None = None,
    smooth_sigma: float = 1.5,
    n_bins: int = 128,
    offset_frac: float = 0.6,
    fill: bool = True,
    alpha: float = 0.8,
    edge: str | None = None,
    scheme: str = RIDGE_SCHEME_STIM,
    single_color: str = "#1f77b4",
    pre_color: str = "#8c8c8c",
    post_color: str = "#d62728",
    early_on_top: bool = True,
    show_median: bool = True,
    baseline_end_s: float | None = None,
) -> dict:
    """Ridgeline of the value distribution per time window: one ridge per
    window, X = value (in display transform ``tr``), ridges stacked over time.
    Median of each window is marked with a dot; the dots are joined by a thin
    line, which is the median kinetic turned on its side."""
    from histogram_overlay import (
        EDGE_SAME, NORM_MODE, OverlaySeries, compute_overlay, draw_ridgeline, scheme_colors,
    )

    tr.apply_to(ax)
    t_lo = time_limits[0] if time_limits[0] is not None else (float(t.min()) if len(t) else 0.0)
    t_hi = time_limits[1] if time_limits[1] is not None else (float(t.max()) if len(t) else 1.0)
    in_t = (t >= t_lo) & (t <= t_hi)
    t, v = t[in_t], v[in_t]

    v_lo, v_hi = value_limits
    if (v_lo is None or v_hi is None) and len(v):
        p_lo, p_hi = np.percentile(v, [0.5, 99.5])
        v_lo = p_lo if v_lo is None else v_lo
        v_hi = p_hi if v_hi is None else v_hi
    if v_lo is None or v_hi is None or v_hi <= v_lo:
        v_lo, v_hi = (0.0, 1.0) if v_lo is None else (v_lo, v_lo + 1.0)

    edges = ridge_window_edges(t_lo, t_hi, window_s, baseline_end_s)
    idx = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, len(edges) - 2)
    series, starts, ends = [], edges[:-1], edges[1:]
    fmt = ".0f" if window_s >= 1 else ".3g"
    for k in range(len(starts)):
        vals = v[idx == k]
        series.append(OverlaySeries(f"{starts[k]:{fmt}}–{ends[k]:{fmt}} s  (n={len(vals)})", vals, "#000000"))

    n = len(series)
    if scheme == RIDGE_SCHEME_STIM:
        if baseline_end_s is None:
            colors = [post_color] * n
        else:
            colors = [pre_color if e <= baseline_end_s + 1e-9 else post_color for e in ends]
    else:
        colors = scheme_colors(n, scheme, single_color) or [single_color] * n

    result = compute_overlay(series, tr, n_bins=n_bins, lo=v_lo, hi=v_hi,
                             normalization=normalization or NORM_MODE, smooth_sigma=smooth_sigma)
    order = list(range(n)) if early_on_top else list(range(n))[::-1]
    curves = [result.curves[i] for i in order]
    labels = [series[i].label for i in order]
    cols = [colors[i] for i in order]
    bases = draw_ridgeline(ax, result.centers_raw, curves, labels, cols, offset_frac=offset_frac,
                           fill=fill, alpha=alpha, edge=edge or EDGE_SAME)
    ax.set_xlim(v_lo, v_hi)

    if show_median and bases:
        meds = [(np.median(series[i].values), b) for i, b in zip(order, bases) if len(series[i].values)]
        if meds:
            mx, my = zip(*meds)
            z = 3 + 3 * n
            ax.plot(mx, my, color="black", linewidth=0.8, alpha=0.6, linestyle="--", zorder=z)
            ax.plot(mx, my, linestyle="none", marker="o", markersize=4, markerfacecolor="white",
                    markeredgecolor="black", zorder=z + 1, label="Median je Fenster")
            ax.legend(fontsize=8, loc="upper right")
    return {"n_windows": n, "n_events": int(len(v)), "n_out_of_range": int(sum(result.n_out_of_range)),
            "stimulus_found": baseline_end_s is not None}


def event_values(time_s: pd.Series, values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Per-event (time, value) pairs with NaN/inf removed (e.g. ratio events
    excluded by the minimum-signal filter)."""
    t = np.asarray(time_s, dtype=float)
    v = np.asarray(values, dtype=float)
    ok = np.isfinite(t) & np.isfinite(v)
    return t[ok], v[ok]


def draw_kinetics(
    ax,
    binned: pd.DataFrame,
    t: np.ndarray,
    v: np.ndarray,
    display: str = KIN_DISPLAY_MEDIAN,
    summary_overlay: bool = True,
    max_points: int = 200_000,
    point_size: float = 2.0,
    point_alpha: float = 0.3,
    point_color: str = "#1f4e8c",
    cmap=None,
    y_limits: tuple[float | None, float | None] = (None, None),
    baseline_end_s: float | None = None,
) -> int:
    """Draw a kinetic onto ``ax`` (not cleared here).

    Binned modes: median or mean per time bin with SEM error bars.
    Event modes: every event as a dot (randomly subsampled to ``max_points``
    for speed, deterministic seed) or as a time x value density map; the
    median per bin can be overlaid as a line. Returns the number of events
    actually drawn (relevant for the subsampling note).
    """
    from matplotlib.colors import LogNorm

    n_drawn = 0
    if display in KIN_EVENT_DISPLAYS:
        if display == KIN_DISPLAY_EVENTS and len(t):
            if len(t) > max_points > 0:
                idx = np.random.default_rng(0).choice(len(t), size=max_points, replace=False)
                idx.sort()
                ts, vs = t[idx], v[idx]
            else:
                ts, vs = t, v
            ax.scatter(ts, vs, s=point_size, color=point_color, alpha=point_alpha,
                       linewidths=0, rasterized=True, zorder=2)
            n_drawn = len(ts)
        elif display == KIN_DISPLAY_DENSITY and len(t):
            # Y range: user limits if given, otherwise 0.5-99.5 % so a few
            # extreme ratio outliers don't squash the whole population into one row.
            y_lo, y_hi = y_limits
            if y_lo is None or y_hi is None:
                p_lo, p_hi = np.percentile(v, [0.5, 99.5])
                y_lo = p_lo if y_lo is None else y_lo
                y_hi = p_hi if y_hi is None else y_hi
            if y_hi <= y_lo:
                y_hi = y_lo + 1.0
            t_lo, t_hi = float(t.min()), float(t.max())
            if t_hi <= t_lo:
                t_hi = t_lo + 1.0
            H, xe, ye = np.histogram2d(t, v, bins=[150, 100], range=[[t_lo, t_hi], [y_lo, y_hi]])
            Hm = np.ma.masked_where(H.T <= 0, H.T)
            if Hm.count():
                ax.pcolormesh(xe, ye, Hm, cmap=cmap or "turbo", norm=LogNorm(vmin=1, vmax=max(H.max(), 1)),
                              shading="flat", rasterized=True, zorder=1)
            n_drawn = int(H.sum())
        if len(v) and (y_limits[0] is None or y_limits[1] is None):
            # Auto Y for events: 0.5-99.5 % (+5 % margin) — a handful of extreme
            # single-event ratios would otherwise squash the population flat.
            p_lo, p_hi = np.percentile(v, [0.5, 99.5])
            pad = 0.05 * (p_hi - p_lo or 1.0)
            ax.set_ylim(p_lo - pad if y_limits[0] is None else y_limits[0],
                        p_hi + pad if y_limits[1] is None else y_limits[1])
        if summary_overlay and len(binned):
            ax.plot(binned["time_bin_start_s"], binned["median_ratio"], color="black", linewidth=2.2, zorder=4)
            ax.plot(binned["time_bin_start_s"], binned["median_ratio"], color="#ffd400", linewidth=1.2,
                    zorder=5, label="Median pro Bin")
    elif len(binned):
        col = "median_ratio" if display == KIN_DISPLAY_MEDIAN else "mean_ratio"
        ax.errorbar(binned["time_bin_start_s"], binned[col], yerr=binned["sem_ratio"],
                    fmt="-o", markersize=3, linewidth=1, capsize=2)

    if baseline_end_s is not None and (len(binned) or len(t)):
        ax.axvline(baseline_end_s, color="red", linestyle="--", linewidth=1,
                   label=f"Stimulus ({baseline_end_s:.0f}s)", zorder=6)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8, loc="upper right")
    return n_drawn


def kinetics_y_label(base_label: str, display: str) -> str:
    return {
        KIN_DISPLAY_MEDIAN: f"{base_label} (Median pro Bin)",
        KIN_DISPLAY_MEAN: f"{base_label} (Mittelwert pro Bin)",
    }.get(display, f"{base_label} (pro Event)")


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
