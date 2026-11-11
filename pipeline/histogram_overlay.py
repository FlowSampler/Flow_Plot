"""1D histogram overlays of several files/populations on one channel.

Display modes:
  * Overlay            - all curves on top of each other, same baseline.
  * Offset             - every curve shifted UP by a fixed step (ridgeline look),
                         so shapes can be compared without hiding each other.
  * Staggered          - shifted up AND to the right (diagonal, FlowJo-style
                         "staggered offset"), a pseudo-3D look for many curves.
  * Getrennt (Panels)  - one panel per curve, shared X and Y axes.

Binning happens in the DISPLAY space of the chosen axis transform (Linear /
Log / Logicle): equal-width bins on screen, mapped back to raw values for
np.histogram. Without that, a log/logicle axis would show bins that are
huge on the left and tiny on the right. Same reason the staggered X-shift is
applied in display space rather than in raw units.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from matplotlib.figure import Figure
from scipy.ndimage import gaussian_filter1d

from transforms import apply_axis_scale

MODES = ["Overlay", "Offset", "Staggered", "Getrennt (Panels)"]
NORM_MODE = "% vom Maximum (Modus)"
NORM_COUNT = "Anzahl Events"
NORM_PERCENT = "% der Events pro Bin"
NORMALIZATIONS = [NORM_MODE, NORM_COUNT, NORM_PERCENT]

DEFAULT_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
    "#393b79", "#ad494a", "#637939", "#e6550d", "#7b4173",
]


def default_color(index: int) -> str:
    return DEFAULT_COLORS[index % len(DEFAULT_COLORS)]


@dataclass
class OverlaySeries:
    label: str
    values: np.ndarray  # raw, finite values of the population
    color: str
    n_gated: int = 0


@dataclass
class OverlayResult:
    series: list[OverlaySeries]
    centers_raw: np.ndarray
    curves: list[np.ndarray]
    n_out_of_range: list[int]
    y_label: str


class DisplayTransform:
    """Forward/inverse mapping raw <-> display coordinates for one axis
    transform, borrowed from the same matplotlib scale used for plotting."""

    def __init__(self, transform_name: str, **scale_kwargs):
        self.transform_name = transform_name
        self.scale_kwargs = scale_kwargs
        ax = Figure().add_subplot(111)
        apply_axis_scale(ax, "x", transform_name, **scale_kwargs)
        self._fwd = ax.xaxis.get_transform()
        self._inv = self._fwd.inverted()

    def forward(self, v) -> np.ndarray:
        return np.asarray(self._fwd.transform_non_affine(np.asarray(v, dtype=float)), dtype=float)

    def inverse(self, v) -> np.ndarray:
        return np.asarray(self._inv.transform_non_affine(np.asarray(v, dtype=float)), dtype=float)

    def apply_to(self, ax):
        apply_axis_scale(ax, "x", self.transform_name, **self.scale_kwargs)


def compute_overlay(
    series: list[OverlaySeries],
    tr: DisplayTransform,
    n_bins: int = 256,
    lo: float | None = None,
    hi: float | None = None,
    normalization: str = NORM_MODE,
    smooth_sigma: float = 0.0,
) -> OverlayResult:
    """Common bin edges for all series (so curves are directly comparable),
    then per-series counts -> optional smoothing -> normalization."""
    pooled = np.concatenate([s.values for s in series]) if series else np.empty(0)
    if lo is None:
        lo = float(pooled.min()) if len(pooled) else 0.0
    if hi is None:
        hi = float(pooled.max()) if len(pooled) else 1.0
    if hi <= lo:
        hi = lo + 1.0

    t_lo, t_hi = tr.forward([lo, hi])
    edges_t = np.linspace(t_lo, t_hi, max(int(n_bins), 2) + 1)
    edges_raw = tr.inverse(edges_t)
    edges_raw[0], edges_raw[-1] = lo, hi  # guard against round-trip float drift
    centers_raw = tr.inverse((edges_t[:-1] + edges_t[1:]) / 2)

    curves, n_out = [], []
    for s in series:
        counts, _ = np.histogram(s.values, bins=edges_raw)
        counts = counts.astype(float)
        n_out.append(int(len(s.values) - counts.sum()))
        if smooth_sigma > 0:
            counts = gaussian_filter1d(counts, smooth_sigma, mode="constant")
        if normalization == NORM_MODE:
            peak = counts.max()
            y = 100.0 * counts / peak if peak > 0 else counts
        elif normalization == NORM_PERCENT:
            y = 100.0 * counts / len(s.values) if len(s.values) else counts
        else:
            y = counts
        curves.append(y)

    y_label = {NORM_MODE: "% vom Maximum", NORM_PERCENT: "% der Events / Bin"}.get(normalization, "Events / Bin")
    return OverlayResult(series, centers_raw, curves, n_out, y_label)


def _legend_label(s: OverlaySeries, with_stats: bool) -> str:
    if not with_stats or not len(s.values):
        return s.label
    return f"{s.label}  (n={len(s.values)}, Median={np.median(s.values):.3g})"


def _short(label: str, n: int = 38) -> str:
    return label if len(label) <= n else "…" + label[-(n - 1):]


def _draw_curve(ax, x, y, base, color, fill, alpha, zorder, occlude, label=None, linewidth=1.4):
    if fill:
        if occlude:  # opaque underlay so curves in front hide the ones behind
            ax.fill_between(x, base, base + y, color=ax.get_facecolor(), zorder=zorder, linewidth=0)
        ax.fill_between(x, base, base + y, color=color, alpha=alpha, zorder=zorder, linewidth=0)
    ax.plot(x, base + y, color=color, linewidth=linewidth, zorder=zorder + 0.5, label=label)


def draw_overlay(
    fig: Figure,
    result: OverlayResult,
    tr: DisplayTransform,
    mode: str,
    x_label: str,
    title: str = "",
    offset_frac: float = 0.6,
    stagger_frac: float = 0.05,
    fill: bool = True,
    alpha: float = 0.35,
    legend_stats: bool = True,
    x_limits: tuple[float, float] | None = None,
):
    """Draw into fig (cleared first). Series order = drawing order: the
    first series is the bottom/front curve in Offset/Staggered modes."""
    fig.clear()
    series, curves, x = result.series, result.curves, result.centers_raw
    n = len(series)
    if n == 0:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Keine Dateien ausgewählt / keine gültigen Werte",
                ha="center", va="center", transform=ax.transAxes, color="#777")
        ax.set_axis_off()
        return

    y_max = max((float(c.max()) for c in curves if len(c)), default=1.0) or 1.0
    t_x = tr.forward(x)
    t_span = float(t_x[-1] - t_x[0]) if len(t_x) > 1 else 1.0
    lo_raw, hi_raw = x_limits if x_limits else (float(x[0]), float(x[-1]))

    if mode == "Getrennt (Panels)":
        axes = fig.subplots(n, 1, sharex=True, sharey=True, squeeze=False)[:, 0]
        for ax, s, y in zip(axes, series, curves):
            tr.apply_to(ax)
            _draw_curve(ax, x, y, 0.0, s.color, fill, alpha, 2, False)
            ax.text(0.01, 0.92, _legend_label(s, legend_stats), transform=ax.transAxes,
                    ha="left", va="top", fontsize=8, color=s.color, fontweight="bold")
            ax.set_ylim(0, y_max * 1.08)
            ax.tick_params(axis="y", labelsize=7)
        axes[-1].set_xlim(lo_raw, hi_raw)
        axes[-1].set_xlabel(x_label)
        fig.supylabel(result.y_label, fontsize=9)
        if title:
            fig.suptitle(title, fontsize=10)
        return

    ax = fig.add_subplot(111)
    tr.apply_to(ax)

    if mode == "Overlay":
        for i, (s, y) in enumerate(zip(series, curves)):
            _draw_curve(ax, x, y, 0.0, s.color, fill, alpha, 2 + i, False, label=_legend_label(s, legend_stats))
        ax.set_ylim(0, y_max * 1.05)
        ax.set_xlim(lo_raw, hi_raw)
        ax.set_ylabel(result.y_label)
        ax.legend(fontsize=8, loc="upper right", framealpha=0.85)
    else:
        step = offset_frac * y_max
        dx_t = stagger_frac * t_span if mode == "Staggered" else 0.0
        bases = [i * step for i in range(n)]
        for i in reversed(range(n)):  # back to front
            s, y = series[i], curves[i]
            xi = tr.inverse(t_x + i * dx_t) if dx_t else x
            ax.plot([xi[0], xi[-1]], [bases[i], bases[i]], color=s.color, linewidth=0.6,
                    alpha=0.6, zorder=2 + 2 * (n - i))
            _draw_curve(ax, xi, y, bases[i], s.color, fill, alpha, 2 + 2 * (n - i), True)
        hi_t = tr.forward([hi_raw])[0] + (n - 1) * dx_t
        ax.set_xlim(lo_raw, float(tr.inverse([hi_t])[0]))
        ax.set_ylim(0, bases[-1] + y_max * 1.08)
        ax.set_yticks(bases)
        ax.set_yticklabels([_short(_legend_label(s, legend_stats), 60) for s in series], fontsize=7)
        for lbl, s in zip(ax.get_yticklabels(), series):
            lbl.set_color(s.color)
        ax.set_ylabel(f"{result.y_label} (versetzt)")
        if dx_t:
            x_label += "   (diagonal versetzt — X-Werte exakt nur für die vorderste Kurve)"
    ax.set_xlabel(x_label)
    if title:
        ax.set_title(title, fontsize=10)
