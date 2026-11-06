"""Measured wind-tunnel references; no third-party CFD solutions are used."""
import csv
import hashlib
from importlib.resources import files
import io
import json
import math
from pathlib import Path
import numpy as np

from .geometry import Contour, Section


def catalog():
    return json.loads(files("aerokinetic").joinpath("data/experiments.json").read_text(encoding="utf-8"))


def load_series(identifier):
    meta = next((s for s in catalog() if s["id"] == identifier), None)
    if meta is None:
        raise ValueError("Unknown wind-tunnel series")
    data = files("aerokinetic").joinpath("data/" + meta["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise ValueError("Wind-tunnel dataset hash does not match its catalog")
    rows = [{key: float(value) for key, value in row.items()} for row in
            csv.DictReader(io.StringIO(data.decode("utf-8-sig")))]
    if any(rows[i]["alpha_deg"] >= rows[i + 1]["alpha_deg"] for i in range(len(rows) - 1)):
        raise ValueError("Reference angles must increase strictly within a single series")
    return meta, rows


def moment_at_reference(cm, cl, cd, alpha_deg, old_reference, new_reference, chord):
    """Transfer a nose-up aerodynamic moment between points in the body frame."""
    a = math.radians(alpha_deg)
    fx, fy = cd * math.cos(a) - cl * math.sin(a), cd * math.sin(a) + cl * math.cos(a)
    dx, dy = (np.asarray(old_reference) - new_reference) / chord
    return float(cm - (dx * fy - dy * fx))


def compare_run(directory, identifier):
    """Preliminary differences with explicitly incomplete physical validation.

    Match Re and known Mach; reject extrapolation and non-single-NACA0012 cases.
    Geometry identity and tunnel effects are not certified by an airfoil name.
    """
    directory = Path(directory)
    meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    reference, samples = load_series(identifier)
    if summary["status"] != "completed" or not summary["mean"] or summary["averaging_samples"] < 2:
        raise ValueError("Need a completed calculation with at least two averaged samples")
    if not math.isclose(meta["flow"]["reynolds"], reference["reynolds"], rel_tol=1e-10):
        raise ValueError(f"Re mismatch: calculation {meta['flow']['reynolds']:g}, wind tunnel {reference['reynolds']:g}")
    if len(meta["contours"]) != 1 or "0012" not in meta["contours"][0]["name"].replace(" ", ""):
        raise ValueError("This wind-tunnel reference requires a single NACA0012 without a flap")
    from .config import Atmosphere, Flow
    flow_values = dict(meta["flow"])
    flow_values["atmosphere"] = Atmosphere(**flow_values.get("atmosphere", {}))
    flow = Flow(**flow_values)
    if reference["mach"] is not None and (flow.physical_mach is None or
            not math.isclose(flow.physical_mach, reference["mach"], abs_tol=1e-6)):
        raise ValueError("Known experimental Mach must match the physical calculation Mach")
    alpha = flow.alpha_deg
    angles = [r["alpha_deg"] for r in samples]
    if not angles[0] <= alpha <= angles[-1]:
        raise ValueError("Angle is outside the measured range; extrapolation is disabled")
    section = Section(tuple(Contour(e["xy"], e["name"]) for e in meta["contours"]),
                      chord=meta["reference_chord_input_units"])
    calculated = dict(summary["mean"])
    calculated["cm_c4"] = moment_at_reference(calculated["cm"], calculated["cl"], calculated["cd"], alpha,
                                             meta["moment_reference_input_units"], section.moment_reference,
                                             section.chord)
    rows = []
    for coefficient in reference["coefficients"]:
        value = float(np.interp(alpha, angles, [r[coefficient] for r in samples]))
        calc = float(calculated[coefficient])
        rows.append({"coefficient": coefficient, "calculated": calc,
                     "experimental_interpolated": value, "difference": calc - value,
                     "relative_difference_percent": 100 * (calc - value) / value if coefficient == "cd" and value else None})
    return {"reference": reference, "alpha_deg": alpha, "reynolds": flow.reynolds,
            "differences": rows, "status": "comparison_only",
            "validation": "inconclusive",
            "limitations": ["No grid/domain/span/time/statistical convergence is certified by this comparison.",
                            "Experimental geometry, transition, turbulence and tunnel corrections need independent agreement.",
                            "Interpolated/digitized values do not have zero experimental uncertainty."]}
