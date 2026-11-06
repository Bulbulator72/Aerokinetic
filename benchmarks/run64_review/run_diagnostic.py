"""Reproduce supplied 64-cell settings, then inspect a later transient.

This is a coarse numerical diagnostic, not a wind-tunnel validation or a
reconstruction of the user's missing history.csv. All physics and geometry
are loaded from the supplied project. A 15-minute computation budget only
limits this diagnostic; it is not a convergence criterion.
"""
from dataclasses import asdict
import json
from pathlib import Path
import resource
import time

from aerokinetic import Project, Solver


def main():
    root = Path(__file__).resolve().parent
    project = Project.load(root / "source_project.json")
    plan = project.plan()
    assert plan.estimated_memory_gib < 3
    print(json.dumps({"project_fingerprint": project.case_fingerprint,
                      "plan": plan.as_dict(), "execution": "Linux CPU, original one thread",
                      "meaning": "diagnostic only; user history not supplied"}, indent=2), flush=True)
    started = time.monotonic()
    solver = Solver(project.section, project.flow, project.grid, progress=lambda m: print(m, flush=True))
    def progress(_, row):
        if int(row["step"]) % 800 == 0:
            print(json.dumps({"t_star": row["t_star"], "cl": row["cl"],
                              "wall_seconds": time.monotonic() - started}), flush=True)
        return time.monotonic() - started < 900
    original = solver.run(output=root / "reproduced_t2", callback=progress, **asdict(project.run))
    print("REPRODUCED_T2", json.dumps(original), flush=True)
    later = None
    if original["status"] == "completed" and time.monotonic() - started < 700:
        later = solver.run(output=root / "continued_t6", duration_convective=4, average_after=3,
                           sample_every=20, frame_every=400, check_every=20,
                           checkpoint_every=0, callback=progress)
        print("LATER_TRANSIENT_NOT_VALIDATION", json.dumps(later), flush=True)
    (root / "execution.json").write_text(json.dumps({
        "original": original, "later_transient": later,
        "peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "wall_seconds_including_compilation": time.monotonic() - started,
        "core_changes": False,
        "experimental_validation": "not_performed",
        "user_history_reproduction": "not_tested; only project.json supplied"
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
