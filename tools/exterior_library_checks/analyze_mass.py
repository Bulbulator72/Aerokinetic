"""Describe completed mass exchanges from cumulative, unsampled step totals.

No zero-wall-flux or aerodynamic acceptance criterion is inferred here.
The force timestamp is half a step behind the cumulative state timestamp.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def analyze(root, start, end):
    metadata = json.loads((root / "metadata.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    if summary["status"] != "completed":
        raise ValueError("Only complete runs may enter the fixed-window analysis")
    if metadata["grid"]["farfield"] != "exterior_regularized_fd_v1":
        raise ValueError("Mass column definitions require the explicit exterior model")
    dt = metadata["plan"]["dt_convective"]
    endpoints = [round(t / dt) for t in (start, end)]
    if not all(abs(step * dt - t) <= 1e-12 for step, t in zip(endpoints, (start, end))):
        raise ValueError("Window endpoints must coincide with completed states")
    if endpoints[0] <= 0 or endpoints[1] <= endpoints[0]:
        raise ValueError("Require a positive, increasing window with both saved endpoints")
    history = root / "history.csv"
    with history.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    steps = [int(row["step"]) for row in rows]
    if any(b <= a for a, b in zip(steps, steps[1:])):
        raise ValueError("History steps are not strictly increasing")
    indexed = dict(zip(steps, rows))
    if not all(step in indexed for step in endpoints):
        raise ValueError("Missing cumulative endpoint; do not approximate from sampled instantaneous values")
    selected = [row for row in rows if endpoints[0] <= int(row["step"]) <= endpoints[1]]
    for row in selected:
        step = int(row["step"])
        if abs(float(row["t_star"]) - (step - .5) * dt) > 1e-12:
            raise ValueError("Force and cumulative state clocks are inconsistent")
        for key in ("wall_mass_exchange_lattice", "wall_mass_exchange_accumulated",
                    "exterior_source_mass_lattice", "exterior_source_mass_accumulated"):
            if not math.isfinite(float(row[key])):
                raise ValueError(f"Non-finite {key}")
    intervals = endpoints[1] - endpoints[0]
    _, ny, nz = metadata["plan"]["shape"]
    inlet_reference = metadata["flow"]["lattice_speed"] * ny * nz
    result = {"run": root.name, "state_window_t_star": [start, end],
              "state_endpoint_steps": endpoints, "step_intervals": intervals,
              "units": "lattice mass; reference rho=1", "reference_inlet_mass_per_step": inlet_reference,
              "history_sha256": hashlib.sha256(history.read_bytes()).hexdigest()}
    for name, column in (("wall", "wall_mass_exchange_accumulated"),
                         ("exterior", "exterior_source_mass_accumulated")):
        values = [float(indexed[step][column]) for step in endpoints]
        difference = values[1] - values[0]
        result[name] = {"cumulative_endpoints": values, "net_mass": difference,
                        "mean_mass_per_step": difference / intervals,
                        "mean_over_reference_inlet": difference / intervals / inlet_reference}
    result["input_checks_passed"] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=float, default=6.)
    parser.add_argument("--end", type=float, default=12.)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    cases = [analyze(args.runs / f"airfoil_down{distance}", args.start, args.end) for distance in (3, 6)]
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"cases": cases, "input_checks_passed": True,
              "wall_impermeability": "not_established", "aerodynamic_validation": "inconclusive",
              "method": "Cumulative endpoint differences, not sampled-rate quadrature",
              "limits": ["Wall exchange is incoming minus outgoing populations, positive into fluid.",
                         "Exterior source belongs to the auxiliary layer, not the physical fluid core.",
                         "The two totals alone are not a fluid-domain balance: open-boundary flux is also required.",
                         "Reference inlet flux is a scale at rho=1, not a measured flux or an acceptance threshold."]}
    (args.output / "ANALYSIS.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    with (args.output / "window_mass.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("case", "source", "window_start", "window_end", "net_mass", "mean_mass_per_step", "mean_over_reference_inlet"))
        for case in cases:
            for name in ("wall", "exterior"):
                writer.writerow((case["run"], name, args.start, args.end,
                                 case[name]["net_mass"], case[name]["mean_mass_per_step"],
                                 case[name]["mean_over_reference_inlet"]))
    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "exterior-mass-20261108"})
        figure, axes = plt.subplots(2, 1, figsize=(10, 7))
        for case, color in zip(cases, ("#1768ac", "#17845e")):
            with (args.runs / case["run"] / "history.csv").open(newline="") as stream:
                indexed = {int(row["step"]): row for row in csv.DictReader(stream)}
            first, last = case["state_endpoint_steps"]
            dt = args.start / first
            selected = [row for step, row in indexed.items() if first <= step <= last]
            for name, style in (("wall", "-"), ("exterior", "--")):
                label = case["run"] + " " + name
                field = "wall_mass_exchange" if name == "wall" else "exterior_source_mass"
                scale = case["reference_inlet_mass_per_step"]
                axes[0].plot([float(row["t_star"]) for row in selected],
                             [100 * float(row[field + "_lattice"]) / scale for row in selected],
                             color=color, linestyle=style, label=label)
                baseline = float(indexed[first][field + "_accumulated"])
                axes[1].plot([int(row["step"]) * dt for row in selected],
                             [float(row[field + "_accumulated"]) - baseline for row in selected],
                             color=color, linestyle=style, label=label)
        axes[0].set(xlabel="Force interval midpoint t U / c", ylabel="Per-step exchange / reference inlet (%)")
        axes[1].set(xlabel="Completed state t U / c", ylabel="Cumulative mass since window start (lattice)")
        for axis in axes:
            axis.grid(alpha=.2); axis.legend(fontsize=8, ncol=2)
            axis.set_xlim(args.start, args.end)
        figure.suptitle("Numerical mass exchanges; auxiliary source is not physical-domain open flux")
        figure.tight_layout(); figure.savefig(args.output / "mass_exchange.svg", metadata={"Date": None})
        plt.close(figure)
        path = args.output / "mass_exchange.svg"
        path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
