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
                or meta.get("numerics_revision") != 1):
            raise ValueError("Unsupported checkpoint format or numerical revision")
        if meta["versions"] != _versions():
            raise ValueError("Checkpoint dependency versions differ; use its original offline environment")
        project = Project.from_dict(meta["project"])
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
        from .convergence import restore_control
        restore_control(solver,saved,meta)
        solver.diagnostics()
    return solver
