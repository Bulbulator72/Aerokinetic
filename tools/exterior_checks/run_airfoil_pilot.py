"""Fixed t*=12 body completion/domain pilot; no experimental accuracy claim."""
import argparse
import csv
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import time
import traceback

import numpy as np
from aerokinetic import Project
from airfoil_pilot import AirfoilPilot


def write(path,data):Path(path).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);started=time.perf_counter()
    original=Project.load(a.project)
    project=replace(original,grid=replace(original.grid,farfield='legacy',openmp_threads=1))
    project.save(a.output/'input_project.json')
    s=AirfoilPilot(project);identity=s.identity()
    write(a.output/'SETUP.json',{'input_fixture':str(a.project),'fixture_sha256':hashlib.sha256(a.project.read_bytes()).hexdigest(),
        'fixture_fingerprint':original.case_fingerprint,'adapted_input_fingerprint':project.case_fingerprint,
        'overrides':'CPU1 thread and separate research_exterior_fd_v1 driver; no original characteristic boundary installed',
        'identity':identity,'platform':platform.platform(),'fluid_physical_cells':s.fluid_count,
        'coefficient_convention':'Cl up; Cd downstream; Cm nose up; total reference chord and periodic span',
        'force_clock':'(step-.5)*dt_convective, after collision and wall reflection before streaming',
        'scope':'Completion/domain research gate at N32. Not grid convergence or wind-tunnel validation.'})
    rows=[];diagnostics=[];audits=[];status='running';failure=None
    fields=('step','t_star','time_s','cl','cd','cm')
    try:
        with(a.output/'history.csv').open('w',newline='')as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for step in range(1,9601):
                load,report=s.step(sample=step%8==0,audit=step in(1,2,128)or step%800==0)
                if load is not None and step%8==0:
                    row={k:load.get(k,'')for k in fields};w.writerow(row);f.flush();rows.append(row)
                if step in(1,2,128)or step%800==0:
                    audits.append(report);write(a.output/'audits.json',audits)
                    if(max(abs(v)for v in report['fluid_budget_per_cell'])>2e-12
                        or max(abs(v)for v in report['physical_fluid_pdf_overwrite_budget'])!=0
                        or max(abs(v)for v in report['wall_force_balance_error'])>2e-10
                        or abs(report['wall_torque_balance_error'])>2e-9):
                        raise RuntimeError('Preregistered fluid/force/torque budget gate failed')
                if step%128==0 or step==9600:
                    d=s.diagnostics();d.update(step=step,t_star=step*s.core_plan.dt_convective)
                    diagnostics.append(d);write(a.output/'diagnostics.json',diagnostics)
                if step%2400==0:
                    s.save_research_checkpoint(a.output/'checkpoint_local.npz')
                    rho,u=s.exterior.macros();k=rho.shape[2]//2
                    np.savez_compressed(a.output/f'frame_local_{step:08d}.npz',rho=rho[:,:,k],velocity=u[:,:,k],
                        solid=s._solid_xy[s.exterior.core[:2]],origin=s.core_plan.origin,dx=s.core_plan.dx,
                        t_star=step*s.core_plan.dt_convective)
                if step%800==0:
                    progress={'step':step,'t_star':step*s.core_plan.dt_convective,
                        'cl':None if load is None else load['cl'],'cd':None if load is None else load['cd'],
                        'elapsed_s':time.perf_counter()-started,'last_diagnostics':diagnostics[-1]if diagnostics else None}
                    write(a.output/'PROGRESS.json',progress);print(json.dumps(progress),flush=True)
                if time.perf_counter()-started>3600:raise RuntimeError('Original3600s wall-time limit reached; fixed completion gate not passed')
        status='completed'
    except Exception as e:
        status='failed';failure={'type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc()}
        write(a.output/'FAILURE.json',failure)
        # Retain the actual first failed state locally; it is not a valid restart.
        if s.exterior.initial is not None:s.save_research_checkpoint(a.output/'failure_state_local.npz')
    result={'status':status,'steps':s.steps,'t_star':s.steps*s.core_plan.dt_convective,
        'requested_steps':9600,'completion_gate_passed':status=='completed'and s.steps==9600,
        'samples':len(rows),'elapsed_s':time.perf_counter()-started,
        'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'failure':failure,
        'identity':identity,'experimental_validation':'not_run','grid_convergence':'not_run'}
    if result['completion_gate_passed']:
        values=np.array([[r[k]for k in('cl','cd','cm')]for r in rows if 6<=r['t_star']<=12])
        result['window6_12']={'samples':len(values),'mean':dict(zip(('cl','cd','cm'),values.mean(axis=0).tolist())),
            'rms_about_mean':dict(zip(('cl','cd','cm'),values.std(axis=0).tolist())),
            'limits':'Prespecified common window; no assertion of stationary statistics or resolved N32 aerodynamics.'}
    else:result['window6_12']={'status':'not_evaluated','reason':'Incomplete fixed window; no partial-window averages'}
    write(a.output/'CHECKS.json',result);print(json.dumps(result,indent=2),flush=True)
    if not result['completion_gate_passed']:raise SystemExit(1)


if __name__=='__main__':main()
