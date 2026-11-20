"""FCS-Inspector: shows the metadata (TEXT segment) of an FCS file — acquisition
info, instrument/CST setup, per-channel detector voltages, lasers, the stored
spillover matrix, every raw keyword — and compares settings across all files
of the experiment."""
from __future__ import annotations

import csv
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from fcs_meta import FcsMetadata, FcsMetaError, _format_duration, _format_param_value, load_metadata

ACCENT = "#1f4e8c"
ACCENT_LIGHT = "#dbe6f4"
CARD_BG = "#ffffff"
CARD_BORDER = "#d6dde8"
PAGE_BG = "#f3f5f9"
MUTED = "#5b6573"
FAINT = "#9aa3ae"
HIGHLIGHT = "#e8833a"
DIFF_BG = "#fde2e1"
ODD_BG = "#f6f8fb"
FONT = "Segoe UI"

# keywords that naturally differ from file to file — left out of the comparison
_VOLATILE = {
    "BEGINDATA", "ENDDATA", "BEGINSTEXT", "ENDSTEXT", "BEGINANALYSIS", "ENDANALYSIS", "NEXTDATA",
    "FIL", "GUID", "ORIGINALGUID", "BTIM", "ETIM", "TOT", "EXPORT TIME", "LAST_MODIFIED", "LAST_MODIFIER",
    "SPILL", "SPILLOVER", "SPILLOVERMATRIX", "COMP", "TUBE NAME", "SRC", "WELL ID", "SAMPLE ID",
}

_COMPARE_MODES = {
    "Spannung ($PnV)": "V",
    "Gain ($PnG)": "G",
    "Marker / Farbstoff ($PnS)": "S",
    "Bereich ($PnR)": "R",
    "Verstärkung lin/log ($PnE)": "E",
    "Allgemeine Keywords (Gerät, Setup, CST, Laser ...)": None,
}


# detector naming of spectral cytometers (Cytek: UV1-A, V3-A, B2-A, YG5-A, R1-A ...) -> laser
_LASER_COLORS = {"UV": "#6a3d9a", "V": "#3f51b5", "B": "#1f8fd6", "YG": "#8fae1b", "G": "#2e9e5b",
                 "Y": "#d9a400", "R": "#c0392b", "IR": "#7f2a1d"}
_LASER_NAMES = {"UV": "UV 355", "V": "Violett 405", "B": "Blau 488", "YG": "Gelb-grün 561", "G": "Grün 532",
                "Y": "Gelb 561", "R": "Rot 640", "IR": "IR 808"}
_DETECTOR_RE = re.compile(r"^(UV|IR|YG|V|B|G|Y|R)\d+-[AHW]$")


def _laser_group(channel: str) -> str | None:
    m = _DETECTOR_RE.match(channel)
    return m.group(1) if m else None


class MetadataInspectorMixin:
    """Opens the FCS-Inspector window; it follows the file selected on the left."""

    def _metadata(self, path: str) -> FcsMetadata:
        """Parsed TEXT segment of a file, cached until the file changes."""
        cache = self.__dict__.setdefault("_meta_cache", {})
        mtime = os.path.getmtime(path)
        hit = cache.get(path)
        if hit is None or hit[0] != mtime:
            hit = cache[path] = (mtime, load_metadata(path))
        return hit[1]

    def _open_metadata_inspector(self, _event=None):
        if not self.fcs_paths:
            messagebox.showinfo("Keine Datei", "Bitte zuerst einen Experiment-Ordner mit .fcs-Dateien laden.")
            return
        existing = getattr(self, "_meta_inspector", None)
        if existing is not None and existing.win.winfo_exists():
            existing.show(self.current_path)
            existing.win.lift()
            existing.win.focus_force()
            return
        self._meta_inspector = _InspectorWindow(self)

    def _sync_metadata_inspector(self):
        insp = getattr(self, "_meta_inspector", None)
        if insp is not None and insp.win.winfo_exists() and insp.follow_var.get():
            insp.refresh_file_list()
            insp.show(self.current_path)


class _InspectorWindow:
    def __init__(self, app):
        self.app = app
        self.meta: FcsMetadata | None = None
        self._compare_dirty = True
        self._selected_channel: str | None = None

        win = self.win = tk.Toplevel(app)
        win.title("FCS-Inspector — Metadaten")
        win.geometry("1180x800")
        win.minsize(860, 560)
        win.configure(bg=PAGE_BG)
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._init_styles()

        # ---- top bar: file chooser
        bar = ttk.Frame(win, padding=(10, 8, 10, 4), style="Page.TFrame")
        bar.pack(fill="x")
        ttk.Label(bar, text="Datei:", style="Page.TLabel").pack(side="left")
        ttk.Button(bar, text="◀", width=3, command=lambda: self._step(-1)).pack(side="left", padx=(6, 0))
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(bar, textvariable=self.file_var, state="readonly", width=70)
        self.file_combo.pack(side="left", padx=4)
        self.file_combo.bind("<<ComboboxSelected>>", self._on_file_combo)
        ttk.Button(bar, text="▶", width=3, command=lambda: self._step(1)).pack(side="left")
        self.follow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="folgt der Dateiauswahl im Hauptfenster", variable=self.follow_var,
                        style="Page.TCheckbutton").pack(side="left", padx=12)
        ttk.Button(bar, text="Datei öffnen...", command=self._open_external).pack(side="right")

        # ---- header card
        self.header = tk.Frame(win, bg=ACCENT, padx=16, pady=12)
        self.header.pack(fill="x", padx=10, pady=(4, 0))
        self.title_lbl = tk.Label(self.header, bg=ACCENT, fg="white", font=(FONT, 15, "bold"), anchor="w")
        self.title_lbl.pack(fill="x")
        self.subtitle_lbl = tk.Label(self.header, bg=ACCENT, fg="#c9d8ee", font=(FONT, 10), anchor="w")
        self.subtitle_lbl.pack(fill="x")
        self.chips = tk.Frame(win, bg=PAGE_BG)
        self.chips.pack(fill="x", padx=10, pady=8)

        # ---- tabs
        self.nb = ttk.Notebook(win)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.tab_overview = ttk.Frame(self.nb)
        self.tab_channels = ttk.Frame(self.nb)
        self.tab_spill = ttk.Frame(self.nb)
        self.tab_keywords = ttk.Frame(self.nb)
        self.tab_compare = ttk.Frame(self.nb)
        self.nb.add(self.tab_overview, text="  Übersicht  ")
        self.nb.add(self.tab_channels, text="  Kanäle & Spannungen  ")
        self.nb.add(self.tab_spill, text="  Spillover-Matrix  ")
        self.nb.add(self.tab_keywords, text="  Alle Keywords  ")
        self.nb.add(self.tab_compare, text="  Vergleich aller Dateien  ")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_overview_tab()
        self._build_channels_tab()
        self._build_spill_tab()
        self._build_keywords_tab()
        self._build_compare_tab()

        self.status_var = tk.StringVar()
        ttk.Label(win, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(
            fill="x", padx=12, pady=(0, 6))

        self.refresh_file_list()
        self.show(app.current_path)

    # ------------------------------------------------------------------ setup
    def _init_styles(self):
        st = ttk.Style(self.win)
        st.configure("Page.TFrame", background=PAGE_BG)
        st.configure("Page.TLabel", background=PAGE_BG)
        st.configure("Page.TCheckbutton", background=PAGE_BG)
        st.configure("Status.TLabel", background=PAGE_BG, foreground=MUTED)
        st.configure("Inspector.Treeview", rowheight=22, font=(FONT, 9))
        st.configure("Inspector.Treeview.Heading", font=(FONT, 9, "bold"))

    def _make_tree(self, parent, columns, show="headings", height=None) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show=show, style="Inspector.Treeview",
                            **({"height": height} if height else {}))
        vs = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.tag_configure("odd", background=ODD_BG)
        tree.tag_configure("diff", background=DIFF_BG)
        tree.tag_configure("muted", foreground=FAINT)
        return tree

    # ------------------------------------------------------------- navigation
    def refresh_file_list(self):
        self._paths = list(self.app.fcs_paths)
        base = self.app.input_dir or ""
        self._labels = [os.path.relpath(p, base) if base else os.path.basename(p) for p in self._paths]
        self.file_combo["values"] = self._labels
        self._compare_dirty = True

    def _on_file_combo(self, _e=None):
        i = self.file_combo.current()
        if i >= 0:
            self.show(self._paths[i])

    def _step(self, direction: int):
        if not self._paths:
            return
        cur = self.meta.path if self.meta else None
        i = self._paths.index(cur) if cur in self._paths else -direction
        self.show(self._paths[(i + direction) % len(self._paths)])

    def _open_external(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="FCS-Datei inspizieren",
            filetypes=[("FCS-Dateien", "*.fcs"), ("Alle Dateien", "*.*")],
            initialdir=self.app.input_dir if self.app.input_dir and os.path.isdir(self.app.input_dir) else None)
        if path:
            self.follow_var.set(False)
            self.show(path)

    def show(self, path: str | None):
        if not path:
            return
        try:
            meta = self.app._metadata(path)
        except (OSError, FcsMetaError) as exc:
            messagebox.showerror("Nicht lesbar", f"{os.path.basename(path)}:\n{exc}", parent=self.win)
            return
        self.meta = meta
        if path in self._paths:
            self.file_combo.current(self._paths.index(path))
        else:
            self.file_var.set(path)
        self._fill_header()
        self._fill_overview()
        self._fill_channels()
        self._fill_spill()
        self._fill_keywords()
        if self._current_tab() is self.tab_compare:
            self._mark_compare_column()
        self.status_var.set(f"{meta.path}   ·   {len(meta.keywords)} Keywords")

    def _current_tab(self):
        try:
            return self.nb.nametowidget(self.nb.select())
        except (KeyError, tk.TclError):
            return None

    def _on_tab_changed(self, _e=None):
        if self._current_tab() is self.tab_compare and self._compare_dirty:
            self.win.after(10, self._fill_compare)

    def _copy(self, text: str, what: str = "Wert"):
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        short = text if len(text) < 60 else text[:57] + "..."
        self.status_var.set(f"{what} kopiert: {short}")

    # ----------------------------------------------------------------- header
    def _fill_header(self):
        m = self.meta
        self.title_lbl.configure(text=m.filename)
        start = m.acquisition_start()
        when = start.strftime("%d.%m.%Y, %H:%M") if start else (m.get("DATE") or "")
        parts = [m.get("CYT"), m.get("CREATOR"), when, m.get("OP") and f"Operator: {m.get('OP')}"]
        self.subtitle_lbl.configure(text="   ·   ".join(p for p in parts if p))

        for w in self.chips.winfo_children():
            w.destroy()
        n, d, rate = m.event_count(), m.duration(), m.event_rate()
        volts = [p.get("V") for p in m.params if p.get("V")]
        chips = [
            (f"{n:,}".replace(",", ".") if n is not None else "–", "Events"),
            (_format_duration(d) if d else "–", "Messdauer"),
            (f"{rate:,.0f}".replace(",", ".") + " /s" if rate else "–", "Ereignisrate"),
            (str(len(m.params)), "Kanäle"),
            (f"{len(volts)}" if volts else "keine", "Kanäle mit Spannung"),
            (str(len(m.lasers)) if m.lasers else "–", "Laser"),
            (self._spill_chip(), "Spillover"),
            (m.version.replace("FCS", "FCS "), "Format"),
        ]
        for i, (value, label) in enumerate(chips):
            card = tk.Frame(self.chips, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER,
                            padx=12, pady=6)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
            self.chips.columnconfigure(i, weight=1, uniform="chip")
            tk.Label(card, text=value, bg=CARD_BG, fg=ACCENT, font=(FONT, 13, "bold"), anchor="w").pack(fill="x")
            tk.Label(card, text=label, bg=CARD_BG, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x")

    def _spill_chip(self) -> str:
        sp = self.meta.spill
        if sp is None:
            return "keine"
        n = len(sp.channels)
        return f"{n}×{n}" + (" (leer)" if sp.is_identity() else "")

    # --------------------------------------------------------------- overview
    def _build_overview_tab(self):
        outer = self.tab_overview
        canvas = tk.Canvas(outer, bg=PAGE_BG, highlightthickness=0)
        vs = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.ov_inner = tk.Frame(canvas, bg=PAGE_BG)
        win_id = canvas.create_window((0, 0), window=self.ov_inner, anchor="nw")
        self.ov_inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))

        def wheel(e):
            if self._current_tab() is self.tab_overview:
                canvas.yview_scroll(int(-e.delta / 120), "units")
        self.win.bind("<MouseWheel>", wheel, add="+")
        self.ov_inner.columnconfigure(0, weight=1, uniform="ov")
        self.ov_inner.columnconfigure(1, weight=1, uniform="ov")

    def _card(self, parent, title: str, row: int, col: int, colspan: int = 1) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER, padx=14, pady=10)
        card.grid(row=row, column=col, columnspan=colspan, sticky="nsew", padx=8, pady=8)
        tk.Label(card, text=title, bg=CARD_BG, fg=ACCENT, font=(FONT, 11, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        tk.Frame(card, bg=ACCENT_LIGHT, height=2).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        card.columnconfigure(1, weight=1)
        return card

    def _fill_overview(self):
        for w in self.ov_inner.winfo_children():
            w.destroy()
        sections = self.meta.summary_sections()
        # Messung + Probe left, Gerät + Datei right, Laser across the bottom
        placement = {"Messung": (0, 0), "Gerät & Setup": (0, 1), "Probe": (1, 0), "Datei": (1, 1)}
        for title, entries in sections:
            r, c = placement.get(title, (2, 0))
            card = self._card(self.ov_inner, title, r, c)
            if not entries:
                tk.Label(card, text="(keine Angaben in der Datei)", bg=CARD_BG, fg=FAINT,
                         font=(FONT, 9, "italic")).grid(row=2, column=0, columnspan=3, sticky="w")
            for i, (label, value, source) in enumerate(entries, start=2):
                tk.Label(card, text=label, bg=CARD_BG, fg=MUTED, font=(FONT, 9), anchor="nw").grid(
                    row=i, column=0, sticky="nw", padx=(0, 12), pady=1)
                v = tk.Label(card, text=value, bg=CARD_BG, fg="#1d232b", font=(FONT, 9, "bold"), anchor="nw",
                             justify="left", wraplength=280, cursor="hand2")
                v.grid(row=i, column=1, sticky="nw", pady=1)
                v.bind("<Double-Button-1>", lambda _e, t=value, l=label: self._copy(t, l))
                tk.Label(card, text=source, bg=CARD_BG, fg=FAINT, font=(FONT, 7), anchor="ne").grid(
                    row=i, column=2, sticky="ne", padx=(8, 0), pady=2)

        m = self.meta
        if m.lasers:
            card = self._card(self.ov_inner, "Laser", 2, 0, colspan=2)
            wavelengths = sorted({p.get("L") for p in m.params if p.get("L")}, key=lambda s: float(s) if
                                 s.replace(".", "", 1).isdigit() else 0)
            for j, head in enumerate(("Nr.", "Name", "Delay (µs)", "Area Scaling (ASF)")):
                tk.Label(card, text=head, bg=CARD_BG, fg=MUTED, font=(FONT, 9, "bold"), anchor="w").grid(
                    row=2, column=j, sticky="w", padx=(0, 24))
            for i, laser in enumerate(m.lasers, start=3):
                for j, val in enumerate((str(laser.index), laser.name, laser.delay, laser.asf)):
                    tk.Label(card, text=val or "–", bg=CARD_BG, fg="#1d232b", font=(FONT, 9), anchor="w").grid(
                        row=i, column=j, sticky="w", padx=(0, 24))
            if wavelengths:
                tk.Label(card, text="Anregungswellenlängen ($PnL): " + ", ".join(f"{w} nm" for w in wavelengths),
                         bg=CARD_BG, fg=MUTED, font=(FONT, 9), anchor="w").grid(
                    row=len(m.lasers) + 3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        tk.Label(self.ov_inner, text="Doppelklick auf einen Wert kopiert ihn.  Grau rechts: Quell-Keyword in der "
                 "Datei.", bg=PAGE_BG, fg=FAINT, font=(FONT, 8)).grid(row=3, column=0, columnspan=2, sticky="w",
                                                                      padx=10, pady=(0, 8))

    # --------------------------------------------------------------- channels
    def _build_channels_tab(self):
        tab = self.tab_channels
        top = ttk.Frame(tab, padding=(6, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Filter:").pack(side="left")
        self.ch_filter_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.ch_filter_var, width=24).pack(side="left", padx=4)
        self.ch_filter_var.trace_add("write", lambda *_: self._fill_channels())
        self.ch_hide_hw_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="-H / -W Kanäle ausblenden (nur Area)", variable=self.ch_hide_hw_var,
                        command=self._fill_channels).pack(side="left", padx=10)
        ttk.Button(top, text="Tabelle kopieren", command=self._copy_channels).pack(side="right")
        ttk.Button(top, text="Als CSV speichern...", command=self._export_channels).pack(side="right", padx=4)

        pane = ttk.PanedWindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=3)
        pane.add(right, weight=2)
        self.ch_tree_parent = left
        self.ch_tree = None

        self.v_fig = Figure(figsize=(4.2, 5), dpi=96)
        self.v_ax = self.v_fig.add_subplot(111)
        self.v_canvas = FigureCanvasTkAgg(self.v_fig, master=right)
        self.v_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _visible_channel_rows(self) -> tuple[list[tuple[str, str, str]], list[list[str]]]:
        m = self.meta
        fields = m.used_param_fields()
        rows = m.channel_rows()
        flt = self.ch_filter_var.get().strip().lower()
        out = []
        for row, p in zip(rows, m.params):
            name = p.get("N", "")
            if self.ch_hide_hw_var.get() and (name.endswith("-H") or name.endswith("-W")):
                continue
            if flt and not any(flt in c.lower() for c in row):
                continue
            out.append(row)
        return fields, out

    def _fill_channels(self):
        if self.meta is None:
            return
        fields, rows = self._visible_channel_rows()
        cols = ["nr"] + [s for s, _, _ in fields]
        if self.ch_tree is None or list(self.ch_tree["columns"]) != cols:
            if self.ch_tree is not None:
                self.ch_tree.master.destroy()
            self.ch_tree = self._make_tree(self.ch_tree_parent, cols)
            self.ch_tree.bind("<<TreeviewSelect>>", self._on_channel_select)
            self.ch_tree.bind("<Double-Button-1>", lambda _e: self._copy_channel_row())
            self.ch_tree.heading("nr", text="#")
            self.ch_tree.column("nr", width=38, anchor="e", stretch=False)
            for suffix, header, _ in fields:
                self.ch_tree.heading(suffix, text=header)
                anchor = "e" if suffix in ("V", "G", "R", "B", "L", "O") else "w"
                self.ch_tree.column(suffix, anchor=anchor, stretch=suffix in ("N", "S"))
        tree = self.ch_tree
        tree.delete(*tree.get_children())
        for i, row in enumerate(rows):
            tree.insert("", "end", iid=row[0], values=row, tags=("odd",) if i % 2 else ())
        # size columns to content
        for j, (suffix, header, _) in enumerate(fields, start=1):
            longest = max([len(header)] + [len(r[j]) for r in rows]) if rows else len(header)
            tree.column(suffix, width=max(48, min(260, 8 * longest + 18)))
        if self._selected_channel and tree.exists(self._selected_channel_iid()):
            tree.selection_set(self._selected_channel_iid())
        self._draw_voltages(rows, fields)

    def _selected_channel_iid(self) -> str:
        for i, p in enumerate(self.meta.params):
            if p.get("N") == self._selected_channel:
                return str(i + 1)
        return ""

    def _on_channel_select(self, _e=None):
        sel = self.ch_tree.selection()
        if sel:
            self._selected_channel = self.meta.params[int(sel[0]) - 1].get("N")
            fields, rows = self._visible_channel_rows()
            self._draw_voltages(rows, fields)

    def _draw_voltages(self, rows, fields):
        ax = self.v_ax
        ax.clear()
        suffixes = [s for s, _, _ in fields]
        if "V" not in suffixes:
            ax.text(0.5, 0.5, "Diese Datei enthält keine\nDetektor-Spannungen ($PnV).", ha="center", va="center",
                    color=MUTED, fontsize=10, transform=ax.transAxes)
            ax.set_axis_off()
            self.v_canvas.draw_idle()
            return
        vi = suffixes.index("V") + 1
        ni = suffixes.index("N") + 1 if "N" in suffixes else 0
        si = suffixes.index("S") + 1 if "S" in suffixes else None
        data = []
        for r in rows:
            try:
                data.append((r[ni], r[si] if si else "", float(r[vi])))
            except ValueError:
                continue
        if not data:
            ax.set_axis_off()
            self.v_canvas.draw_idle()
            return
        n = len(data)
        fs = max(5.0, min(9.0, 360.0 / n))
        labels = [f"{name}  ({marker})" if marker else name for name, marker, _ in data]
        values = [v for _, _, v in data]
        groups = [_laser_group(name) for name, _, _ in data]
        use_groups = len({g for g in groups if g}) >= 2
        colors = [HIGHLIGHT if name == self._selected_channel
                  else (_LASER_COLORS.get(g, ACCENT) if use_groups else ACCENT)
                  for (name, _, _), g in zip(data, groups)]
        y = np.arange(n)
        ax.barh(y, values, color=colors, height=0.72)
        if use_groups:
            from matplotlib.patches import Patch
            present = [g for g in _LASER_COLORS if g in groups]
            ax.legend(handles=[Patch(color=_LASER_COLORS[g], label=_LASER_NAMES.get(g, g)) for g in present],
                      fontsize=7, loc="lower right", frameon=False)
        ax.set_yticks(y, labels, fontsize=fs)
        ax.invert_yaxis()
        ax.set_ylim(max(n, 16) - 0.5, -0.5)  # few channels: keep bars slim, aligned at the top
        vmax = max(values) if max(values) > 0 else 1
        ax.set_xlim(0, vmax * 1.16)
        if n <= 70:
            for yi, v in zip(y, values):
                ax.text(v + vmax * 0.01, yi, f"{v:g}", va="center", fontsize=fs, color=MUTED)
        ax.set_title("Detektor-Spannungen ($PnV)", fontsize=10, color="#1d232b", loc="left")
        ax.set_xlabel("Volt", fontsize=8, color=MUTED)
        ax.tick_params(axis="x", labelsize=7, colors=MUTED)
        ax.tick_params(axis="y", length=0)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(CARD_BORDER)
        ax.spines["bottom"].set_color(CARD_BORDER)
        ax.grid(axis="x", color="#e6eaf0", linewidth=0.6)
        ax.set_axisbelow(True)
        self.v_fig.tight_layout()
        self.v_canvas.draw_idle()

    def _channel_table_text(self) -> tuple[list[str], list[list[str]]]:
        fields, rows = self._visible_channel_rows()
        header = ["#"] + [f"{h} ($Pn{s})" for s, h, _ in fields]
        return header, rows

    def _copy_channels(self):
        header, rows = self._channel_table_text()
        self._copy("\n".join("\t".join(r) for r in [header] + rows), "Kanal-Tabelle")
        self.status_var.set(f"Kanal-Tabelle ({len(rows)} Zeilen) kopiert — in Excel einfügen mit Strg+V.")

    def _copy_channel_row(self):
        sel = self.ch_tree.selection()
        if sel:
            self._copy("\t".join(str(v) for v in self.ch_tree.item(sel[0], "values")), "Zeile")

    def _export_channels(self):
        header, rows = self._channel_table_text()
        self._save_csv(header, rows, os.path.splitext(self.meta.filename)[0] + "_kanaele.csv")

    def _save_csv(self, header, rows, default_name):
        exports = getattr(self.app, "exports_dir", None)
        path = filedialog.asksaveasfilename(
            parent=self.win, defaultextension=".csv", initialfile=default_name,
            initialdir=exports if exports and os.path.isdir(exports) else None,
            filetypes=[("CSV (Excel, ;-getrennt)", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(header)
                w.writerows(rows)
        except OSError as exc:
            messagebox.showerror("Nicht gespeichert", str(exc), parent=self.win)
            return
        self.status_var.set(f"Gespeichert: {path}")

    # ------------------------------------------------------------ spillover
    def _build_spill_tab(self):
        tab = self.tab_spill
        top = ttk.Frame(tab, padding=(6, 6))
        top.pack(fill="x")
        self.spill_info_var = tk.StringVar()
        ttk.Label(top, textvariable=self.spill_info_var).pack(side="left")
        ttk.Button(top, text="Matrix kopieren (Excel)", command=self._copy_spill).pack(side="right")
        self.spill_hover_var = tk.StringVar(value="Maus über eine Zelle bewegen, um den Wert zu sehen.")
        ttk.Label(tab, textvariable=self.spill_hover_var, foreground=MUTED).pack(fill="x", padx=8)
        self.s_fig = Figure(figsize=(6, 5), dpi=96)
        self.s_canvas = FigureCanvasTkAgg(self.s_fig, master=tab)
        self.s_canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)
        self.s_canvas.mpl_connect("motion_notify_event", self._on_spill_hover)

    def _fill_spill(self):
        self.s_fig.clear()
        sp = self.meta.spill
        if sp is None:
            ax = self.s_fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.5, "In dieser Datei ist keine Spillover-Matrix gespeichert\n"
                    "($SPILLOVER / SPILL fehlt).", ha="center", va="center", color=MUTED, fontsize=11)
            self.spill_info_var.set("")
            self.s_canvas.draw_idle()
            return
        n = len(sp.channels)
        apply = self.meta.get("APPLY COMPENSATION")
        info = f"{n} × {n} Kanäle   ·   Quelle: {sp.name}"
        if apply:
            info += f"   ·   APPLY COMPENSATION = {apply}"
        if sp.is_identity():
            info += "   ·   Einheitsmatrix — es ist keine Kompensation hinterlegt"
        self.spill_info_var.set(info)

        ax = self.s_fig.add_subplot(111)
        vals = sp.spill * 100.0
        off = vals.copy()
        np.fill_diagonal(off, np.nan)
        finite = off[np.isfinite(off)]
        lim = max(float(np.nanmax(np.abs(finite))) if finite.size else 0.0, 1.0)
        if finite.size and finite.min() < 0:
            cmap, vmin, vmax = "RdBu_r", -lim, lim
        else:
            cmap, vmin, vmax = "Blues", 0, lim
        cm =matplotlib.colormaps[cmap].copy()
        cm.set_bad("#e3e7ee")
        im = ax.imshow(off, cmap=cm, vmin=vmin, vmax=vmax, aspect="equal", interpolation="nearest")
        fs = max(4.0, min(9.0, 260.0 / n))
        ax.set_xticks(range(n), sp.channels, rotation=90, fontsize=fs)
        ax.set_yticks(range(n), sp.channels, fontsize=fs)
        ax.set_xlabel("Detektor (Spill INTO)", fontsize=8, color=MUTED)
        ax.set_ylabel("Fluorochrom / Quelle (Spill FROM)", fontsize=8, color=MUTED)
        ax.tick_params(length=0)
        if n <= 16:
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    v = vals[i, j]
                    if abs(v) < 0.005:
                        continue
                    strong = abs(v) > 0.55 * lim
                    ax.text(j, i, f"{v:.1f}", ha="center", va="center", fontsize=fs - 0.5,
                            color="white" if strong else "#1d232b")
        cb = self.s_fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
        cb.set_label("Spillover (%) — Diagonale (100 %) grau", fontsize=8, color=MUTED)
        cb.ax.tick_params(labelsize=7)
        self.s_fig.tight_layout()
        self.s_ax = ax
        self.s_canvas.draw_idle()

    def _on_spill_hover(self, event):
        sp = self.meta.spill if self.meta else None
        if sp is None or event.inaxes is not getattr(self, "s_ax", None) or event.xdata is None:
            return
        j, i = int(round(event.xdata)), int(round(event.ydata))
        n = len(sp.channels)
        if 0 <= i < n and 0 <= j < n:
            self.spill_hover_var.set(
                f"{sp.channels[i]}  →  {sp.channels[j]}:   {sp.spill[i, j] * 100:.3f} %   (Faktor {sp.spill[i, j]:.5f})")

    def _copy_spill(self):
        sp = self.meta.spill if self.meta else None
        if sp is None:
            return
        lines = ["\t" + "\t".join(sp.channels)]
        for name, row in zip(sp.channels, sp.spill):
            lines.append(name + "\t" + "\t".join(f"{v:.6g}" for v in row))
        self._copy("\n".join(lines), "Spillover-Matrix")
        self.status_var.set("Spillover-Matrix kopiert (Faktoren, Zeile = Quelle, Spalte = Detektor).")

    # -------------------------------------------------------------- keywords
    def _build_keywords_tab(self):
        tab = self.tab_keywords
        top = ttk.Frame(tab, padding=(6, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Suchen:").pack(side="left")
        self.kw_search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.kw_search_var, width=30).pack(side="left", padx=4)
        self.kw_search_var.trace_add("write", lambda *_: self._fill_keywords())
        self.kw_hide_params_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Kanal-Keywords ($Pn...) ausblenden", variable=self.kw_hide_params_var,
                        command=self._fill_keywords).pack(side="left", padx=10)
        self.kw_count_var = tk.StringVar()
        ttk.Label(top, textvariable=self.kw_count_var, foreground=MUTED).pack(side="left", padx=6)
        ttk.Button(top, text="Alles kopieren", command=self._copy_keywords).pack(side="right")
        ttk.Button(top, text="Als CSV speichern...", command=self._export_keywords).pack(side="right", padx=4)
        body = ttk.Frame(tab, padding=(6, 0, 6, 6))
        body.pack(fill="both", expand=True)
        self.kw_tree = self._make_tree(body, ("key", "value"))
        self.kw_tree.heading("key", text="Keyword")
        self.kw_tree.heading("value", text="Wert")
        self.kw_tree.column("key", width=260, stretch=False)
        self.kw_tree.column("value", width=700, stretch=True)
        self.kw_tree.tag_configure("std", foreground=ACCENT)
        self.kw_tree.bind("<Double-Button-1>", self._copy_keyword_value)
        ttk.Label(tab, text="Blau = Standard-Keyword der FCS-Norm ($...), schwarz = herstellerspezifisch.  "
                  "Doppelklick kopiert den Wert.", foreground=FAINT).pack(anchor="w", padx=8, pady=(0, 4))

    def _keyword_rows(self) -> list[tuple[str, str]]:
        q = self.kw_search_var.get().strip().lower()
        hide = self.kw_hide_params_var.get()
        rows = []
        for k, v in self.meta.keywords.items():
            if hide and self.meta.is_param_keyword(k):
                continue
            if q and q not in k.lower() and q not in v.lower():
                continue
            rows.append((k, v))
        return rows

    def _fill_keywords(self):
        if self.meta is None:
            return
        tree = self.kw_tree
        tree.delete(*tree.get_children())
        rows = self._keyword_rows()
        for i, (k, v) in enumerate(rows):
            tags = ["odd"] if i % 2 else []
            if k.startswith("$"):
                tags.append("std")
            display = v if len(v) <= 400 else v[:400] + f" … ({len(v)} Zeichen)"
            tree.insert("", "end", iid=k, values=(k, display), tags=tags)
        self.kw_count_var.set(f"{len(rows)} von {len(self.meta.keywords)}")

    def _copy_keyword_value(self, _e=None):
        sel = self.kw_tree.selection()
        if sel:
            self._copy(self.meta.keywords.get(sel[0], ""), sel[0])

    def _copy_keywords(self):
        rows = self._keyword_rows()
        self._copy("\n".join(f"{k}\t{v}" for k, v in rows), "Keywords")
        self.status_var.set(f"{len(rows)} Keywords kopiert.")

    def _export_keywords(self):
        self._save_csv(["Keyword", "Wert"], self._keyword_rows(),
                       os.path.splitext(self.meta.filename)[0] + "_metadaten.csv")

    # --------------------------------------------------------------- compare
    def _build_compare_tab(self):
        tab = self.tab_compare
        top = ttk.Frame(tab, padding=(6, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Vergleichen:").pack(side="left")
        self.cmp_mode_var = tk.StringVar(value=next(iter(_COMPARE_MODES)))
        cb = ttk.Combobox(top, textvariable=self.cmp_mode_var, values=list(_COMPARE_MODES), state="readonly", width=44)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._fill_compare())
        self.cmp_only_diff_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="nur Abweichungen zeigen", variable=self.cmp_only_diff_var,
                        command=self._fill_compare).pack(side="left", padx=10)
        ttk.Button(top, text="Neu einlesen", command=self._reload_compare).pack(side="right")
        ttk.Button(top, text="Als CSV speichern...", command=self._export_compare).pack(side="right", padx=4)
        ttk.Button(top, text="Kopieren", command=self._copy_compare).pack(side="right")
        self.cmp_summary = tk.Label(tab, bg=PAGE_BG, fg="#1d232b", font=(FONT, 10), anchor="w", padx=10, pady=6)
        self.cmp_summary.pack(fill="x", padx=6)
        self.cmp_body = ttk.Frame(tab, padding=(6, 4, 6, 6))
        self.cmp_body.pack(fill="both", expand=True)
        self.cmp_tree = None
        self._cmp_prefix_len = 0
        self._cmp_col_titles: list[str] = []
        self._cmp_table: tuple[list[str], list[list[str]]] = ([], [])

    def _reload_compare(self):
        self.app.__dict__.get("_meta_cache", {}).clear()
        self.refresh_file_list()
        self._fill_compare()
        if self.meta:
            self.show(self.meta.path)

    def _load_all(self) -> list[FcsMetadata]:
        metas = []
        for k, p in enumerate(self._paths):
            if k % 15 == 0:
                self.cmp_summary.configure(text=f"Lese Metadaten ... {k}/{len(self._paths)}")
                self.win.update_idletasks()
            try:
                metas.append(self.app._metadata(p))
            except (OSError, FcsMetaError):
                metas.append(None)
        return metas

    def _fill_compare(self):
        self._compare_dirty = False
        metas = self._load_all()
        mode = _COMPARE_MODES[self.cmp_mode_var.get()]
        names: list[str] = []
        seen: set[str] = set()
        per_file: list[dict[str, str]] = []
        for m in metas:
            d: dict[str, str] = {}
            if m is not None:
                if mode is None:
                    for k, v in m.keywords.items():
                        bare = k.lstrip("$")
                        if m.is_param_keyword(k) or bare in _VOLATILE:
                            continue
                        d[k] = v
                else:
                    for p in m.params:
                        if p.get("N") and p.get(mode):
                            d[p["N"]] = _format_param_value(mode, p[mode])
                        elif p.get("N"):
                            d.setdefault(p["N"], "")
            per_file.append(d)
            for key in d:
                if key not in seen:
                    seen.add(key)
                    names.append(key)

        rows, n_diff, total = [], 0, 0
        for key in names:
            vals = [("—" if key not in d else d[key]) if metas[i] is not None else "?"
                    for i, d in enumerate(per_file)]
            distinct = {v for v in vals if v != "?"}
            if mode is not None and distinct <= {"", "—"}:
                continue  # channel without that property in any file (e.g. Time has no voltage)
            total += 1
            differs = len(distinct) > 1
            n_diff += differs
            if self.cmp_only_diff_var.get() and not differs:
                continue
            if differs:
                counts = {v: vals.count(v) for v in distinct}
                status = f"≠  {len(distinct)} Werte"
                majority = max(counts, key=counts.get)
            else:
                status, majority = "✓ gleich", None
            rows.append((key, status, vals, majority, differs))

        what = "Keywords" if mode is None else "Kanäle"
        if not total:
            self.cmp_summary.configure(
                text=f"Keine der {len(metas)} Dateien enthält „{self.cmp_mode_var.get()}“.", bg=PAGE_BG)
        elif n_diff:
            self.cmp_summary.configure(
                text=f"⚠  {n_diff} von {total} {what} weichen zwischen den {len(metas)} Dateien ab "
                     f"(rot markiert; abweichende Einzelwerte mit ● gekennzeichnet).", bg=DIFF_BG)
        else:
            self.cmp_summary.configure(
                text=f"✓  Alle {total} {what} sind in allen {len(metas)} Dateien identisch.", bg="#e3f2e5")

        # ---- table
        if self.cmp_tree is not None:
            self.cmp_tree.master.destroy()
        file_cols = [f"f{i}" for i in range(len(self._paths))]
        tree = self.cmp_tree = self._make_tree(self.cmp_body, ["status"] + file_cols, show="tree headings")
        tree.heading("#0", text="Keyword" if mode is None else "Kanal")
        tree.column("#0", width=230 if mode is None else 150, stretch=False)
        tree.heading("status", text="Status")
        tree.column("status", width=90, stretch=False)
        self._cmp_col_titles = [os.path.splitext(os.path.basename(p))[0] for p in self._paths]
        # file names often share a long prefix ("Lysate Buffer_CI ...") — show only the distinguishing part
        prefix = os.path.commonprefix(self._cmp_col_titles) if len(self._cmp_col_titles) > 1 else ""
        self._cmp_prefix_len = len(prefix) if len(prefix) >= 6 else 0
        for i, p in enumerate(self._paths):
            short = self._short_title(self._cmp_col_titles[i])
            tree.heading(f"f{i}", text=short, command=lambda path=p: self.show(path))
            tree.column(f"f{i}", width=min(190, max(80, 8 * len(short) + 16)), anchor="center", stretch=False)
        if self._cmp_prefix_len:
            self.cmp_summary.configure(text=self.cmp_summary.cget("text") + f"   (Spalten ohne gemeinsamen Präfix "
                                       f"„{prefix}“; Klick auf Spaltenkopf zeigt die Datei)")
        for r, (key, status, vals, majority, differs) in enumerate(rows):
            shown = [f"● {v}" if differs and v != majority else v for v in vals]
            tags = ("diff",) if differs else (("odd",) if r % 2 else ())
            tree.insert("", "end", text=key, values=[status] + shown, tags=tags)
        self._cmp_table = (["Kanal/Keyword", "Status"] + self._cmp_col_titles,
                           [[key, status] + vals for key, status, vals, _, _ in rows])
        self._mark_compare_column()

    def _mark_compare_column(self):
        tree = self.cmp_tree
        if tree is None or self.meta is None:
            return
        for i, (p, title) in enumerate(zip(self._paths, self._cmp_col_titles)):
            short = self._short_title(title)
            tree.heading(f"f{i}", text=("▶ " + short) if p == self.meta.path else short)

    def _short_title(self, title: str) -> str:
        t = title[self._cmp_prefix_len:].lstrip(" _-") if self._cmp_prefix_len else title
        t = ("…" + t) if self._cmp_prefix_len else t
        return t if len(t) <= 26 else t[:24] + "…"

    def _copy_compare(self):
        header, rows = self._cmp_table
        self._copy("\n".join("\t".join(r) for r in [header] + rows), "Vergleich")
        self.status_var.set(f"Vergleichstabelle ({len(rows)} Zeilen) kopiert.")

    def _export_compare(self):
        header, rows = self._cmp_table
        self._save_csv(header, rows, "metadaten_vergleich.csv")

    # ------------------------------------------------------------------ close
    def _close(self):
        self.app._meta_inspector = None
        self.win.destroy()
