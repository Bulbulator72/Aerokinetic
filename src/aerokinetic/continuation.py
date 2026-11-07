"""Continue a settled CPU result into a new, explicitly linked time segment.

The parent result is never edited. The new CSV contains new samples only;
averaging uses global tU/c, and lineage retains parent/checkpoint digests.
This differs from resuming an interrupted run towards its existing end time.
"""
from dataclasses import asdict,replace
import json
import math
from pathlib import Path

from ._io import file_hash,write_json
from .project import Project,RunSettings
from .convergence import validate_history
from .timing import duration_steps


def continue_run(source, output, additional_time, *, average_after=None,
                 progress=None,callback=None,stop_event=None,residual_callback=None):
    """Continue a completed/stopped result without altering the parent directory.

    Retains physics, mesh, sampling cadence and termination settings. Returns
    the child summary. Histories and means refer to the new segment only.
    The child can subsequently be resumed using the normal resume API.
    """
    if not math.isfinite(additional_time) or additional_time<=0:
        raise ValueError('additional_time must be finite and positive')
    source,output=Path(source).resolve(),Path(output).resolve()
    if output==source or output.is_relative_to(source):
        raise ValueError('Continuation output must be outside the parent result directory')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Continuation requires a new or empty output directory')
    required=('metadata.json','summary.json','project.json','history.csv','residuals.csv','checkpoint.npz')
    if any(not(source/name).is_file()for name in required):
        raise ValueError('Parent result needs metadata, summary, project, histories and a CPU checkpoint')
    metadata=json.loads((source/'metadata.json').read_text(encoding='utf-8'))
    summary=json.loads((source/'summary.json').read_text(encoding='utf-8'))
    if metadata.get('status')not in ('completed','stopped')or summary.get('status')!=metadata['status']:
        raise ValueError('Parent must be completed or stopped at a saved checkpoint')
    if metadata.get('grid',{}).get('target','cpu')!='cpu':
        raise ValueError('Continuation is currently verified for CPU checkpoints only')
    parent_hashes={name:file_hash(source/name)for name in required}
    refined=metadata.get('format')=='aerokinetic_refined_run'
    if refined:
        from .refined_solver import RefinedSolver,load_refined_project
        original,refinement=load_refined_project(source/'project.json')
        solver=RefinedSolver.from_checkpoint(source/'checkpoint.npz',progress=progress)
        if original.case_fingerprint!=solver.project.case_fingerprint or refinement!=solver.refinement:
            raise ValueError('Parent project differs from its checkpoint')
        fingerprint=solver.case_fingerprint
        dt=solver.coarse_level.dt
    else:
        from .solver import Solver
        original=Project.load(source/'project.json')
        solver=Solver.from_checkpoint(source/'checkpoint.npz',progress=progress)
        fingerprint=Project(solver.section,solver.flow,solver.grid).case_fingerprint
        if original.case_fingerprint!=fingerprint:
            raise ValueError('Parent project differs from its checkpoint')
        dt=solver.plan.dt_convective
    context=solver._checkpoint_context
    if fingerprint!=metadata.get('case_fingerprint')or fingerprint!=context.get('case_fingerprint'):
        raise ValueError('Parent case fingerprints differ')
    if asdict(original.run)!=metadata['run']:
        raise ValueError('Parent run settings differ from its project')
    if solver.steps!=summary.get('steps')or not math.isclose(summary.get('t_star',-1),solver.steps*dt,abs_tol=1e-12):
        raise ValueError('Parent summary and checkpoint clocks differ')
    if (source/'history.csv').stat().st_size!=context.get('history_bytes')or parent_hashes['history.csv']!=context.get('history_sha256'):
        raise ValueError('Parent history differs from the checkpoint prefix')
    validate_history(source,context)
    if (source/'residuals.csv').stat().st_size!=context.get('residuals_bytes'):
        raise ValueError('Parent residual history has an uncommitted tail')
    start_step=solver.steps;start_time=start_step*dt
    cutoff=max(start_time,original.run.average_after)if average_after is None else average_after
    settings=replace(original.run,duration_convective=additional_time,average_after=cutoff)
    lineage={'format':'aerokinetic_continuation','schema_version':1,
        'parent_directory':str(source),'parent_version':metadata.get('version'),
        'parent_file_sha256':parent_hashes,'case_fingerprint':fingerprint,
        'start_step':start_step,'start_t_star':start_time,'requested_additional_t_star':additional_time,
        'target_step':start_step+duration_steps(additional_time,dt),
        'history_scope':'new_segment_only','averaging_scope':'new_segment_only; global tU/c cutoff',
        'average_after_t_star':settings.average_after}
    try:
        if refined:
            solver.project=replace(solver.project,run=settings)
            result=solver.run(output,callback=callback,stop_event=stop_event,residual_callback=residual_callback)
        else:
            result=solver.run(output=output,callback=callback,stop_event=stop_event,
                              residual_callback=residual_callback,**asdict(settings))
        return result
    finally:
        unchanged=all(file_hash(source/name)==digest for name,digest in parent_hashes.items())
        if (output/'metadata.json').is_file():
            lineage['parent_files_unchanged']=unchanged
            write_json(output/'continuation.json',lineage)
        if not unchanged:
            raise RuntimeError('Parent files changed during continuation; inspect provenance')
