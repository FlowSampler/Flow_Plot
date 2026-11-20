"""'Plot exportieren...' dialog: export any tab's matplotlib figure with a chosen
size, resolution, format, labels and font sizes — with a live preview.

The live figure is modified only temporarily: every property the export
touches is snapshotted first and restored afterwards (try/finally), so the
plot in the tab looks exactly as before once the dialog is done."""
from __future__ import annotations

import io
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.text import Text
from PIL import Image, ImageTk

from clipboard_utils import copy_image_to_windows_clipboard

CM_PER_INCH = 2.54

FORMATS = {
    "PNG": ".png",
    "TIFF": ".tif",
    "JPG": ".jpg",
    "SVG (Vektor)": ".svg",
    "PDF (Vektor)": ".pdf",
}

# label -> (width_cm, height_cm)
SIZE_PRESETS = {
    "Paper, 1 Spalte (8,5 × 7 cm)": (8.5, 7.0),
    "Paper, 1,5 Spalten (11,5 × 9 cm)": (11.5, 9.0),
    "Paper, 2 Spalten (17,5 × 10 cm)": (17.5, 10.0),
    "Folie, halbe Breite (15 × 11 cm)": (15.0, 11.0),
    "Folie, volle Breite (30 × 15 cm)": (30.0, 15.0),
    "Quadratisch (10 × 10 cm)": (10.0, 10.0),
}

FONT_FAMILIES = ["(wie im Programm)", "Arial", "Helvetica", "Calibri", "DejaVu Sans", "Times New Roman"]

LEGEND_LOCS = ["(wie im Programm)", "best", "upper right", "upper left", "lower right", "lower left",
               "center right", "center left", "upper center", "lower center", "center"]

DEFAULTS = {
    "width": "12", "height": "9", "unit": "cm", "dpi": "300", "format": "PNG", "transparent": False,
    "show_title": True, "show_xlabel": True, "show_ylabel": True, "show_ticklabels": True,
    "show_legend": True, "legend_loc": LEGEND_LOCS[0], "show_texts": True,
    "despine": False, "grid": False,
    "fs_title": "12", "fs_label": "10", "fs_ticks": "8", "fs_legend": "8", "fs_texts": "",
    "font_family": FONT_FAMILIES[0],
}

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".fcs_alternative", "plot_export.json")


def _load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        s.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return s


def _save_settings(s: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1)
    except OSError:
        pass  # not remembered — no harm


def _float_or_none(s) -> float | None:
    try:
        v = float(str(s).replace(",", ".").strip())
    except ValueError:
        return None
    return v if v > 0 else None


class _FigureState:
    """Snapshot of everything apply_export_options() may change, so it can be
    put back exactly."""

    def __init__(self, fig):
        self.fig = fig
        self.size = tuple(fig.get_size_inches())
        sp = fig.subplotpars
        self.subplotpars = dict(left=sp.left, right=sp.right, bottom=sp.bottom, top=sp.top,
                                wspace=sp.wspace, hspace=sp.hspace)
        self.layout_engine = fig.get_layout_engine()
        # positions matter for axes placed manually (colorbars etc.)
        self.positions = [(ax, ax.get_position(original=True)) for ax in fig.axes]
        self.texts = [(t, t.get_text(), t.get_visible(), t.get_fontsize(), t.get_fontproperties().copy())
                      for t in fig.findobj(Text)]
        self.axes = []
        for ax in fig.axes:
            xt = ax.xaxis.get_major_ticks()
            yt = ax.yaxis.get_major_ticks()
            legend = ax.get_legend()
            self.axes.append(dict(
                ax=ax,
                xtick_size=xt[0].label1.get_fontsize() if xt else None,
                ytick_size=yt[0].label1.get_fontsize() if yt else None,
                xtick_on=xt[0].label1.get_visible() if xt else True,
                ytick_on=yt[0].label1.get_visible() if yt else True,
                xtick_font=xt[0].label1.get_fontproperties().copy() if xt else None,
                ytick_font=yt[0].label1.get_fontproperties().copy() if yt else None,
                spines={k: s.get_visible() for k, s in ax.spines.items()},
                xgrid=bool(ax.xaxis.get_gridlines()) and ax.xaxis.get_gridlines()[0].get_visible(),
                ygrid=bool(ax.yaxis.get_gridlines()) and ax.yaxis.get_gridlines()[0].get_visible(),
                legend=legend,
                legend_visible=legend.get_visible() if legend else None,
                legend_loc=legend._loc if legend else None,
            ))

    def restore(self):
        fig = self.fig
        fig.set_size_inches(*self.size, forward=False)
        for t, text, vis, size, fp in self.texts:
            t.set_text(text)
            t.set_visible(vis)
            t.set_fontproperties(fp)
            t.set_fontsize(size)
        for a in self.axes:
            ax = a["ax"]
            if a["xtick_size"] is not None:
                ax.tick_params(axis="x", which="both", labelsize=a["xtick_size"], labelbottom=a["xtick_on"])
            if a["ytick_size"] is not None:
                ax.tick_params(axis="y", which="both", labelsize=a["ytick_size"], labelleft=a["ytick_on"])
            for tick in ax.xaxis.get_major_ticks() + ax.xaxis.get_minor_ticks():
                if a["xtick_font"] is not None:
                    tick.label1.set_fontproperties(a["xtick_font"])
                    tick.label1.set_fontsize(a["xtick_size"])
            for tick in ax.yaxis.get_major_ticks() + ax.yaxis.get_minor_ticks():
                if a["ytick_font"] is not None:
                    tick.label1.set_fontproperties(a["ytick_font"])
                    tick.label1.set_fontsize(a["ytick_size"])
            for k, vis in a["spines"].items():
                ax.spines[k].set_visible(vis)
            ax.xaxis.grid(a["xgrid"])
            ax.yaxis.grid(a["ygrid"])
            if a["legend"] is not None:
                a["legend"].set_visible(a["legend_visible"])
                a["legend"]._loc = a["legend_loc"]
        fig.set_layout_engine(self.layout_engine)
        fig.subplots_adjust(**self.subplotpars)
        for ax, pos in self.positions:
            # private setter: the public set_position() would also take the
            # axes out of future tight_layout() calls of the tab
            ax._set_position(pos, which="both")


def apply_export_options(fig, opts: dict) -> None:
    """Apply size/labels/fonts to the (snapshotted) figure in place."""
    w = _float_or_none(opts["width"]) or 12.0
    h = _float_or_none(opts["height"]) or 9.0
    if opts["unit"] == "cm":
        w, h = w / CM_PER_INCH, h / CM_PER_INCH
    fig.set_size_inches(w, h, forward=False)

    fs_title = _float_or_none(opts["fs_title"])
    fs_label = _float_or_none(opts["fs_label"])
    fs_ticks = _float_or_none(opts["fs_ticks"])
    fs_legend = _float_or_none(opts["fs_legend"])
    fs_texts = _float_or_none(opts["fs_texts"])
    family = opts["font_family"] if opts["font_family"] != FONT_FAMILIES[0] else None

    # texts that belong to axes decorations — everything else in ax.texts is
    # in-plot annotation (gate names, percentages, ...)
    for i, ax in enumerate(fig.axes):
        main = i == 0
        title = ax.title
        if main:
            title.set_text(opts.get("title_text", title.get_text()))
            ax.xaxis.label.set_text(opts.get("xlabel_text", ax.xaxis.label.get_text()))
            ax.yaxis.label.set_text(opts.get("ylabel_text", ax.yaxis.label.get_text()))
        for t in (title, ax._left_title, ax._right_title):
            t.set_visible(opts["show_title"] and t.get_visible())
            if fs_title:
                t.set_fontsize(fs_title)
        ax.xaxis.label.set_visible(opts["show_xlabel"])
        ax.yaxis.label.set_visible(opts["show_ylabel"])
        if fs_label:
            ax.xaxis.label.set_fontsize(fs_label)
            ax.yaxis.label.set_fontsize(fs_label)

        tick_kw = {}
        if fs_ticks:
            tick_kw["labelsize"] = fs_ticks
        if not opts["show_ticklabels"]:
            tick_kw.update(labelbottom=False, labelleft=False)
        if tick_kw:
            ax.tick_params(axis="both", which="both", **tick_kw)

        legend = ax.get_legend()
        if legend is not None:
            legend.set_visible(opts["show_legend"])
            if opts["legend_loc"] != LEGEND_LOCS[0]:
                legend.set_loc(opts["legend_loc"])
            if fs_legend:
                for t in legend.get_texts():
                    t.set_fontsize(fs_legend)
                legend.get_title().set_fontsize(fs_legend)

        for t in ax.texts:
            if not opts["show_texts"]:
                t.set_visible(False)
            elif fs_texts:
                t.set_fontsize(fs_texts)

        if opts["despine"]:
            for k in ("top", "right"):
                if k in ax.spines:
                    ax.spines[k].set_visible(False)
        if opts["grid"]:
            ax.grid(True, alpha=0.3, linewidth=0.6)

    if family:
        for t in fig.findobj(Text):
            t.set_fontfamily(family)
        for ax in fig.axes:
            for tick in ax.xaxis.get_major_ticks() + ax.yaxis.get_major_ticks():
                tick.label1.set_fontfamily(family)

    # re-fit the margins to the new size/labels so nothing is cut off and the
    # file has EXACTLY the requested dimensions (no bbox_inches="tight")
    try:
        fig.set_layout_engine("tight")
        fig.get_layout_engine().execute(fig)
    except Exception:
        pass
    fig.set_layout_engine(None)


def render_figure(fig, opts: dict, fmt_ext: str, dpi: float, target, transparent: bool = False) -> None:
    """Snapshot -> apply -> savefig -> restore."""
    state = _FigureState(fig)
    try:
        apply_export_options(fig, opts)
        save_kw = dict(dpi=dpi, transparent=transparent)
        if fmt_ext == ".jpg":
            save_kw.pop("transparent")
            save_kw["pil_kwargs"] = {"quality": 95}
        if isinstance(target, str):
            fig.savefig(target, **save_kw)
        else:
            fig.savefig(target, format=fmt_ext.lstrip("."), **save_kw)
    finally:
        state.restore()


class PlotExportDialog(tk.Toplevel):
    PREVIEW_W, PREVIEW_H = 520, 440

    def __init__(self, master, figure, canvas=None, initialdir: str | None = None,
                 default_name: str = "plot", status_var: tk.StringVar | None = None):
        super().__init__(master)
        self.title("Plot exportieren")
        self.transient(master.winfo_toplevel())
        self.figure = figure
        self.canvas = canvas
        self.initialdir = initialdir
        self.default_name = default_name
        self.status_var = status_var
        self._preview_job = None
        self._preview_img = None

        s = _load_settings()
        self.vars: dict[str, tk.Variable] = {}
        for k, v in s.items():
            self.vars[k] = tk.BooleanVar(value=v) if isinstance(DEFAULTS[k], bool) else tk.StringVar(value=str(v))

        main_ax = figure.axes[0] if figure.axes else None
        self.title_var = tk.StringVar(value=main_ax.get_title() if main_ax else "")
        self.xlabel_var = tk.StringVar(value=main_ax.get_xlabel() if main_ax else "")
        self.ylabel_var = tk.StringVar(value=main_ax.get_ylabel() if main_ax else "")
        self.has_legend = any(ax.get_legend() is not None for ax in figure.axes)
        self.has_texts = any(ax.texts for ax in figure.axes)

        self._build()
        for var in list(self.vars.values()) + [self.title_var, self.xlabel_var, self.ylabel_var]:
            var.trace_add("write", lambda *_: self._schedule_preview())
        self._update_px_info()
        self._schedule_preview(delay=50)
        self.bind("<Escape>", lambda _e: self.destroy())

    # ---------------- UI ----------------
    def _build(self):
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)
        left = ttk.Frame(outer)
        left.pack(side="left", fill="y")
        right = ttk.LabelFrame(outer, text="Vorschau (Seitenverhältnis & Proportionen wie im Export)")
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        v = self.vars

        # --- size & format ---
        size = ttk.LabelFrame(left, text="Größe & Format", padding=6)
        size.pack(fill="x")
        ttk.Label(size, text="Vorlage:").grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar(value="")
        preset = ttk.Combobox(size, textvariable=self.preset_var, state="readonly", width=32,
                              values=list(SIZE_PRESETS) + ["Wie auf dem Bildschirm"])
        preset.grid(row=0, column=1, columnspan=4, sticky="we", pady=2)
        preset.bind("<<ComboboxSelected>>", self._apply_preset)

        ttk.Label(size, text="Breite (X):").grid(row=1, column=0, sticky="w")
        ttk.Entry(size, textvariable=v["width"], width=7).grid(row=1, column=1, sticky="w")
        ttk.Label(size, text="Höhe (Y):").grid(row=1, column=2, sticky="e", padx=(6, 2))
        ttk.Entry(size, textvariable=v["height"], width=7).grid(row=1, column=3, sticky="w")
        unit = ttk.Combobox(size, textvariable=v["unit"], values=["cm", "inch"], state="readonly", width=5)
        unit.grid(row=1, column=4, padx=(4, 0))
        unit.bind("<<ComboboxSelected>>", self._convert_unit)
        self._last_unit = v["unit"].get()

        ttk.Label(size, text="Auflösung (DPI):").grid(row=2, column=0, sticky="w")
        ttk.Combobox(size, textvariable=v["dpi"], values=["150", "300", "600", "1200"], width=6).grid(
            row=2, column=1, sticky="w", pady=2)
        ttk.Label(size, text="Format:").grid(row=2, column=2, sticky="e", padx=(6, 2))
        ttk.Combobox(size, textvariable=v["format"], values=list(FORMATS), state="readonly", width=13).grid(
            row=2, column=3, columnspan=2, sticky="w")
        ttk.Checkbutton(size, text="Transparenter Hintergrund", variable=v["transparent"]).grid(
            row=3, column=0, columnspan=3, sticky="w")
        self.px_info = tk.StringVar()
        ttk.Label(size, textvariable=self.px_info, foreground="#555").grid(row=4, column=0, columnspan=5, sticky="w")
        for k in ("width", "height", "unit", "dpi", "format"):
            v[k].trace_add("write", lambda *_: self._update_px_info())

        # --- labels ---
        lab = ttk.LabelFrame(left, text="Beschriftungen (was soll enthalten sein?)", padding=6)
        lab.pack(fill="x", pady=(8, 0))
        rows = [
            ("Titel", v["show_title"], self.title_var),
            ("X-Achse", v["show_xlabel"], self.xlabel_var),
            ("Y-Achse", v["show_ylabel"], self.ylabel_var),
        ]
        for r, (name, show, text) in enumerate(rows):
            ttk.Checkbutton(lab, text=name, variable=show).grid(row=r, column=0, sticky="w")
            ttk.Entry(lab, textvariable=text, width=34).grid(row=r, column=1, columnspan=3, sticky="we", pady=1)
        ttk.Checkbutton(lab, text="Achsenzahlen (Tick-Beschriftungen)", variable=v["show_ticklabels"]).grid(
            row=3, column=0, columnspan=4, sticky="w")
        leg_cb = ttk.Checkbutton(lab, text="Legende", variable=v["show_legend"])
        leg_cb.grid(row=4, column=0, sticky="w")
        ttk.Label(lab, text="Position:").grid(row=4, column=1, sticky="e")
        leg_loc = ttk.Combobox(lab, textvariable=v["legend_loc"], values=LEGEND_LOCS, state="readonly", width=18)
        leg_loc.grid(row=4, column=2, columnspan=2, sticky="w")
        if not self.has_legend:
            leg_cb.state(["disabled"])
            leg_loc.state(["disabled"])
        txt_cb = ttk.Checkbutton(lab, text="Texte im Plot (Gate-Namen, Prozente, ...)", variable=v["show_texts"])
        txt_cb.grid(row=5, column=0, columnspan=4, sticky="w")
        if not self.has_texts:
            txt_cb.state(["disabled"])
        ttk.Checkbutton(lab, text="Rahmen oben/rechts entfernen", variable=v["despine"]).grid(
            row=6, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(lab, text="Gitternetz", variable=v["grid"]).grid(row=6, column=2, columnspan=2, sticky="w")
        lab.columnconfigure(1, weight=1)

        # --- fonts ---
        fonts = ttk.LabelFrame(left, text="Schrift (Größe in pt, leer = wie im Programm)", padding=6)
        fonts.pack(fill="x", pady=(8, 0))
        font_rows = [("Titel:", "fs_title"), ("Achsentitel:", "fs_label"), ("Achsenzahlen:", "fs_ticks"),
                     ("Legende:", "fs_legend"), ("Texte im Plot:", "fs_texts")]
        for i, (name, key) in enumerate(font_rows):
            r, c = divmod(i, 2)
            ttk.Label(fonts, text=name).grid(row=r, column=c * 2, sticky="w", padx=(0 if c == 0 else 10, 2))
            ttk.Spinbox(fonts, textvariable=v[key], from_=4, to=40, increment=1, width=5).grid(
                row=r, column=c * 2 + 1, sticky="w", pady=1)
        ttk.Label(fonts, text="Schriftart:").grid(row=3, column=0, sticky="w")
        ttk.Combobox(fonts, textvariable=v["font_family"], values=FONT_FAMILIES, width=20).grid(
            row=3, column=1, columnspan=3, sticky="w", pady=(2, 0))

        # --- buttons ---
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Speichern unter...", command=self._save).pack(side="left")
        ttk.Button(btns, text="In Zwischenablage", command=self._copy).pack(side="left", padx=4)
        ttk.Button(btns, text="Standard", command=self._reset_defaults).pack(side="left")
        ttk.Button(btns, text="Schließen", command=self.destroy).pack(side="right")
        ttk.Label(left, text="Der Plot im Programm bleibt unverändert; die Einstellungen werden gemerkt.",
                  foreground="#555", wraplength=380).pack(anchor="w", pady=(6, 0))

        # a (blank) image makes width/height count in pixels instead of characters
        self._blank = tk.PhotoImage(width=1, height=1)
        self.preview_label = tk.Label(right, bg="#d9d9d9", image=self._blank,
                                      width=self.PREVIEW_W, height=self.PREVIEW_H)
        self.preview_label.pack(padx=4, pady=4)
        self.preview_info = tk.StringVar()
        ttk.Label(right, textvariable=self.preview_info, foreground="#555").pack(anchor="w", padx=4)

    # ---------------- helpers ----------------
    def _opts(self) -> dict:
        o = {k: var.get() for k, var in self.vars.items()}
        o["title_text"] = self.title_var.get()
        o["xlabel_text"] = self.xlabel_var.get()
        o["ylabel_text"] = self.ylabel_var.get()
        return o

    def _size_inches(self) -> tuple[float, float]:
        w = _float_or_none(self.vars["width"].get()) or 12.0
        h = _float_or_none(self.vars["height"].get()) or 9.0
        if self.vars["unit"].get() == "cm":
            return w / CM_PER_INCH, h / CM_PER_INCH
        return w, h

    def _dpi(self) -> float:
        return _float_or_none(self.vars["dpi"].get()) or 300.0

    def _update_px_info(self):
        w, h = self._size_inches()
        ext = FORMATS.get(self.vars["format"].get(), ".png")
        if ext in (".svg", ".pdf"):
            self.px_info.set(f"Vektorgrafik: {w * CM_PER_INCH:.1f} × {h * CM_PER_INCH:.1f} cm (DPI nur für Rasteranteile)")
        else:
            d = self._dpi()
            self.px_info.set(f"= {round(w * d)} × {round(h * d)} Pixel")

    def _apply_preset(self, _e=None):
        name = self.preset_var.get()
        if name == "Wie auf dem Bildschirm":
            w_in, h_in = self.figure.get_size_inches()
            w_cm, h_cm = w_in * CM_PER_INCH, h_in * CM_PER_INCH
        else:
            w_cm, h_cm = SIZE_PRESETS[name]
        if self.vars["unit"].get() == "cm":
            self.vars["width"].set(f"{w_cm:.1f}")
            self.vars["height"].set(f"{h_cm:.1f}")
        else:
            self.vars["width"].set(f"{w_cm / CM_PER_INCH:.2f}")
            self.vars["height"].set(f"{h_cm / CM_PER_INCH:.2f}")

    def _convert_unit(self, _e=None):
        new = self.vars["unit"].get()
        if new == self._last_unit:
            return
        factor = 1 / CM_PER_INCH if new == "inch" else CM_PER_INCH
        for k in ("width", "height"):
            val = _float_or_none(self.vars[k].get())
            if val:
                self.vars[k].set(f"{val * factor:.2f}" if new == "inch" else f"{val * factor:.1f}")
        self._last_unit = new

    def _reset_defaults(self):
        for k, val in DEFAULTS.items():
            self.vars[k].set(val)
        self._last_unit = self.vars["unit"].get()

    def _remember(self):
        _save_settings({k: var.get() for k, var in self.vars.items()})

    # ---------------- preview ----------------
    def _schedule_preview(self, delay: int = 300):
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(delay, self._render_preview)

    def _render_preview(self):
        self._preview_job = None
        if not self.winfo_exists():
            return
        w_in, h_in = self._size_inches()
        # same layout as the export, just rendered at a screen-sized DPI
        box_w, box_h = self.PREVIEW_W - 8, self.PREVIEW_H - 8
        dpi = max(min(box_w / w_in, box_h / h_in), 10)
        buf = io.BytesIO()
        try:
            render_figure(self.figure, self._opts(), ".png", dpi, buf)
        except Exception as exc:
            self.preview_info.set(f"Vorschau fehlgeschlagen: {exc}")
            return
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
        self._preview_img = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=self._preview_img)
        self.preview_info.set(
            f"Exportgröße {w_in * CM_PER_INCH:.1f} × {h_in * CM_PER_INCH:.1f} cm — Vorschau verkleinert dargestellt")

    # ---------------- actions ----------------
    def _save(self):
        fmt = self.vars["format"].get()
        ext = FORMATS.get(fmt, ".png")
        path = filedialog.asksaveasfilename(
            parent=self, title="Plot speichern", defaultextension=ext,
            filetypes=[(fmt, f"*{ext}")] + [(k, f"*{e}") for k, e in FORMATS.items() if e != ext],
            initialdir=self.initialdir, initialfile=f"{self.default_name}{ext}",
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower() or ext
        if ext == ".tiff":
            ext = ".tif"
        if ext not in FORMATS.values():
            messagebox.showerror("Format", f"Unbekannte Dateiendung '{ext}'.", parent=self)
            return
        try:
            render_figure(self.figure, self._opts(), ext, self._dpi(), path,
                          transparent=self.vars["transparent"].get())
        except Exception as exc:
            messagebox.showerror("Export fehlgeschlagen", str(exc), parent=self)
            return
        finally:
            self._redraw_live()
        self._remember()
        self.initialdir = os.path.dirname(path)
        msg = f"Plot gespeichert: {path}"
        if self.status_var is not None:
            self.status_var.set(msg)
        self.preview_info.set(msg)

    def _copy(self):
        buf = io.BytesIO()
        try:
            render_figure(self.figure, self._opts(), ".png", self._dpi(), buf)
            buf.seek(0)
            copy_image_to_windows_clipboard(Image.open(buf).convert("RGB"))
        except Exception as exc:
            messagebox.showerror("Zwischenablage nicht verfügbar", str(exc), parent=self)
            return
        finally:
            self._redraw_live()
        self._remember()
        msg = "Plot (mit diesen Einstellungen) in die Zwischenablage kopiert — mit Strg+V einfügbar."
        if self.status_var is not None:
            self.status_var.set(msg)
        self.preview_info.set(msg)

    def _redraw_live(self):
        if self.canvas is not None:
            try:
                self.canvas.draw_idle()
            except Exception:
                pass

    def destroy(self):
        try:
            self._remember()
        except Exception:
            pass
        self._redraw_live()
        super().destroy()
