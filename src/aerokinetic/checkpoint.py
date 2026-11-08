"""D3Q27 CPU checkpoints including the history-dependent outflow boundary.

The private index API is intentionally tied to lbmpy/pystencils 1.4. Saving
only macroscopic variables or PDFs would not reproduce this outflow.
"""
from importlib.metadata import version
import json
from pathlib import Path
import numpy as np

from ._io import atomic_binary
from .project import Project
from .config import EXTERIOR_FARFIELD


def _versions():
    return {name: version(name) for name in ("lbmpy", "pystencils", "numpy")}


def _boundaries(solver):
    handling = solver._stepper.boundary_handling
    handling.prepare()
    result = {}
    for block in solver._stepper.data_handling.iterate(ghost_layers=1):
        for boundary, array in block[handling._index_array_name].boundary_object_to_index_list.items():
            if boundary.name in result:
                raise RuntimeError("Checkpoint requires unique boundaries in serial data handling")
            result[boundary.name] = array
    return result


def save_checkpoint(solver, path, context=None):
    if solver.grid.target != "cpu":
        raise ValueError("Restart checkpoints are currently verified for the CPU backend only")
    boundaries = _boundaries(solver)
    fields = solver._stepper.data_handling.cpu_arrays
    project = Project(solver.section, solver.flow, solver.grid)
    metadata = {"format": "aerokinetic_cpu_checkpoint", "schema_version": 1,
                "numerics_revision": 1, "versions": _versions(), "project": project.as_dict(),
                "case_fingerprint": project.case_fingerprint, "steps": solver.steps,
                "fields": sorted(fields), "boundaries": sorted(boundaries),
                "wall_fallback_links": solver.wall_fallback_links, "context": context or {}}
    arrays = {}
    arrays.update({f"field_{i}": fields[name] for i, name in enumerate(metadata["fields"])})
    arrays.update({f"boundary_{i}": boundaries[name] for i, name in enumerate(metadata["boundaries"])})
    exterior = getattr(solver, "_exterior", None)
    if exterior is not None:
        from .exterior import STATE_SCHEMA
        metadata["numerics_revision"] = 2
        metadata["exterior_state"] = {"model": EXTERIOR_FARFIELD, "state_schema": STATE_SCHEMA,
            "pdf_stage": "pre_collision", "step_count": exterior.step_count,
            "initial_ready": exterior.initial is not None, "last_report": exterior.last_report}
        if exterior.step_count != solver.steps:
            raise ValueError("Exterior and solver checkpoint clocks differ")
        if exterior.initial is None and solver.steps != 0:
            raise ValueError("Missing exterior incoming-trace state")
        if exterior.initial is not None:
            arrays["exterior_initial"] = exterior.initial
        arrays["exterior_source_total"] = exterior.source_total
        arrays["exterior_wall_mass_state"] = np.array([solver._wall_mass_exchange, solver._wall_mass_total])
    from .convergence import save_control
    save_control(solver,arrays,metadata)
    arrays['metadata']=np.asarray(json.dumps(metadata,ensure_ascii=False,allow_nan=False))
    # Uncompressed ZIP avoids compression time and preserves structured dtypes.
    # Replacement is atomic; an interrupted write leaves the previous file usable.
    with atomic_binary(path) as handle:
        np.savez(handle, **arrays)
    return Path(path)


def restore_checkpoint(solver_type, path, progress=None):
    with np.load(path, allow_pickle=False) as saved:
        meta = json.loads(str(saved["metadata"]))
        if (meta.get("format") != "aerokinetic_cpu_checkpoint" or meta.get("schema_version") != 1
                or meta.get("numerics_revision") not in (1, 2)):
            raise ValueError("Unsupported checkpoint format or numerical revision")
        if meta["versions"] != _versions():
            raise ValueError("Checkpoint dependency versions differ; use its original offline environment")
        project = Project.from_dict(meta["project"])
        is_exterior = project.grid.farfield == EXTERIOR_FARFIELD
        if is_exterior != (meta["numerics_revision"] == 2):
            raise ValueError("Checkpoint numerical phase/model revision differs")
        if is_exterior:
            from .exterior import STATE_SCHEMA
            state = meta.get("exterior_state", {})
            if (state.get("model") != EXTERIOR_FARFIELD or state.get("state_schema") != STATE_SCHEMA
                    or state.get("pdf_stage") != "pre_collision" or state.get("step_count") != meta["steps"]
                    or type(state.get("initial_ready")) is not bool):
                raise ValueError("Checkpoint exterior phase/signature is inconsistent")
        if project.grid.target != "cpu" or project.case_fingerprint != meta["case_fingerprint"]:
            raise ValueError("Checkpoint physical/numerical case is inconsistent")
        if type(meta["steps"]) is not int or meta["steps"] < 0:
            raise ValueError("Invalid checkpoint step count")
        solver = solver_type(project.section, project.flow, project.grid, progress=progress)
        boundaries = _boundaries(solver)
        fields = solver._stepper.data_handling.cpu_arrays
        if sorted(fields) != meta["fields"] or sorted(boundaries) != meta["boundaries"]:
            raise ValueError("Checkpoint fields or boundaries do not match the rebuilt case")
        for label, names, current in (("field", meta["fields"], fields),
                                       ("boundary", meta["boundaries"], boundaries)):
            for i, name in enumerate(names):
                array = saved[f"{label}_{i}"]
                target = current[name]
                if array.shape != target.shape or array.dtype != target.dtype:
                    raise ValueError(f"Checkpoint shape/dtype mismatch: {name}")
                if label == "boundary":
                    for coordinate in ("x", "y", "z", "dir"):
                        if not np.array_equal(array[coordinate], target[coordinate]):
                            raise ValueError(f"Checkpoint boundary geometry mismatch: {name}")
                np.copyto(target, array)
        solver.steps = meta["steps"]
        solver._stepper.time_steps_run = solver.steps
        solver.wall_fallback_links = meta["wall_fallback_links"]
        solver._checkpoint_context = meta["context"]
        if is_exterior:
            exterior = solver._exterior
            if state["initial_ready"]:
                initial = saved["exterior_initial"]
                if initial.shape != (*solver._storage_shape, 4) or initial.dtype != np.float64 or not np.isfinite(initial).all():
                    raise ValueError("Invalid exterior incoming-trace checkpoint field")
                exterior.initial = initial.copy()
            elif solver.steps != 0:
                raise ValueError("Missing exterior incoming-trace checkpoint field")
            for key, shape in (("exterior_source_total", (4,)), ("exterior_wall_mass_state", (2,))):
                value = saved[key]
                if value.shape != shape or value.dtype != np.float64 or not np.isfinite(value).all():
                    raise ValueError("Invalid exterior checkpoint diagnostics")
            exterior.step_count = solver.steps
            exterior.source_total[...] = saved["exterior_source_total"]
            exterior.last_report = state["last_report"]
            solver._wall_mass_exchange, solver._wall_mass_total = map(float, saved["exterior_wall_mass_state"])
        from .convergence import restore_control
        restore_control(solver,saved,meta)
        solver.diagnostics()
    return solver
