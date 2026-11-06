"""Verify an installed wheel with Python network connections forbidden.

Use a clean venv populated from the local wheelhouse; set fresh pystencils
config/cache locations before invocation. This does not certify Windows.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import socket
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    def denied(*a, **kw):
        raise AssertionError("Network connection forbidden during offline verification")
    socket.create_connection = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    import aerokinetic
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), aerokinetic.__file__
    assert aerokinetic.__version__ == "0.4.0"
    print("Testing installed wheel:", aerokinetic.__file__, flush=True)
    import pytest
    root = Path(__file__).resolve().parents[1]
    status = pytest.main([str(root / "tests"), "-q", "-o", "pythonpath="])
    if status:
        raise SystemExit(status)
    from aerokinetic import Project, Solver
    from aerokinetic.animation import animate
    from aerokinetic.experiments import compare_run
    from aerokinetic._io import write_json
    project = Project.from_dict({"geometry": {"naca0012": {"n_side": 31}},
        "flow": {"reynolds": 30000, "alpha_deg": 5, "physical_chord_m": 0.15},
        "grid": {"cells_per_chord": 16, "upstream_chords": 1, "downstream_chords": 2,
                 "transverse_chords": 1, "memory_limit_gib": 1},
        "run": {"duration_convective": 0.05, "sample_every": 2, "frame_every": 5,
                "check_every": 2, "checkpoint_every": 5, "average_after": 0.025}})
    solver = Solver(project.section, project.flow, project.grid)
    summary = solver.run(output=args.output, **asdict(project.run))
    assert summary["status"] == "completed"
    animate(args.output, field="pressure_pa")
    comparison = compare_run(args.output, "ohtake_re30000")
    assert comparison["status"] == "comparison_only"
    write_json(args.output / "experiment_ohtake_re30000.json", comparison)
    write_json(args.output / "OFFLINE_CHECK.json", {
        "status": "passed", "installed_package": aerokinetic.__file__,
        "python_network_connections": "blocked", "calculation": "completed",
        "checkpoint": "saved", "pressure_pa_gif": "created",
        "experimental_comparison_export": "created",
        "aerodynamic_accuracy": "not_validated; coarse startup example only"})
    print("Offline installed-wheel calculation, checkpoint, physical-pressure GIF and reference export passed.", flush=True)


if __name__ == "__main__":
    main()
