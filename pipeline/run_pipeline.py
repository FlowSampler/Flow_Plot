"""Batch pipeline: pregate -> per-event ratio -> time-binned kinetics, per FCS file.

For every .fcs file in --input-dir:
  1. Resolve and apply all matching gate-sets from --gates-dir (file- and
     group-specific gates are combined with any "all files" gate).
  2. Compute the per-event ratio num_channel / denom_channel on the gated
     population.
  3. Bin the ratio over time (bin width --bin-width-s) and write:
       <output-dir>/<file_stem>_kinetics.csv
       <output-dir>/<file_stem>_kinetics.png
     plus a gating QC scatter plot:
       <output-dir>/<file_stem>_gating_qc.png

Usage:
    python run_pipeline.py
    python run_pipeline.py --num-channel "PerCP-A" --denom-channel "Qdot 655-A" --bin-width-s 1.0
"""
from __future__ import annotations

import argparse
import os

import matplotlib.pyplot as plt
import pandas as pd

from gating import apply_gates, apply_named_gate_set, list_gate_set_names, load_all_gate_sets
from io_utils import find_fcs_files, load_fcs
from kinetics import bin_kinetics, compute_ratio, parse_baseline_duration_s, value_stats


def plot_kinetics(binned: pd.DataFrame, filename: str, baseline_end_s: float | None, out_path: str):
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.errorbar(
        binned["time_bin_start_s"],
        binned["median_ratio"],
        yerr=binned["sem_ratio"],
        fmt="-o",
        markersize=3,
        linewidth=1,
        capsize=2,
    )
    if baseline_end_s is not None:
        ax.axvline(baseline_end_s, color="red", linestyle="--", linewidth=1, label=f"stimulus added ({baseline_end_s:.0f}s)")
        ax.legend()
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Ratio (median per bin)")
    ax.set_title(filename)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_gating_qc(df: pd.DataFrame, mask, filename: str, out_path: str, x_channel="FSC-A", y_channel="SSC-A"):
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(df[x_channel][~mask], df[y_channel][~mask], s=2, color="lightgray", label="excluded")
    ax.scatter(df[x_channel][mask], df[y_channel][mask], s=2, color="tab:blue", label="gated")
    ax.set_xlabel(x_channel)
    ax.set_ylabel(y_channel)
    ax.set_title(f"{filename}\n{mask.sum()}/{len(df)} events gated")
    ax.legend(markerscale=4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", default=os.path.join(os.path.dirname(__file__), "..", "Ca2+_kinetic"))
    parser.add_argument("--gates-dir", default=os.path.join(os.path.dirname(__file__), "..", "gates"))
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(__file__), "..", "output"))
    parser.add_argument("--num-channel", default="PerCP-A")
    parser.add_argument("--denom-channel", default="Qdot 655-A")
    parser.add_argument("--bin-width-s", type=float, default=1.0)
    parser.add_argument("--min-signal", type=float, default=1.0, help="Exclude events with either channel <= this value")
    parser.add_argument(
        "--pos-threshold", type=float, default=None,
        help="If set, also compute %%positive/%%negative (ratio > threshold) per file in the summary CSV",
    )
    parser.add_argument(
        "--gate-set-name", default=None,
        help="Apply only the gate-set with this exact name (its file/group/all scope variants are still "
        "resolved by priority). REQUIRED if you have several mutually exclusive saved populations (e.g. "
        "'Lebend' and 'Dead') — without it, every gate-set whose scope matches a file gets ANDed together, "
        "which gives their (near-empty) intersection instead of picking one.",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    gate_sets = load_all_gate_sets(args.gates_dir)
    if not gate_sets:
        print(f"WARNING: no gate-sets found in {args.gates_dir} — proceeding ungated. "
              f"Run define_gates.py first to create pregates.")
    elif args.gate_set_name is None and len(list_gate_set_names(gate_sets)) > 1:
        print(
            f"WARNING: {len(list_gate_set_names(gate_sets))} different gate-set names found "
            f"({list_gate_set_names(gate_sets)}) but no --gate-set-name given — ALL of them whose scope "
            f"matches a file will be ANDed together. If these are mutually exclusive populations (e.g. "
            f"'Lebend'/'Dead'), pass --gate-set-name to pick exactly one."
        )

    fcs_paths = find_fcs_files(args.input_dir)
    summary_rows = []

    for path in fcs_paths:
        fcs = load_fcs(path)
        stem = os.path.splitext(fcs.filename)[0]
        if args.gate_set_name is not None:
            mask, applied_names = apply_named_gate_set(fcs.df, fcs.filename, gate_sets, args.gate_set_name)
        else:
            mask, applied_names = apply_gates(fcs.df, fcs.filename, gate_sets)
        gated_df = fcs.df[mask]

        print(f"[{fcs.filename}] gates applied: {applied_names or 'none'} "
              f"-> {mask.sum()}/{len(fcs.df)} events")

        ratio = compute_ratio(gated_df, args.num_channel, args.denom_channel, min_signal=args.min_signal)
        binned = bin_kinetics(gated_df["Time"], ratio, bin_width_s=args.bin_width_s)

        # include the gate-set name in output filenames so re-running for a
        # different population (e.g. "Lebend" then "Dead") doesn't overwrite
        # the previous run's files
        suffix = f"_{args.gate_set_name}" if args.gate_set_name else ""
        safe_suffix = "".join(c if c.isalnum() or c in "-_" else "_" for c in suffix)

        csv_path = os.path.join(args.output_dir, f"{stem}{safe_suffix}_kinetics.csv")
        binned.to_csv(csv_path, index=False)

        baseline_end_s = parse_baseline_duration_s(fcs.filename)
        png_path = os.path.join(args.output_dir, f"{stem}{safe_suffix}_kinetics.png")
        plot_kinetics(binned, fcs.filename, baseline_end_s, png_path)

        qc_path = os.path.join(args.output_dir, f"{stem}{safe_suffix}_gating_qc.png")
        plot_gating_qc(fcs.df, mask, fcs.filename, qc_path)

        baseline_ratio = (
            binned.loc[binned["time_bin_start_s"] < (baseline_end_s or 0), "median_ratio"].mean()
            if baseline_end_s
            else binned["median_ratio"].iloc[: max(1, len(binned) // 10)].mean()
        )
        peak_ratio = binned["median_ratio"].max()
        peak_time = binned.loc[binned["median_ratio"].idxmax(), "time_bin_start_s"] if len(binned) else float("nan")

        row = {
            "filename": fcs.filename,
            "gates_applied": ";".join(applied_names) if applied_names else "none",
            "n_total_events": len(fcs.df),
            "n_gated_events": int(mask.sum()),
            "baseline_end_s": baseline_end_s,
            "baseline_ratio": baseline_ratio,
            "peak_ratio": peak_ratio,
            "peak_time_s": peak_time,
            "fold_change": (peak_ratio / baseline_ratio) if baseline_ratio else float("nan"),
        }
        if args.pos_threshold is not None:
            stats = value_stats(ratio, args.pos_threshold, n_total=len(fcs.df), n_gated=len(gated_df))
            row.update(
                {
                    "pos_threshold": args.pos_threshold,
                    "n_positive": stats["n_positive"],
                    "pct_positive": stats["pct_positive"],
                    "n_negative": stats["n_negative"],
                    "pct_negative": stats["pct_negative"],
                }
            )
        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    summary_suffix = f"_{args.gate_set_name}" if args.gate_set_name else ""
    safe_summary_suffix = "".join(c if c.isalnum() or c in "-_" else "_" for c in summary_suffix)
    summary_path = os.path.join(args.output_dir, f"summary{safe_summary_suffix}.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\nWrote summary for {len(summary_rows)} files to {summary_path}")


if __name__ == "__main__":
    main()
