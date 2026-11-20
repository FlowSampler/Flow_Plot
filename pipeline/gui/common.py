"""Small helpers shared by several tabs."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox

import numpy as np

from clipboard_utils import copy_figure_to_clipboard
from histogram_overlay import DisplayTransform


class CommonMixin:
    """Widget visibility, number parsing, display transforms, clipboard."""

    @staticmethod
    def _set_shown(widgets, shown: bool):
        """Show or hide grid-managed widgets. grid_remove() remembers each
        widget's grid options, so a bare grid() puts it back in place."""
        for w in widgets:
            if shown:
                w.grid()
            else:
                w.grid_remove()

    @staticmethod
    def _parse_optional_float(s: str) -> float | None:
        s = s.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _make_display_transform(self, transform_name: str, channel: str | None, value_arrays) -> DisplayTransform:
        """Axis transform with the Gating tab's Log/Logicle parameters.
        ``channel`` = raw channel (its instrument range sets the Logicle
        top-of-scale) or None for a ratio (data maximum is used instead)."""
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
        # Logicle top-of-scale: the channel's declared range when plotting a
        # raw channel; for a ratio (no instrument range) the data maximum.
        channel_range = None
        if channel and self.current_path:
            channel_range = (self._get_data(self.current_path).channel_ranges or {}).get(channel)
        if not channel_range:
            channel_range = max((float(np.max(np.abs(a))) for a in value_arrays if len(a)), default=262144.0)
        return DisplayTransform(
            transform_name, channel_range=channel_range,
            log_linthresh=linthresh, logicle_w=logicle_w, logicle_m=logicle_m,
        )

    def _open_plot_export(self, figure, canvas=None, suffix: str = "plot",
                          status_var: tk.StringVar | None = None):
        """'Plot exportieren...' dialog (size, DPI, format, labels, fonts)."""
        from gui.plot_export_dialog import PlotExportDialog

        if not figure.axes:
            messagebox.showinfo("Plot exportieren", "Es ist noch kein Plot zu sehen.")
            return
        stem = os.path.splitext(os.path.basename(self.current_path))[0] if self.current_path else ""
        name = f"{stem}_{suffix}" if stem else suffix
        try:
            initialdir = self._ensure_exports_dir()
        except OSError:
            initialdir = None
        PlotExportDialog(self, figure, canvas=canvas, initialdir=initialdir,
                         default_name=name, status_var=status_var)

    def _copy_to_clipboard(self, figure, status_var: tk.StringVar | None = None):
        try:
            copy_figure_to_clipboard(figure)
        except Exception as exc:
            messagebox.showerror(
                "Zwischenablage nicht verfügbar",
                f"Konnte den Plot nicht in die Zwischenablage kopieren:\n{exc}"
            )
            return
        message = "Plot in die Zwischenablage kopiert — mit Strg+V einfügbar."
        if status_var is not None:
            status_var.set(message)
        else:
            messagebox.showinfo("Kopiert", message)
