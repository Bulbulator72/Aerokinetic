"""Replay a failed uniform CPU run into a NEW diagnostic directory.

The parent and its valid checkpoint stay untouched. Numerical guards remain
enabled. An inadmissible state is captured for diagnosis, never for averaging.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def hashes(directory):
    result={}
    for path in directory.rglob('*'):
        if path.is_file():
            with path.open('rb')as handle:result[path.relative_to(directory).as_posix()]=hashlib.file_digest(handle,'sha256').hexdigest()
    return result


def capture(directory,output,save_pdfs=False):
    directory=Path(directory).resolve();output=Path(output).resolve()
    if output.is_relative_to(directory):raise ValueError('Diagnostic output must be outside the parent run')
    if output.exists():raise FileExistsError(output)
    metadata=json.loads((directory/'metadata.json').read_text(encoding='utf-8-sig'))
    summary=json.loads((directory/'summary.json').read_text(encoding='utf-8-sig'))
    if summary.get('status')!='failed' or not metadata.get('failure','').startswith('NumericalFailure:'):
        raise ValueError('Choose a failed run with a recorded NumericalFailure')
    checkpoint=directory/'checkpoint.npz'
    with np.load(checkpoint,allow_pickle=False)as saved:cp=json.loads(str(saved['metadata']))
    if cp.get('format')!='aerokinetic_cpu_checkpoint':
        raise ValueError('This diagnostic currently supports uniform CPU checkpoints only')
    if cp['case_fingerprint']!=metadata['case_fingerprint']:
        raise ValueError('Checkpoint and failed run case differ')
    stop=summary['steps'];start=cp['steps']
    if type(stop)is not int or not start<stop:raise ValueError('A valid earlier checkpoint is required')
    before=hashes(directory)
    import aerokinetic
    from aerokinetic import Solver
    from aerokinetic.solver import NumericalFailure
    solver=Solver.from_checkpoint(checkpoint)
    output.mkdir(parents=True,exist_ok=False)
    settings=metadata['run'];error=None
    print(f'Replaying saved step {start} to reported failure step {stop}; original guards enabled',flush=True)
    try:
        while solver.steps<stop:
            sample=(solver.steps+1)%settings['sample_every']==0
            row=solver.step(sample=sample)
            if sample or solver.steps%settings['check_every']==0 or solver.steps==metadata['end_step']:
                solver.diagnostics()
            if row is not None and not all(math.isfinite(row[k])for k in ('cl','cd','cm')):
                raise NumericalFailure('Non-finite loads; the run is invalid')
    except NumericalFailure as exc:
        error=str(exc)
    stepper=solver._stepper;pdf=stepper.data_handling.cpu_arrays[stepper.pdf_array_name]
    g=pdf[1:-1,1:-1,1:-1];rho=1+g.sum(axis=-1)
    with np.errstate(all='ignore'):
        velocity=np.einsum('...q,qa->...a',g,solver._stencil,optimize=True)/rho[...,None]
        mach=np.sqrt(3)*np.linalg.norm(velocity,axis=-1)
    finite=np.isfinite(rho)&np.isfinite(velocity).all(axis=-1)
    index=np.unravel_index(np.argmax(np.where(solver._fluid,np.where(finite,mach,np.inf),-np.inf)),rho.shape)
    z=index[2];x=solver.plan.origin[0]+(np.arange(rho.shape[0])+.5)*solver.plan.dx
    y=solver.plan.origin[1]+(np.arange(rho.shape[1])+.5)*solver.plan.dx
    np.savez_compressed(output/'inadmissible_plane.npz',rho=rho[:,:,z],velocity=velocity[:,:,z],
                        solid=solver._solid_xy,x_over_c=x,y_over_c=y,z_index=z)
    if save_pdfs:
        np.savez(output/'inadmissible_pdfs_NOT_RESTART.npz',
            pdf_source=pdf,pdf_temporary=stepper.data_handling.cpu_arrays[stepper._tmp_arr_name],fluid=solver._fluid)
    unchanged=hashes(directory)==before
    reproduced=error is not None and solver.steps==stop and metadata['failure']=='NumericalFailure: '+error
    result={'status':'reproduced'if reproduced else'not_reproduced_at_reported_step',
        'runtime_version':aerokinetic.__version__,'imported':aerokinetic.__file__,
        'tool_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'solver_sha256':hashlib.sha256(Path(aerokinetic.__file__).with_name('solver.py').read_bytes()).hexdigest(),
        'case_fingerprint':metadata['case_fingerprint'],'checkpoint_step':start,
        'original_failure_step':stop,'replayed_step':solver.steps,'replayed_t_star':solver.steps*solver.plan.dt_convective,
        'exception':error,'original_exception':metadata['failure'],
        'parent_files_unchanged':unchanged,'parent_sha256':before,
        'all_fluid_finite':bool(finite[solver._fluid].all()),
        'local_mach_max':float(mach[solver._fluid].max())if finite[solver._fluid].all()else None,
        'density_error_max':float(abs(rho[solver._fluid]-1).max())if np.isfinite(rho[solver._fluid]).all()else None,
        'max_mach_index':list(map(int,index)),
        'max_mach_xy_over_c':[float(x[index[0]]),float(y[index[1]])],
        'plane_z_index':int(z),'pdf_archive_is_restart':False,
        'aerodynamic_averages':'withheld: captured state is inadmissible',
        'captured_files_sha256':hashes(output)}
    (output/'CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k not in ('parent_sha256','captured_files_sha256')},indent=2),flush=True)
    if not unchanged:raise AssertionError('Parent files changed during replay')
    if not reproduced:raise RuntimeError('Original numerical failure was not reproduced at its reported step')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--save-pdfs',action='store_true',help='Save large diagnostic PDF arrays; never a restart checkpoint')
    args=parser.parse_args();capture(args.directory,args.output,args.save_pdfs)


if __name__=='__main__':main()
