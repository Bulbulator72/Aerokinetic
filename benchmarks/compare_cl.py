"""Compare completed single-NACA0012 runs against digitized Re=30000 Fig.5.

This produces differences, not a certificate of mesh or experimental validation.
"""
import argparse
import csv
import json
from pathlib import Path
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--reference", type=Path, default=Path(__file__).resolve().parents[1] / "references/ohtake2007_fig5_cl_re30000.csv")
    parser.add_argument("--output", type=Path, default=Path("comparison.csv"))
    args = parser.parse_args()
    ref = np.genfromtxt(args.reference, delimiter=",", names=True)
    if not np.all(ref["reynolds"] == 30000):
        parser.error("This comparison requires the Re=30000 reference")
    rows = []
    for run in args.runs:
        meta = json.loads((run / "metadata.json").read_text())
        summary = json.loads((run / "summary.json").read_text())
        if meta["flow"]["reynolds"] != 30000 or len(meta["contours"]) != 1:
            parser.error(f"{run}: benchmark requires a single NACA0012 at Re=30000")
        name = meta["contours"][0]["name"].lower().replace(" ", "")
        if "0012" not in name:
            parser.error(f"{run}: geometry is not identified as NACA0012")
        if summary["status"] != "completed" or not summary["mean"]:
            parser.error(f"{run}: no completed averaged result")
        alpha = meta["flow"]["alpha_deg"]
        if not ref["alpha_deg"].min() <= alpha <= ref["alpha_deg"].max():
            parser.error(f"{run}: angle outside the digitized range; extrapolation is disabled")
        cl_ref = float(np.interp(alpha, ref["alpha_deg"], ref["cl"]))
        cl = summary["mean"]["cl"]
        rows.append({"run": str(run), "alpha_deg": alpha, "cl": cl, "cl_reference_interpolated": cl_ref,
                     "delta_cl": cl - cl_ref, "reference_digitization_cl_bound": 0.008,
                     "averaging_samples": summary["averaging_samples"], "validation_status": "comparison_only"})
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(args.output)


if __name__ == "__main__":
    main()
