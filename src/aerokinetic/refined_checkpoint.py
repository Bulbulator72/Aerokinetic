"""Synchronized CPU restart of both levels and every structured boundary history."""
from importlib.metadata import version
import hashlib
import json
from pathlib import Path

import numpy as np

from ._io import atomic_binary, file_hash
from .project import Project
from .refined_solver import RefinementSettings

# Only this exact released 0.3.0 state is eligible for the verified migration.
# An arbitrary old/edited numerical implementation must still be rejected.
RELEASE_030_IDENTITY={
    'interface.py':'001f26cd119232ecea61b93dd2a2278772bc34d010cd6c14b0b519be9711e67a',
    'refinement.py':'798053ea229960ca48c818e36e737aab8657e076ae9187230379f752d76b6df8',
    'walls.py':'13c8e9812f05390f3afb20f9d6908c00bb6f37ab58515e2d244cf031d64346dd',
    'refined_solver.py':'871807df8f3e61ad41eae2214fc5020072f679cef8e522a1368fe75bb964da94',
    'refined_checkpoint.py':'6090c1303edf608ba92ba200a1d6632c57c2c490548741cbaa94e0dc43feac10'}


# Exact released0.5.1 identity: legacy equations are unchanged by the optional
# farfield setting; verified by saved old-state/manual-continuation fixtures.
RELEASE_051_IDENTITY={'interface.py': '97a8f327ce2d091ba8ca9a5b6870e2d0fa00b4a0961945d130118a0e06c61a2b', 'refinement.py': '798053ea229960ca48c818e36e737aab8657e076ae9187230379f752d76b6df8', 'walls.py': '13c8e9812f05390f3afb20f9d6908c00bb6f37ab58515e2d244cf031d64346dd', 'refined_solver.py': '5b789e0d47cc3c116f8959f3c2d20f39110ba55d01210e7cbc85c20154089019', 'refined_checkpoint.py': 'c2be84a529fb6647f62dbe15b5b0a3862782dd65d35accdace86d2d571860795', 'parallel.py': '97e907d036f169bdc195f101eefcf4d3cb50ff2c7bba4d917b362d2c739d691a', 'convergence.py': 'c4acfcbdc0cd067884e65df1405273f2269e79437aa06dd6fc438f93fb7afd14', 'config.py': 'fa2a3411c5b959a8568a924567bdc1f6746fda30208e35f8a411477d3de65972', 'geometry.py': '30beffe1914547e836fcf636d776a12018878945db817172a9c35c970aef4a94', 'project.py': '503dae52f098f1c370f996512fa5c75daac7e9511f6e19866d0da5291366685c'}


def numerical_identity():
    return {name: file_hash(Path(__file__).with_name(name)) for name in
            ("interface.py", "refinement.py", "walls.py", "refined_solver.py", "refined_checkpoint.py",
             "parallel.py", "convergence.py", "config.py", "geometry.py", "project.py")}


def versions():
    return {name: version(name) for name in ("lbmpy", "pystencils", "numpy", "sympy")}


def boundary_arrays(lbm):
    handling = lbm.boundary_handling
    handling.prepare()
    result = {}
    for block in lbm.data_handling.iterate(ghost_layers=1):
        for boundary, array in block[handling._index_array_name].boundary_object_to_index_list.items():
            if boundary.name in result:
                raise RuntimeError("Serial checkpoints require unique boundary names per level")
            result[boundary.name] = array
    return result


def _hash_array(array):
    return hashlib.sha256(array.tobytes(order="C")).hexdigest()


def save_checkpoint(solver, path, context=None):
    if not solver.initialized or solver.fine_steps != 2 * solver.steps:
        raise ValueError("Checkpoint requires a valid synchronized state")
    arrays, levels = {}, {}
    for label, lbm in (("coarse", solver._coarse), ("fine", solver._fine)):
        fields, boundaries = lbm.data_handling.cpu_arrays, boundary_arrays(lbm)
        levels[label] = {"fields": sorted(fields), "boundaries": sorted(boundaries)}
        for kind, names, source in (("field", sorted(fields), fields), ("boundary", sorted(boundaries), boundaries)):
            for i, name in enumerate(names):
                arrays[f"{label}_{kind}_{i}"] = source[name]
    arrays["last_reflux"] = solver.last_reflux_moments
    control_meta={}
    from .convergence import save_control
    save_control(solver,arrays,control_meta)
    metadata = {"format": "aerokinetic_refined_cpu_checkpoint", "schema_version": 1,
                "numerics_revision": 1, "numerical_identity": numerical_identity(), "versions": versions(),
                "project": solver.project.as_dict(), "refinement": solver.refinement.__dict__,
                "case_fingerprint": solver.case_fingerprint, "steps": solver.steps, "fine_steps": solver.fine_steps,
                "levels": levels, "coarse_boundary_calls": solver.coarse_boundary_calls,
                "fine_boundary_calls": solver.fine_boundary_calls,
                "cumulative_wall_mass_exchange": solver.cumulative_wall_mass_exchange,
                "cumulative_wall_impulse": solver.cumulative_wall_impulse.tolist(),
                "cumulative_external_in": solver.cumulative_external_in.tolist(),
                "last_loads": solver.last_loads, "last_budget": solver.last_budget,
                "initial_integrals": solver._initial_integrals, "wall_fallback_links": solver.walls.fallback_links,
                "context": context or {},
                "array_sha256": {name: _hash_array(array) for name, array in arrays.items()}}
    metadata.update(control_meta)
    arrays["metadata"] = np.asarray(json.dumps(metadata, ensure_ascii=False, allow_nan=False))
    with atomic_binary(Path(path)) as handle:
        np.savez(handle, **arrays)
    return Path(path)


def restore_checkpoint(solver_type, path, progress=None):
    with np.load(path, allow_pickle=False) as saved:
        meta = json.loads(str(saved["metadata"]))
        if (meta.get("format") != "aerokinetic_refined_cpu_checkpoint" or meta.get("schema_version") != 1
                or meta.get("numerics_revision") != 1):
            raise ValueError("Unsupported refined checkpoint format")
        if (meta.get("versions") != versions() or
                meta.get("numerical_identity") not in (numerical_identity(),RELEASE_030_IDENTITY,RELEASE_051_IDENTITY)):
            raise ValueError("Checkpoint numerical code or pinned dependency versions differ")
        if type(meta.get("steps")) is not int or meta["steps"] < 0 or meta.get("fine_steps") != 2 * meta["steps"]:
            raise ValueError("Invalid refined checkpoint clocks")
        if meta.get("coarse_boundary_calls") != meta["steps"] or meta.get("fine_boundary_calls") != meta["fine_steps"]:
            raise ValueError("Boundary counters do not match synchronized checkpoint clocks")
        if set(saved.files) != set(meta["array_sha256"]) | {"metadata"}:
            raise ValueError("Checkpoint array manifest differs")
        for name, expected in meta["array_sha256"].items():
            if _hash_array(saved[name]) != expected:
                raise ValueError(f"Checkpoint array checksum failed: {name}")
        project = Project.from_dict(meta["project"])
        refinement = RefinementSettings(**meta["refinement"])
        solver = solver_type(project, refinement, progress=progress)
        if solver.case_fingerprint != meta["case_fingerprint"]:
            raise ValueError("Refined checkpoint case fingerprint differs")
        for label, lbm in (("coarse", solver._coarse), ("fine", solver._fine)):
            current = {"field": lbm.data_handling.cpu_arrays, "boundary": boundary_arrays(lbm)}
            for kind, group in current.items():
                names = meta["levels"][label][kind + "s" if kind != "boundary" else "boundaries"]
                if sorted(group) != names:
                    raise ValueError(f"Checkpoint {label} {kind} names differ")
                for i, name in enumerate(names):
                    data, target = saved[f"{label}_{kind}_{i}"], group[name]
                    if data.shape != target.shape or data.dtype != target.dtype:
                        raise ValueError(f"Checkpoint shape/dtype mismatch: {label}/{name}")
                    if kind == "boundary":
                        for component in ("x", "y", "z", "dir"):
                            if not np.array_equal(data[component], target[component]):
                                raise ValueError(f"Checkpoint boundary geometry differs: {label}/{name}")
                    np.copyto(target, data)
        if saved["last_reflux"].shape != solver.last_reflux_moments.shape:
            raise ValueError("Checkpoint reflux shape differs")
        solver.last_reflux_moments[...] = saved["last_reflux"]
        solver.steps, solver.fine_steps = meta["steps"], meta["fine_steps"]
        solver.coarse_boundary_calls, solver.fine_boundary_calls = meta["coarse_boundary_calls"], meta["fine_boundary_calls"]
        solver.cumulative_wall_mass_exchange = float(meta["cumulative_wall_mass_exchange"])
        solver.cumulative_wall_impulse = np.array(meta["cumulative_wall_impulse"], float)
        solver.cumulative_external_in = np.array(meta["cumulative_external_in"], float)
        solver.last_loads, solver.last_budget = meta["last_loads"], meta["last_budget"]
        solver._initial_integrals = meta["initial_integrals"]
        solver.walls.fallback_links = meta["wall_fallback_links"]
        solver.last_donor_time_fractions = (0., 0.5) if solver.steps else ()
        solver._checkpoint_context = meta["context"]
        from .convergence import restore_control
        restore_control(solver,saved,meta)
        solver.diagnostics()
        return solver
