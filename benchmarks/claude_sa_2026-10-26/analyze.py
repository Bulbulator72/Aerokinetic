"""Reproduce a preliminary comparison; no CFD solver is run or modified.

Run from any directory: python path/to/analyze.py
All inputs are local. The original user CSV is preserved byte for byte.
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE = (HERE.parent.parent / "references/nasa_tmr/"
             "Ladson_TM4074_table_IX_Re5p95m_80W.csv")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interpolate(rows, alpha, coefficient):
    angles = [float(row["alpha_deg"]) for row in rows]
    if not angles[0] <= alpha <= angles[-1]:
        raise ValueError("Extrapolation of experimental data is forbidden")
    i = bisect.bisect_left(angles, alpha)
    if i < len(angles) and angles[i] == alpha:
        return float(rows[i][coefficient]), [alpha, alpha]
    lo, hi = rows[i - 1], rows[i]
    a0, a1 = angles[i - 1], angles[i]
    t = (alpha - a0) / (a1 - a0)
    return ((1.0 - t) * float(lo[coefficient]) + t * float(hi[coefficient]),
            [a0, a1])


def main():
    raw = HERE / "polar_original.csv"
    with raw.open(encoding="utf-8-sig", newline="") as f:
        samples = list(csv.DictReader(f, delimiter=";"))
    with REFERENCE.open(encoding="utf-8", newline="") as f:
        reference = list(csv.DictReader(f))
    if len(samples) != 2 or [float(r["alpha"]) for r in samples] != [10.0, 12.0]:
        raise ValueError("This report describes the received two-point 10/12-degree CSV")
    for row in samples:
        if row["метод"] != "rans" or float(row["M"]) != 0.15 or float(row["Re"]) != 6e6:
            raise ValueError("Unexpected solver/Re/M; recheck the comparison setup")
        if not all(math.isfinite(float(row[k])) for k in ("alpha", "Cl", "Cd", "Cm")):
            raise ValueError("Nonfinite input coefficient")
    result = []
    for row in samples:
        alpha = float(row["alpha"])
        for column, ref_column in (("Cl", "cl"), ("Cd", "cd"), ("Cm", "cm_c4")):
            exp, bracket = interpolate(reference, alpha, ref_column)
            calc = float(row[column])
            result.append({
                "alpha_deg": alpha, "coefficient": column,
                "calculated": calc, "experimental_interpolated": exp,
                "calculated_minus_experimental": calc - exp,
                # Cd has a nonzero, positive reference; report absolute Cl/Cm errors.
                "relative_difference_percent": 100.0 * (calc - exp) / exp if column == "Cd" else "",
                "reference_lower_alpha_deg": bracket[0], "reference_upper_alpha_deg": bracket[1],
                "calculation_re": float(row["Re"]), "experiment_re": 5.95e6,
                "mach": 0.15, "reported_converged": row["надёжно"],
                "validation_status": "inconclusive",
            })
    with (HERE / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)

    first, second = samples
    alpha0, alpha1 = float(first["alpha"]), float(second["alpha"])
    delta = math.radians(alpha1 - alpha0)
    cl0, cd0 = float(first["Cl"]), float(first["Cd"])
    cl1, cd1 = float(second["Cl"]), float(second["Cd"])
    projected_cl = cl0 * math.cos(delta) - cd0 * math.sin(delta)
    projected_cd = cd0 * math.cos(delta) + cl0 * math.sin(delta)

    def body_force(row):
        a = math.radians(float(row["alpha"]))
        cl, cd = float(row["Cl"]), float(row["Cd"])
        return [cd * math.cos(a) - cl * math.sin(a),
                cd * math.sin(a) + cl * math.cos(a)]

    f0, f1 = body_force(first), body_force(second)
    diagnostics = {
        "original_csv_sha256": digest(raw), "reference_csv_sha256": digest(REFERENCE),
        "comparison_kind": "preliminary_Claude_RANS_vs_wind_tunnel",
        "reference_interpolation": "piecewise_linear_in_alpha_within_one_series_no_extrapolation",
        "re_difference_percent_relative_to_experiment": (6e6 / 5.95e6 - 1) * 100,
        "rotation_only_prediction_at_12": {"cl": projected_cl, "cd": projected_cd},
        "actual_minus_rotation_prediction_at_12": {"cl": cl1 - projected_cl, "cd": cd1 - projected_cd},
        "body_axis_force_at_10": f0, "body_axis_force_at_12": f1,
        "relative_body_force_change": math.hypot(f1[0] - f0[0], f1[1] - f0[1]) / math.hypot(*f0),
        "cm_change": float(second["Cm"]) - float(first["Cm"]),
        "suspicion": "premature_convergence_after_warm_start; not_confirmed_by_CFD_reproduction",
        "iteration_history_available": False, "cp_available": False,
        "solver_modified": False, "CFD_reproduction": "not_run",
        "new_kinetic_solver_validation": "not_run",
    }
    (HERE / "diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"comparison": result, "diagnostics": diagnostics}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
