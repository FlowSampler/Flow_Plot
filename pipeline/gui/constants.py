"""Constants shared by the GUI modules (option lists, defaults, file names)."""
from __future__ import annotations

from panel_export import POP_ALL as PANEL_POP_ALL

SHAPES = ["Polygon", "Rectangle", "Ellipse", "Quadrant", "Quadrant (Winkel)"]
QUADRANT_SHAPES = ("Quadrant", "Quadrant (Winkel)")
SCOPES = ["all", "group", "file"]
HIST_1D = "Histogramm (1D)"
PLOT_TYPES = ["Pseudocolor", "Contour", "Density", "Scatter", HIST_1D]
# Display name -> matplotlib colormap for the 2D density plots.
COLORMAPS = {
    "Turbo (FlowJo-ähnlich)": "turbo",
    "Viridis": "viridis",
    "Magma": "magma_r",
    "Blau (einfarbig)": "Blues",
    "Graustufen": "Greys",
}
DEFAULT_CHANNEL_PAIR = ("FSC-A", "SSC-A")
DEFAULT_RATIO_PAIR = ("PerCP-A", "Qdot 655-A")

# Options for the "which population?" selector in the Kinetik/Statistik tabs.
# Picking a specific saved gate-set NAME avoids silently ANDing together every
# saved gate-set that happens to match a file's scope — important once you
# have several mutually exclusive named populations (e.g. "Lebend"/"Dead").
GATE_SOURCE_LIVE = "Live (Gating-Tab-Ansicht, nur aktuelle Datei)"
GATE_SOURCE_NONE = PANEL_POP_ALL  # "Ungegated (alle Events)"

# Which compensation every tab works on. FACSDiva stores UNCOMPENSATED data
# plus the matrix ($SPILL), so "file" (= what Diva/FlowJo show) is the default.
COMP_MODES = {
    "none": "Keine (unkompensiert, Rohdaten)",
    "file": "Matrix aus der FCS-Datei ($SPILL)",
    "experiment": "Experiment-Matrix (bearbeitet)",
}
COMP_MATRIX_FILE = "matrix.json"
COMP_SETTINGS_FILE = "settings.json"
EXCLUDED_FILES_FILE = "excluded_files.json"  # in the experiment folder: files hidden from the list

# Overlay line styles: label -> (matplotlib linestyle, short marker shown in the file list)
OVERLAY_LINESTYLES = {
    "Durchgezogen": ("-", ""),
    "Gestrichelt": ("--", "– –"),
    "Gepunktet": (":", "···"),
    "Strich-Punkt": ("-.", "–·"),
    "Strich-Punkt-Punkt": ((0, (6, 2, 1, 2, 1, 2)), "–··"),
    "Lang gestrichelt": ((0, (10, 4)), "— —"),
}
DEFAULT_LINESTYLE = "Durchgezogen"
