"""Cross-interpreter equivalence against the frozen7Nov prototype.

prepare runs in the unchanged0.5.2 environment; compare runs in the installed
new wheel. Local PDF fixtures are not publication artifacts.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")


def links(solver, padding):
    result = []
    step = solver._stepper
    step.boundary_handling.prepare()
    for block in step.data_handling.iterate(ghost_layers=1):
        arrays = block[step.boundary_handling._index_array_name].boundary_object_to_index_list
        for element, wall in enumerate(solver._wall_objects):
            for row in arrays[wall]:
                result.append((element, *(int(row[k])-p for k,p in zip(("x","y","z"),padding)),
                               int(row["dir"]), float(row["q"])))
    return np.asarray(sorted(result))


def snapshot(path, solver):
    step, model = solver._stepper, solver.exterior
    rho, velocity = model.macros()
    arrays = dict(src=step.data_handling.cpu_arrays[step.pdf_array_name],
                  dst=step.data_handling.cpu_arrays[step._tmp_arr_name], step=solver.steps,
                  rho=rho, velocity=velocity, solid=solver._solid_xy[model.core[:2]],
                  links=links(solver, model.pad))
    if model.initial is not None:
        arrays["initial"] = model.initial
    np.savez(path, **arrays)


def prepare(a):
    import aerokinetic
    from aerokinetic import Project
    if aerokinetic.__version__ != "0.5.2":
        raise ValueError("prepare requires the unchanged frozen0.5.2 interpreter")
    if sha(a.frozen_root/"exterior_fd.py") != "9c0b2f35c79c340f916364c6cd7fff9cf36776df16f1d7a45b03024d7e853c02":
        raise ValueError("Frozen numerical source differs")
    sys.path.insert(0, str(a.frozen_root))
    from airfoil_pilot import AirfoilPilot
    a.output.mkdir(parents=True, exist_ok=False)
    project = Project.load(a.project)
    project = replace(project, grid=replace(project.grid, farfield="legacy", openmp_threads=1))
    project.save(a.output/"input_project.json")
    s = AirfoilPilot(project)
    snapshot(a.output/"initial.npz", s)
    cases = []
    for checkpoint in [None, *a.checkpoint]:
        if checkpoint is not None:
            s.restore_research_checkpoint(checkpoint)
        start = s.steps
        before = a.output/"initial.npz" if checkpoint is None else checkpoint.resolve()
        rows = []
        for _ in range(16):
            load, report = s.step(sample=True, audit=True)
            rows.append({**{k:load[k] for k in ("step","t_star","cl","cd","cm")},
                         "wall_mass_exchange_lattice": report["wall_mass_exchange"],
                         "exterior_source_mass_lattice": report["exterior_source_mass_momentum"][0]})
        after = a.output/f"after_{start:05d}.npz"
        snapshot(after, s)
        cases.append({"start":start,"before":str(before),"before_sha256":sha(before),
                      "after":after.name,"after_sha256":sha(after),"loads":rows})
    write(a.output/"REFERENCE.json", {"version":aerokinetic.__version__, "cases":cases,
          "prototype_sha256":sha(a.frozen_root/"exterior_fd.py"),
          "adapter_sha256":sha(a.frozen_root/"airfoil_pilot.py")})
    print("Prepared frozen reference segments", [r["start"] for r in cases], flush=True)


def compare(a):
    import aerokinetic
    from aerokinetic import Project, Solver
    a.output.mkdir(parents=True, exist_ok=False)
    ref = json.loads((a.reference/"REFERENCE.json").read_text())
    p = Project.load(a.reference/"input_project.json")
    p = replace(p, grid=replace(p.grid, farfield="exterior_regularized_fd_v1"))
    s = Solver(p.section, p.flow, p.grid)
    with np.load(a.reference/"initial.npz", allow_pickle=False) as initial:
        core = tuple(slice(1+pad,1+pad+n) for pad,n in zip(s._padding,s.plan.shape))
        initial_error = float(np.max(abs(s._stepper.data_handling.cpu_arrays[s._stepper.pdf_array_name][core]-initial["src"][core])))
        assert np.array_equal(s._solid_xy,initial["solid"])
    rows = []
    for case in ref["cases"]:
        assert sha(case["before"]) == case["before_sha256"]
        assert sha(a.reference/case["after"]) == case["after_sha256"]
        with np.load(case["before"], allow_pickle=False) as d:
            for key, name in (("src",s._stepper.pdf_array_name),("dst",s._stepper._tmp_arr_name)):
                np.copyto(s._stepper.data_handling.cpu_arrays[name], d[key])
            s.steps = int(d["step"])
            s._stepper.time_steps_run = s.steps
            s._exterior.step_count = s.steps
            s._exterior.initial = d["initial"].copy() if "initial" in d else None
            s._exterior.source_total.fill(0)
            s._wall_mass_exchange = s._wall_mass_total = 0.0
        measured = [s.step(sample=True) for _ in range(16)]
        error = max(abs(r[k]-q[k]) for r,q in zip(measured,case["loads"]) for k in q if k!="step")
        with np.load(a.reference/case["after"], allow_pickle=False) as d:
            pdf_error = max(float(np.max(abs(s._stepper.data_handling.cpu_arrays[name]-d[key])))
                for key,name in (("src",s._stepper.pdf_array_name),("dst",s._stepper._tmp_arr_name)))
            s._stepper.post_run()
            rho = s._stepper.data_handling.gather_array(s._stepper.density_data_name)[s._core_slice]
            velocity = s._stepper.data_handling.gather_array(s._stepper.velocity_data_name)[s._core_slice]
            macro_error = max(float(np.max(abs(rho-d["rho"]))),float(np.max(abs(velocity-d["velocity"]))))
            trace_error = float(np.max(abs(s._exterior.initial-d["initial"])))
            assert np.array_equal(s._solid_xy,d["solid"])
            current = links(s,s._padding)
            assert np.array_equal(current[:,:5],d["links"][:,:5])
            q_error = float(np.max(abs(current[:,5]-d["links"][:,5])))
        rows.append({"start":case["start"],"pdf_max_error":pdf_error,"macro_max_error":macro_error,
                     "load_mass_and_clock_max_error":error,"trace_max_error":trace_error,"wall_q_max_error":q_error})
    result = {"version":aerokinetic.__version__,"installed":aerokinetic.__file__,"segments":rows,
        "initial_physical_pdf_max_error":initial_error,
        "passed":initial_error<=2e-15 and all(r["pdf_max_error"]<=2e-12 and r["macro_max_error"]<=2e-12
            and r["load_mass_and_clock_max_error"]<=2e-10 and r["trace_max_error"]<=2e-12
            and r["wall_q_max_error"]<=2e-12 for r in rows),
        "scope":"Refactor equivalence on frozen startup/nonlinear states, not aerodynamic validation"}
    write(a.output/"CHECKS.json",result)
    print(json.dumps(result,indent=2),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode",required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--frozen-root",type=Path,required=True)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--checkpoint",type=Path,action="append",default=[])
    p.add_argument("--output",type=Path,required=True)
    p = sub.add_parser("compare")
    p.add_argument("--reference",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    (prepare if args.mode=="prepare" else compare)(args)
