"""Gating tab: drawing the density plot, axes and gate outlines."""
from __future__ import annotations

import math
import os

import matplotlib
import numpy as np
from matplotlib import patheffects
from matplotlib.colors import ListedColormap, LogNorm
from matplotlib.patches import Ellipse as EllipsePatch
from matplotlib.patches import Polygon as PolygonPatch
from scipy.ndimage import gaussian_filter

from gating import GateStep
from histogram_overlay import NORM_COUNT, DisplayTransform, OverlaySeries, compute_overlay, default_color
from transforms import apply_axis_scale, default_axis_range
from gui.constants import QUADRANT_SHAPES, HIST_1D, COLORMAPS


class GatingPlotMixin:
    """Renders the Gating-tab plot: axis limits/transforms, pseudocolor/contour/histogram plots and the outlines of existing gates."""

    @staticmethod
    def _resolve_axis_limits(values: np.ndarray, lo_str: str, hi_str: str) -> tuple[float | None, float | None, bool]:
        """Parses the Min/Max entry fields for one axis. Returns (lo, hi,
        manual). manual is False when both fields are empty — meaning
        matplotlib should autoscale that axis as usual. As soon as either
        bound is set, manual is True and the missing bound is backfilled from
        the data's own extent, so outliers on the OTHER side still get
        clipped/flagged even if only one side was constrained."""
        lo_str, hi_str = lo_str.strip(), hi_str.strip()
        try:
            lo = float(lo_str) if lo_str else None
        except ValueError:
            lo = None
        try:
            hi = float(hi_str) if hi_str else None
        except ValueError:
            hi = None
        if lo is None and hi is None:
            return None, None, False
        finite = values[np.isfinite(values)] if len(values) else values
        data_lo = float(finite.min()) if len(finite) else 0.0
        data_hi = float(finite.max()) if len(finite) else 1.0
        if lo is None:
            lo = data_lo
        if hi is None:
            hi = data_hi
        if lo >= hi:
            hi = lo + 1.0
        return lo, hi, True

    def _autofit_axis_limits(self):
        """Fill the Min/Max fields from the 1st/99th percentile of the
        currently displayed population — a one-click fix for the case that
        prompted this feature: a couple of far-out events (e.g. -10^6) that
        blow the axis out so badly the real population is squeezed flat."""
        if not self.current_path:
            return
        fcs = self._get_data(self.current_path)
        df = fcs.df
        x_ch, y_ch = self.x_channel_var.get(), self.y_channel_var.get()
        if x_ch not in df.columns or y_ch not in df.columns:
            return
        view_mask = self._cumulative_pending_mask(df)
        x_vals = df[x_ch].to_numpy()[view_mask]
        y_vals = df[y_ch].to_numpy()[view_mask]
        if len(x_vals) == 0:
            return
        x_lo, x_hi = np.percentile(x_vals, [1, 99])
        y_lo, y_hi = np.percentile(y_vals, [1, 99])
        self.x_min_var.set(f"{x_lo:.4g}")
        self.x_max_var.set(f"{x_hi:.4g}")
        self.y_min_var.set(f"{y_lo:.4g}")
        self.y_max_var.set(f"{y_hi:.4g}")

    def _reset_axis_limits(self):
        for v in (self.x_min_var, self.x_max_var, self.y_min_var, self.y_max_var):
            v.set("")

    @staticmethod
    def _default_transform_for(channel: str) -> str:
        """Scatter (FSC/SSC) and Time are shown linear, every other channel
        (fluorescence/'color' channels) on a log axis."""
        name = channel.upper()
        if "FSC" in name or "SSC" in name or "TIME" in name:
            return "Linear"
        return "Log"

    def _sync_axis_transforms(self, x_ch: str, y_ch: str):
        """When an axis gets a DIFFERENT channel (dropdown, gate navigation,
        new file), switch its transform to that channel's default. A transform
        picked by hand is kept as long as the channel stays the same."""
        last = getattr(self, "_auto_transform_channel", {})
        for axis, ch, var in (("x", x_ch, self.x_transform_var), ("y", y_ch, self.y_transform_var)):
            if last.get(axis) != ch:
                var.set(self._default_transform_for(ch))
                last[axis] = ch
        self._auto_transform_channel = last

    def _redraw(self):
        if not self.current_path:
            return
        fcs = self._get_data(self.current_path)
        df = fcs.df
        x_ch, y_ch = self.x_channel_var.get(), self.y_channel_var.get()
        if x_ch not in df.columns or y_ch not in df.columns:
            return
        self._sync_axis_transforms(x_ch, y_ch)

        # Drill-down view: only events that passed every already-committed
        # step (saved gate-sets if enabled, plus every pending step so far)
        # are shown — cells excluded by an earlier step disappear from later
        # steps' plots, same as navigating down into a FlowJo gate hierarchy.
        view_mask = self._cumulative_pending_mask(df)
        plot_type = self.plot_type_var.get()

        x_raw = df[x_ch].to_numpy(dtype=float)
        y_raw = df[y_ch].to_numpy(dtype=float)

        # Manual axis limits: clip out-of-range events to the border instead
        # of letting a couple of far-out events (e.g. -10^6) blow the axis
        # out and squeeze the real population flat. Clipped events are
        # tracked (out_of_range) and re-drawn as a red '×' at the border so
        # their presence stays obvious instead of silently disappearing.
        x_lo, x_hi, x_manual = self._resolve_axis_limits(x_raw[view_mask], self.x_min_var.get(), self.x_max_var.get())
        y_lo, y_hi, y_manual = self._resolve_axis_limits(y_raw[view_mask], self.y_min_var.get(), self.y_max_var.get())
        x_plot = np.clip(x_raw, x_lo, x_hi) if x_manual else x_raw
        y_plot = np.clip(y_raw, y_lo, y_hi) if y_manual else y_raw
        x_out = ((x_raw < x_lo) | (x_raw > x_hi)) if x_manual else np.zeros(len(df), dtype=bool)
        y_out = ((y_raw < y_lo) | (y_raw > y_hi)) if y_manual else np.zeros(len(df), dtype=bool)
        out_of_range = (x_out | y_out) & view_mask

        # No manual limits: use a FIXED per-channel range (channel range $PnR
        # + transform) instead of matplotlib's per-file autoscaling, so the
        # axes don't jump when clicking through files. Events outside it are
        # simply off-screen (not clipped / not flagged as red ×).
        if not x_manual:
            x_lo, x_hi = self._default_axis_range(x_ch, self.x_transform_var.get())
        if not y_manual:
            y_lo, y_hi = self._default_axis_range(y_ch, self.y_transform_var.get())

        value_range = [[x_lo, x_hi], [y_lo, y_hi]]
        is_hist = plot_type == HIST_1D

        self.ax.clear()
        self.ax.set_facecolor("white")
        if is_hist:
            n_out = self._plot_histogram_1d(df, view_mask, x_raw, x_ch, x_lo, x_hi)
            if not x_manual:
                n_out = 0
        else:
            xv, yv = x_plot[view_mask], y_plot[view_mask]
            if plot_type == "Scatter":
                self.ax.scatter(xv, yv, s=1, color="#1f4e8c", alpha=0.5, linewidths=0, rasterized=True)
            elif plot_type == "Pseudocolor":
                self._plot_pseudocolor(self.ax, xv, yv, x_ch, y_ch, value_range, sigma=0.0)
            elif plot_type == "Density":
                self._plot_pseudocolor(self.ax, xv, yv, x_ch, y_ch, value_range, sigma=2.0)
            elif plot_type == "Contour":
                self._plot_contour(self.ax, xv, yv, x_ch, y_ch, value_range)
            self._overlay_child_shapes(self.ax, x_ch, y_ch)
            n_out = int(out_of_range.sum())

        self._apply_axis_transforms(x_ch, y_ch, y_linear=is_hist)
        self.ax.set_xlim(x_lo, x_hi)
        if not is_hist:
            self.ax.set_ylim(y_lo, y_hi)

        if not is_hist:
            self._overlay_quadrant_preview(self.ax, x_ch, y_ch)
            if n_out:
                self.ax.scatter(
                    x_plot[out_of_range], y_plot[out_of_range], marker="x", s=45, color="red",
                    linewidths=1.8, zorder=6,
                )

        self.ax.set_xlabel(x_ch)
        self.ax.set_ylabel("Events / Bin" if is_hist else y_ch)
        node_name = self.gate_tree[self.current_node_id].name
        title_pop = f"{node_name}: {int(view_mask.sum())}/{len(df)} Events"
        if n_out:
            title_pop += f"  |  {n_out} außerhalb der Achsen-Grenzen" + ("" if is_hist else " (rot ×)")
        self.ax.set_title(f"{os.path.basename(self.current_path)}\n{title_pop}")
        self.canvas.draw_idle()
        self._update_breadcrumb()
        self._refresh_gate_tree_view(df)

    # ---- 2D density plots -------------------------------------------------
    # All 2D density views bin in DISPLAY space (after the Log/Logicle axis
    # transform), so each bin is the same size on screen — binning raw values
    # on a log axis gives giant smeared cells left and invisible ones right.
    # Colors use a LOG scale: with a linear scale the debris/noise peak takes
    # the whole top of the colormap and every real population drowns in the
    # darkest color. Empty bins stay white so population borders are visible.
    def _colormap(self):
        name = COLORMAPS.get(self.colormap_var.get(), "turbo")
        base = matplotlib.colormaps[name]
        # Skip the near-black/near-white end so the LOWEST density is still a
        # clearly visible color (not mistaken for "dense" or for background).
        start = {"turbo": 0.12, "Blues": 0.25, "Greys": 0.25}.get(name, 0.0)
        cmap = ListedColormap(base(np.linspace(start, 1.0, 256)), name=f"{name}_trunc")
        cmap.set_bad("white")
        cmap.set_under("white")
        return cmap

    def _density_grid(self, x, y, x_ch, y_ch, value_range, bins=200, sigma=0.0):
        tx = self._display_transform(self.x_transform_var.get(), x_ch)
        ty = self._display_transform(self.y_transform_var.get(), y_ch)
        xt, yt = tx.forward(x), ty.forward(y)
        ok = np.isfinite(xt) & np.isfinite(yt)
        xt, yt = xt[ok], yt[ok]

        def rng(vals, tr, manual):
            if manual is not None:
                lo, hi = tr.forward(manual)
            elif len(vals):
                lo, hi = float(vals.min()), float(vals.max())
            else:
                lo, hi = 0.0, 1.0
            return (lo, hi) if hi > lo else (lo, lo + 1.0)

        H, xe_t, ye_t = np.histogram2d(
            xt, yt, bins=bins, range=[rng(xt, tx, value_range[0]), rng(yt, ty, value_range[1])]
        )
        if sigma > 0:
            H = gaussian_filter(H, sigma=sigma)
        return {
            "H": H, "xe": tx.inverse(xe_t), "ye": ty.inverse(ye_t),
            "xc": tx.inverse((xe_t[:-1] + xe_t[1:]) / 2), "yc": ty.inverse((ye_t[:-1] + ye_t[1:]) / 2),
            "xe_t": xe_t, "ye_t": ye_t, "xt": xt, "yt": yt, "ok": ok,
        }

    def _plot_pseudocolor(self, ax, x, y, x_ch, y_ch, value_range, sigma=0.0):
        g = self._density_grid(x, y, x_ch, y_ch, value_range, bins=200, sigma=sigma)
        H = g["H"]
        if not (H > 0).any():
            return
        # Smoothed: one isolated event peaks at ~1/(2πσ²) (≈0.04 for σ=2);
        # a floor above that keeps lone outliers from becoming big blobs.
        floor = 1.0 if sigma == 0 else max(2.5 / (2 * np.pi * sigma**2), float(H.max()) * 1e-4)
        masked = np.ma.masked_less(H, floor)
        ax.pcolormesh(
            g["xe"], g["ye"], masked.T, cmap=self._colormap(),
            norm=LogNorm(vmin=floor, vmax=max(float(H.max()), floor * 1.01)), shading="flat", rasterized=True,
        )

    CONTOUR_PROBABILITIES = (0.98, 0.95, 0.90, 0.75, 0.50, 0.25, 0.10)

    def _plot_contour(self, ax, x, y, x_ch, y_ch, value_range):
        """Equal-probability contours (like FlowJo): each line encloses the
        given share of the events, e.g. the '50%' line surrounds the densest
        half. Events outside the outermost line are drawn as grey dots so
        sparse populations don't silently vanish."""
        g = self._density_grid(x, y, x_ch, y_ch, value_range, bins=200, sigma=2.0)
        H = g["H"]
        total = float(H.sum())
        if total <= 0:
            return
        flat = np.sort(H.ravel())[::-1]
        cum = np.cumsum(flat) / total
        level_to_pct = {}
        for p in self.CONTOUR_PROBABILITIES:
            thr = float(flat[min(np.searchsorted(cum, p), len(flat) - 1)])
            if thr > 0 and thr not in level_to_pct:
                level_to_pct[thr] = p
        levels = sorted(level_to_pct)
        if not levels:
            return

        ix = np.clip(np.digitize(g["xt"], g["xe_t"]) - 1, 0, H.shape[0] - 1)
        iy = np.clip(np.digitize(g["yt"], g["ye_t"]) - 1, 0, H.shape[1] - 1)
        outlier = H[ix, iy] < levels[0]
        xs, ys = np.asarray(x)[g["ok"]], np.asarray(y)[g["ok"]]
        ax.scatter(xs[outlier], ys[outlier], s=1.5, color="#8a8a8a", alpha=0.6, linewidths=0, rasterized=True)

        if len(levels) == 1:
            cs = ax.contour(g["xc"], g["yc"], H.T, levels=levels, colors=["#1f4e8c"], linewidths=1.3)
        else:
            cs = ax.contour(  # floor avoids LogNorm's "z <= 0 masked" warning; below levels[0] anyway
                g["xc"], g["yc"], np.maximum(H, levels[0] * 1e-3).T, levels=levels, cmap=self._colormap(),
                norm=LogNorm(vmin=levels[0], vmax=levels[-1]), linewidths=1.3,
            )
        ax.clabel(cs, fmt={lv: f"{level_to_pct[lv] * 100:.0f}%" for lv in levels}, fontsize=7, inline=True)

    def _plot_histogram_1d(self, df, view_mask, x_raw, x_ch, lo, hi) -> int:
        """Intensity (X channel) vs. number of events, as lines: the current
        population filled in grey, every child gate's sub-population as a
        line in that gate's color (same color as its outline in 2D plots).
        Returns the number of events outside the manual X limits."""
        node = self.gate_tree[self.current_node_id]
        series = [OverlaySeries(node.name, x_raw[view_mask & np.isfinite(x_raw)], "#555555")]
        n_parent = max(int(view_mask.sum()), 1)
        for i, child_id in enumerate(node.children):
            child = self.gate_tree[child_id]
            m = view_mask & child.step.mask(df) & np.isfinite(x_raw)
            series.append(OverlaySeries(
                f"{child.name}  ({int(m.sum())}, {100.0 * m.sum() / n_parent:.1f}%)", x_raw[m], default_color(i)
            ))
        tr = self._display_transform(self.x_transform_var.get(), x_ch)
        result = compute_overlay(series, tr, n_bins=256, lo=lo, hi=hi, normalization=NORM_COUNT, smooth_sigma=1.0)
        x = result.centers_raw
        parent_y = result.curves[0]
        self.ax.fill_between(x, 0, parent_y, color="#bbbbbb", alpha=0.5, linewidth=0)
        self.ax.plot(x, parent_y, color="#333333", linewidth=1.5, label=f"{node.name}  ({int(view_mask.sum())})")
        for s, y in zip(series[1:], result.curves[1:]):
            self.ax.plot(x, y, color=s.color, linewidth=1.6, label=s.label)
        y_max = max((float(c.max()) for c in result.curves if len(c)), default=1.0)
        self.ax.set_ylim(0, (y_max or 1.0) * 1.05)
        if len(x):
            self.ax.set_xlim(lo if lo is not None else x[0], hi if hi is not None else x[-1])
        self.ax.legend(fontsize=8, loc="upper right", framealpha=0.85)
        return result.n_out_of_range[0]

    def _channel_range(self, channel: str) -> float:
        fcs = self._get_data(self.current_path)
        r = (fcs.channel_ranges or {}).get(channel)
        if r:
            return r
        col = fcs.df[channel]
        return float(col.max()) if len(col) else 262144.0

    def _scale_params(self) -> dict:
        try:
            linthresh = float(self.log_linthresh_var.get())
        except ValueError:
            linthresh = 10.0
        try:
            logicle_w = float(self.logicle_w_var.get())
        except ValueError:
            logicle_w = 0.5
        try:
            logicle_m = float(self.logicle_m_var.get())
        except ValueError:
            logicle_m = 4.5
        return {"log_linthresh": linthresh, "logicle_w": logicle_w, "logicle_m": logicle_m}

    def _default_axis_range(self, channel: str, transform_name: str) -> tuple[float, float]:
        return default_axis_range(transform_name, channel_range=self._channel_range(channel), **self._scale_params())

    def _display_transform(self, transform_name: str, channel: str) -> DisplayTransform:
        return DisplayTransform(transform_name, channel_range=self._channel_range(channel), **self._scale_params())

    def _apply_axis_transforms(self, x_ch: str, y_ch: str, y_linear: bool = False):
        params = self._scale_params()
        apply_axis_scale(self.ax, "x", self.x_transform_var.get(), channel_range=self._channel_range(x_ch), **params)
        apply_axis_scale(
            self.ax, "y", "Linear" if y_linear else self.y_transform_var.get(),
            channel_range=self._channel_range(y_ch), **params,
        )

    @staticmethod
    def _shape_patch(step: GateStep, **kwargs):
        if step.shape in ("polygon", "rectangle"):
            return PolygonPatch(step.vertices, closed=True, fill=False, **kwargs)
        if step.shape == "ellipse":
            p = step.params
            return EllipsePatch((p["cx"], p["cy"]), width=2 * p["rx"], height=2 * p["ry"], fill=False, **kwargs)
        return None

    @staticmethod
    def _draw_quadrant_lines(ax, cx: float, cy: float, angle_deg: float, **kwargs):
        """Draw the two perpendicular dividing lines of a quadrant gate,
        crossing at (cx, cy) and rotated by angle_deg. Drawn far beyond the
        current view and clipped to it (matplotlib's default clip-on-axes
        behavior for line artists) so the lines always look like they span
        the whole plot; scalex/scaley=False keeps them from affecting
        autoscale/the current view themselves."""
        angle = math.radians(angle_deg)
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        span = math.hypot(xlim[1] - xlim[0], ylim[1] - ylim[0]) * 2 + 1e-9
        kwargs.setdefault("linewidth", 1.5)
        for dx, dy in ((math.sin(angle), -math.cos(angle)), (math.cos(angle), math.sin(angle))):
            ax.plot(
                [cx - dx * span, cx + dx * span], [cy - dy * span, cy + dy * span],
                scalex=False, scaley=False, **kwargs,
            )

    @staticmethod
    def _quadrant_label_position(ax, params: dict) -> tuple[float, float]:
        """Approximate label position inside the given quadrant: offset from
        the center towards that quadrant, normalized by the current axis
        span per-axis so it lands sensibly even when X and Y have very
        different raw ranges (this is only for label placement, not gate
        math — the gate boundary itself always rotates in raw data units)."""
        cx, cy = params["cx"], params["cy"]
        angle = math.radians(params.get("angle_deg", 0.0))
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        x_span, y_span = (xlim[1] - xlim[0]) or 1.0, (ylim[1] - ylim[0]) or 1.0
        x_sign = 1.0 if params["x_sign"] == "+" else -1.0
        y_sign = 1.0 if params["y_sign"] == "+" else -1.0
        frac = 0.22
        off_x = x_sign * frac * math.cos(angle) - y_sign * frac * math.sin(angle)
        off_y = x_sign * frac * math.sin(angle) + y_sign * frac * math.cos(angle)
        return cx + off_x * x_span, cy + off_y * y_span

    def _overlay_child_shapes(self, ax, x_ch: str, y_ch: str):
        """Draw outlines of the current node's child gates that use this exact
        channel pair, as a visual reference on top of any plot type — also
        what you double-click on to navigate into that child population."""
        # Each child gate gets its own color (same index -> same color as its
        # line in the 1D histogram), with a white halo so outline and label
        # stay readable on top of any colormap.
        halo_line = [patheffects.withStroke(linewidth=3.5, foreground="white")]
        halo_text = [patheffects.withStroke(linewidth=2.5, foreground="white")]
        drawn_quadrant_centers = set()
        for i, child_id in enumerate(self.gate_tree[self.current_node_id].children):
            step = self.gate_tree[child_id].step
            color = default_color(i)
            label_kw = dict(ha="center", fontsize=8, fontweight="bold", path_effects=halo_text, zorder=8)
            if step.shape == "range":
                if step.x_channel == x_ch:
                    self._draw_range_gate(ax, step, self.gate_tree[child_id].name, color, i, halo_line, label_kw)
                continue
            if step.x_channel != x_ch or step.y_channel != y_ch:
                continue
            if step.shape == "quadrant":
                p = step.params
                key = (p["cx"], p["cy"], p.get("angle_deg", 0.0))
                if key not in drawn_quadrant_centers:
                    drawn_quadrant_centers.add(key)
                    self._draw_quadrant_lines(
                        ax, p["cx"], p["cy"], p.get("angle_deg", 0.0), color="black", path_effects=halo_line
                    )
                cx, cy = self._quadrant_label_position(ax, p)
                ax.annotate(self.gate_tree[child_id].name, (cx, cy), color=color, **label_kw)
                continue
            patch = self._shape_patch(step, edgecolor=color, linewidth=1.8, path_effects=halo_line, zorder=7)
            if patch is None:
                continue
            ax.add_patch(patch)
            if step.shape == "ellipse":
                cx, cy = step.params["cx"], step.params["cy"]
            else:
                cx, cy = patch.get_path().vertices.mean(axis=0)
            ax.annotate(self.gate_tree[child_id].name, (cx, cy), color=color, **label_kw)

    @staticmethod
    def _draw_range_gate(ax, step: GateStep, name: str, color, index: int, halo_line, label_kw):
        """1D range gate (e.g. imported FlowJo 'Comp-PE-A+'): its bounds as
        vertical lines, a bracket and the name a bit below the top — staggered
        per gate so neighbouring ranges don't print over each other."""
        lo, hi = step.params.get("min"), step.params.get("max")
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        for v in (lo, hi):
            if v is not None:
                ax.axvline(v, color=color, lw=1.6, path_effects=halo_line, zorder=7)
        x0 = xlim[0] if lo is None else max(lo, xlim[0])
        x1 = xlim[1] if hi is None else min(hi, xlim[1])
        frac = 0.92 - 0.06 * (index % 6)
        if ax.get_yscale() == "linear":
            y = ylim[0] + frac * (ylim[1] - ylim[0])
        else:  # log-like Y: place in display space
            y = ax.transData.inverted().transform(ax.transAxes.transform((0, frac)))[1]
        ax.plot([x0, x1], [y, y], color=color, lw=1.4, scalex=False, scaley=False, zorder=7)
        try:
            xm = ax.transData.inverted().transform(
                (np.mean([ax.transData.transform((x0, y))[0], ax.transData.transform((x1, y))[0]]), 0)
            )[0]
        except Exception:
            xm = (x0 + x1) / 2
        ax.annotate(name, (xm, y), color=color, va="bottom", **label_kw)

    @staticmethod
    def _step_on_axes(step: GateStep, x_ch: str, y_ch: str) -> bool:
        if step.shape == "range":
            return step.x_channel == x_ch
        return step.x_channel == x_ch and step.y_channel == y_ch

    def _overlay_quadrant_preview(self, ax, x_ch: str, y_ch: str):
        """While a quadrant gate is being placed: once its center is picked,
        keep showing the (dashed, orange) preview crosshair across redraws
        until 'Schritt übernehmen'/'Abbrechen' is clicked."""
        coords = self.pending_shape_coords
        if not coords or coords.get("shape") not in QUADRANT_SHAPES:
            return
        center = coords.get("center")
        if center is None or coords["x_channel"] != x_ch or coords["y_channel"] != y_ch:
            return
        angle_deg = self._quadrant_angle_value() if coords["shape"] == "Quadrant (Winkel)" else 0.0
        self._draw_quadrant_lines(ax, center[0], center[1], angle_deg, color="orange", linestyle="--", linewidth=1.5)
