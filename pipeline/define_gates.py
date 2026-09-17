"""Interactively draw one or more sequential polygon pre-gates on a representative
FCS file and save them as a reusable gate-set JSON.

Usage examples
--------------
Define a gate that applies to ALL files in the folder:
    python define_gates.py --sample "Ca2+_kinetic\\Lysate Buffer_MEC-1 baseline 60 + Lysate.fcs" \
        --name singlets_cells --scope all

Define a gate that only applies to files whose name contains "ZCL278":
    python define_gates.py --sample "Ca2+_kinetic\\...ZCL278...fcs" \
        --name zcl278_refine --scope group --pattern ZCL278

Define a gate for exactly one file:
    python define_gates.py --sample "Ca2+_kinetic\\...fcs" --name one_off --scope file

For each step you specify a channel pair (default sequence: FSC-A/SSC-A, then
FSC-H/FSC-W for singlets). A density plot of the (possibly already pre-gated-
by-earlier-steps) population opens; click to place polygon vertices, then
press Enter to close the polygon and move to the next step.
"""
from __future__ import annotations

import argparse
import os
import sys

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.widgets import PolygonSelector

from gating import GateSet, GateStep, save_gate_set
from io_utils import load_fcs


def draw_polygon(df, x_channel: str, y_channel: str, mask_in: np.ndarray) -> list[list[float]]:
    x = df[x_channel].to_numpy()[mask_in]
    y = df[y_channel].to_numpy()[mask_in]

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.hist2d(x, y, bins=200, cmap="viridis")
    ax.set_xlabel(x_channel)
    ax.set_ylabel(y_channel)
    ax.set_title(
        f"Click to draw gate vertices on {x_channel} vs {y_channel}\n"
        f"Press ENTER when the polygon is closed"
    )

    vertices: list[list[float]] = []

    def on_select(verts):
        vertices.clear()
        vertices.extend([[float(vx), float(vy)] for vx, vy in verts])

    selector = PolygonSelector(ax, on_select)

    done = {"flag": False}

    def on_key(event):
        if event.key == "enter":
            done["flag"] = True
            plt.close(fig)

    fig.canvas.mpl_connect("key_press_event", on_key)
    plt.show()

    if not vertices:
        raise RuntimeError("No polygon was drawn (need at least 3 vertices).")
    return vertices


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", required=True, help="Path to a representative .fcs file to gate on")
    parser.add_argument("--name", required=True, help="Name for this gate-set (used as output filename)")
    parser.add_argument("--scope", required=True, choices=["all", "group", "file"])
    parser.add_argument("--pattern", help="Substring to match filenames, required when --scope group")
    parser.add_argument(
        "--filename",
        help="Exact filename this gate applies to when --scope file (defaults to the --sample filename)",
    )
    parser.add_argument(
        "--steps",
        default="FSC-A,SSC-A;FSC-H,FSC-W",
        help='Semicolon-separated list of "x_channel,y_channel" pairs, one gating step each. '
        'Default: "FSC-A,SSC-A;FSC-H,FSC-W" (cells, then singlets).',
    )
    parser.add_argument(
        "--gates-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "gates"),
        help="Directory to save the gate-set JSON into",
    )
    args = parser.parse_args()

    if args.scope == "group" and not args.pattern:
        sys.exit("--pattern is required when --scope group")

    scope = {"type": args.scope}
    if args.scope == "group":
        scope["pattern"] = args.pattern
    elif args.scope == "file":
        scope["filename"] = args.filename or os.path.basename(args.sample)

    fcs = load_fcs(args.sample)
    df = fcs.df

    step_defs = [tuple(p.split(",")) for p in args.steps.split(";") if p.strip()]
    for x_ch, y_ch in step_defs:
        for ch in (x_ch, y_ch):
            if ch not in df.columns:
                sys.exit(f"Channel '{ch}' not found in {fcs.filename}. Available: {list(df.columns)}")

    running_mask = np.ones(len(df), dtype=bool)
    steps: list[GateStep] = []
    for x_ch, y_ch in step_defs:
        print(f"\n--- Gating step: {x_ch} vs {y_ch} ({running_mask.sum()} events shown) ---")
        vertices = draw_polygon(df, x_ch, y_ch, running_mask)
        step = GateStep(x_channel=x_ch, y_channel=y_ch, vertices=vertices)
        steps.append(step)
        running_mask &= step.mask(df)
        print(f"  -> {running_mask.sum()} / {len(df)} events remain after this step")

    gate_set = GateSet(name=args.name, scope=scope, steps=steps)
    out_path = save_gate_set(gate_set, args.gates_dir)
    print(f"\nSaved gate-set '{args.name}' (scope={scope}) to {out_path}")
    print(f"Final gated population: {running_mask.sum()} / {len(df)} events on the representative sample.")


if __name__ == "__main__":
    main()
