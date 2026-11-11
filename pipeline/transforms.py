"""Axis transforms (Linear / Log / Logicle) for the gating plot.

Gating math always stays in raw channel values — only how those values are
MAPPED TO SCREEN POSITION changes. This is implemented as a custom matplotlib
axis scale ("logicle") built on FlowKit's LogicleTransform, plus matplotlib's
built-in "symlog" for a log-like view that still tolerates the small
negative/near-zero noise common in raw FCS data. Because it's a true axis
scale (not a data transform), polygon/rectangle/ellipse gates drawn while a
transform is active are captured by matplotlib in real data coordinates, so
saved GateStep vertices are unaffected by whichever transform was on screen.
"""
from __future__ import annotations

import numpy as np
from flowkit._models.transforms import LogicleTransform
from matplotlib import scale as mscale
from matplotlib import transforms as mtransforms
from matplotlib import ticker as mticker

TRANSFORM_NAMES = ["Linear", "Log", "Logicle"]


class _LogicleMplTransform(mtransforms.Transform):
    input_dims = output_dims = 1
    is_separable = True

    def __init__(self, logicle: LogicleTransform):
        super().__init__()
        self.logicle = logicle

    def transform_non_affine(self, values):
        return self.logicle.apply(np.asarray(values, dtype=float))

    def inverted(self):
        return _InvertedLogicleMplTransform(self.logicle)


class _InvertedLogicleMplTransform(mtransforms.Transform):
    input_dims = output_dims = 1
    is_separable = True

    def __init__(self, logicle: LogicleTransform):
        super().__init__()
        self.logicle = logicle

    def transform_non_affine(self, values):
        return self.logicle.inverse(np.asarray(values, dtype=float))

    def inverted(self):
        return _LogicleMplTransform(self.logicle)


class _LogicleDecadeLocator(mticker.Locator):
    """Ticks at 0 and ±10^k (major) or ±m·10^k, m=2..9 (minor). The default
    AutoLocator puts evenly spaced LINEAR ticks on a log-like axis, which all
    pile up at the top end ('50000100000150000...')."""

    def __init__(self, minor: bool = False):
        self.minor = minor

    def __call__(self):
        vmin, vmax = self.axis.get_view_interval()
        return self.tick_values(vmin, vmax)

    def tick_values(self, vmin, vmax):
        lo, hi = min(vmin, vmax), max(vmin, vmax)
        k_max = int(np.ceil(np.log10(max(abs(lo), abs(hi), 10.0))))
        # Decades below 10^2 sit inside logicle's near-linear zone around 0
        # and would overlap the '0' label, so start there.
        mults = range(2, 10) if self.minor else (1,)
        ticks = [] if self.minor else [0.0]
        for k in range(2, k_max + 1):
            for m in mults:
                ticks += [m * 10.0**k, -m * 10.0**k]
        return np.array(sorted(t for t in ticks if lo <= t <= hi))


def _decade_label(v, _pos=None) -> str:
    if v == 0:
        return "0"
    k = int(round(np.log10(abs(v))))
    return f"${'-' if v < 0 else ''}10^{{{k}}}$"


class LogicleScale(mscale.ScaleBase):
    """Registers as matplotlib scale name 'logicle'. Use via
    ax.set_xscale('logicle', t=262144, w=0.5, m=4.5, a=0.0)."""

    name = "logicle"

    def __init__(self, axis, *, t: float = 262144.0, w: float = 0.5, m: float = 4.5, a: float = 0.0, **kwargs):
        super().__init__(axis)
        self._logicle = LogicleTransform(param_t=t, param_w=w, param_m=m, param_a=a)

    def get_transform(self):
        return _LogicleMplTransform(self._logicle)

    def set_default_locators_and_formatters(self, axis):
        axis.set_major_locator(_LogicleDecadeLocator())
        axis.set_major_formatter(mticker.FuncFormatter(_decade_label))
        axis.set_minor_locator(_LogicleDecadeLocator(minor=True))
        axis.set_minor_formatter(mticker.NullFormatter())

    def limit_range_for_scale(self, vmin, vmax, minpos):
        return vmin, vmax


mscale.register_scale(LogicleScale)


def apply_axis_scale(
    ax,
    axis: str,
    transform_name: str,
    channel_range: float = 262144.0,
    log_linthresh: float = 10.0,
    logicle_w: float = 0.5,
    logicle_m: float = 4.5,
) -> None:
    """axis is 'x' or 'y'. Only changes the display scale; underlying data /
    gate coordinates are always raw and unaffected."""
    set_scale = ax.set_xscale if axis == "x" else ax.set_yscale
    if transform_name == "Linear":
        set_scale("linear")
    elif transform_name == "Log":
        set_scale("symlog", linthresh=max(log_linthresh, 1e-6))
    elif transform_name == "Logicle":
        set_scale("logicle", t=max(channel_range, 1.0), w=logicle_w, m=logicle_m, a=0.0)
    else:
        raise ValueError(f"Unknown transform: {transform_name!r}")
