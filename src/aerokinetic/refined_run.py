"""Single-point run output and synchronized stop/restart for the refined solver."""
from dataclasses import asdict
import csv
import json
import math
import os
from pathlib import Path
import shutil
import uuid

import numpy as np

from ._io import file_hash, write_json
from .refined_solver import load_refined_project, refined_project_dict
from .project import RunSettings
from .convergence import RunControl,validate_history


def run(solver, output, *, callback=None, stop_event=None, resume=False, residual_callback=None):
    from . import __version__
    from .refined_visualization import save_frame
    output = Path(output)
    settings = solver.project.run
    from .timing import duration_steps
    requested = duration_steps(settings.duration_convective, solver.coarse_level.dt)
    if not resume and output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new/empty refined result directory")
    output.mkdir(parents=True, exist_ok=True)
    (output / "frames").mkdir(exist_ok=resume)
    history = output / "history.csv"
    columns = ["step", "t_star", "cl", "cd", "cm"]
    if solver.plan.dt_seconds is not None:
        columns.append("time_s")
    end_step = solver.steps + requested
    if resume:
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        context = solver._checkpoint_context
        metadata['resumed_with_version']=__version__
        original, refinement = load_refined_project(output / "project.json")
        if (original.case_fingerprint != solver.project.case_fingerprint or refinement != solver.refinement
                or original.run != settings or metadata["case_fingerprint"] != solver.case_fingerprint
                or RunSettings(**metadata["run"]) != settings or context.get("case_fingerprint") != solver.case_fingerprint):
            raise ValueError("Refined project/run settings changed; cannot mix histories")
        if metadata["status"] == "completed":
            raise ValueError("The requested refined run has already completed")
        end_step = context["end_step"]
        prefix = context["history_bytes"]
        if file_hash(history, prefix) != context["history_sha256"]:
            raise ValueError("Refined history before checkpoint changed")
        validate_history(output,context)
        future = [p for p in (output / "frames").glob("frame_*.npz") if int(p.stem.split("_")[-1]) > solver.steps]
        if history.stat().st_size > prefix or future:
            recovery = output / "recovery" / uuid.uuid4().hex
            recovery.mkdir(parents=True)
            shutil.copy2(history, recovery / "history_before_resume.csv")
            with history.open("r+b") as handle:
                handle.truncate(prefix)
            for path in future:
                shutil.move(str(path), str(recovery / path.name))
    else:
        metadata = {"format": "aerokinetic_refined_run", "version": __version__,
                    "case_fingerprint": solver.case_fingerprint, "flow": asdict(solver.flow),
                    "grid": asdict(solver.grid), "refinement": asdict(solver.refinement),
                    "plan": solver.plan.as_dict(), "run": asdict(settings), "start_step": solver.steps,
                    "contours": [{"name": e.name, "xy": e.xy.tolist()} for e in solver.section.elements],
                    "coefficient_convention": "Cl up; Cd downstream; Cm nose-up about the chosen reference; area=c*periodic_span",
                    "force_time": "average of both fine momentum-transfer intervals; timestamp at coarse interval midpoint",
                    "force_sampling": "one coarse-interval average at each scheduled sample; not a claim of resolved fluctuation frequency",
                    "experimental_validation": "not_performed", "resume_supported": True,
                    "reference_chord_input_units": solver.section.chord,
                    "moment_reference_input_units": solver.section.moment_reference}
        write_json(output / "project.json", refined_project_dict(solver.project, solver.refinement))
        with history.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=columns).writeheader()
        save_frame(solver, output / "frames" / f"frame_{solver.steps:09d}.npz")
    control=RunControl(solver,settings,output,resume=resume,callback=residual_callback,
                       start_step=metadata.get('start_step',solver.steps))
    metadata.update(status="running", target_steps=end_step)
    metadata['residual_definition']='Volume-weighted L2/Linf of d(rho,j_common)/dt*, owned fluid only; integral balance defects of the last coarse step are a separate diagnostic'
    write_json(output / "metadata.json", metadata)
    status, error = "completed", None
    reason='duration_reached'

    def checkpoint():
        context = {"case_fingerprint": solver.case_fingerprint, "end_step": end_step,
                   "history_bytes": history.stat().st_size, "history_sha256": file_hash(history)}
        context.update(control.checkpoint_context())
        solver.save_checkpoint(output / "checkpoint.npz", context)

    try:
        with history.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            while solver.steps < end_step:
                if stop_event is not None and stop_event.is_set():
                    status = "stopped"
                    reason='user_stopped'
                    break
                record = solver.step()
                if solver.steps % settings.check_every == 0:
                    solver.diagnostics()
                    control.observe()
                if solver.steps % settings.sample_every == 0:
                    writer.writerow({k: record[k] for k in columns})
                    handle.flush()
                    if callback is not None and callback(solver, record) is False:
                        status = "stopped"
                        reason='user_stopped'
                if solver.steps % settings.frame_every == 0:
                    save_frame(solver, output / "frames" / f"frame_{solver.steps:09d}.npz")
                if settings.checkpoint_every and solver.steps % settings.checkpoint_every == 0:
                    handle.flush()
                    os.fsync(handle.fileno())
                    checkpoint()
                if status == "stopped":
                    break
                selected_reason=control.stop_reason()
                if selected_reason:
                    reason=selected_reason
                    if reason=='wall_time_limit':status='stopped'
                    break
            handle.flush()
            os.fsync(handle.fileno())
        solver.diagnostics()
        save_frame(solver, output / "frames" / f"frame_{solver.steps:09d}.npz")
        checkpoint()
    except Exception as failure:
        status, error = "failed", f"{type(failure).__name__}: {failure}"
        reason='failed'
    with history.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    selected = [r for r in rows if float(r["t_star"]) >= settings.average_after]
    mean = {name: float(np.mean([float(r[name]) for r in selected])) for name in ("cl", "cd", "cm")} if selected else None
    rms = {name: float(np.std([float(r[name]) for r in selected])) for name in ("cl", "cd", "cm")} if selected else None
    final_diagnostics = {"state": "invalid"}
    if solver.initialized:
        try:
            final_diagnostics = solver.diagnostics()
        except Exception as failure:
            status, error = "failed", f"{type(failure).__name__}: {failure}"
            reason='failed'
            solver.initialized = False
    summary = {"status": status, "steps": solver.steps, "fine_steps": solver.fine_steps,
               "t_star": solver.time, "average_after_t_star": settings.average_after,
               "samples": len(rows), "averaging_samples": len(selected), "mean": mean, "rms": rms,
               "experimental_validation": "not_performed", "statistical_convergence": "not_established",
               "diagnostics": final_diagnostics}
    summary.update(control.summary(reason))
    control.close()
    if error:
        summary["error"] = error
    metadata.update(status=status, completed_steps=solver.steps)
    metadata['termination_reason']=reason
    write_json(output / "metadata.json", metadata)
    write_json(output / "summary.json", summary)
    if error:
        raise RuntimeError(error)
    return summary


def resume(solver_type, output, **kwargs):
    output = Path(output)
    solver = solver_type.from_checkpoint(output / "checkpoint.npz", progress=kwargs.pop("progress", None))
    return run(solver, output, resume=True, **kwargs)
