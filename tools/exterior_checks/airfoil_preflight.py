"""Fixed acceptance checks for body geometry, force phase and exact restart."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

import numpy as np
from aerokinetic import Project,Solver
from airfoil_pilot import AirfoilPilot,wall_links


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);t=time.perf_counter()
    project=Project.load(a.project)
    project=replace(project,grid=replace(project.grid,farfield='legacy',openmp_threads=1))
    old=Solver(project.section,project.flow,project.grid)
    pilot=AirfoilPilot(project);m=pilot.exterior
    s=old._stepper;new=pilot._stepper
    initial_error=float(np.max(abs(s.data_handling.cpu_arrays[s.pdf_array_name][1:-1,1:-1,1:-1]
                                 -new.data_handling.cpu_arrays[new.pdf_array_name][m.core_pdf])))
    mask_error=bool(np.any(old._solid_xy!=pilot._solid_xy[m.core[:2]]))
    old_links=wall_links(old,(0,0,0));new_links=wall_links(pilot,m.pad)
    assert len(old_links)==len(new_links)
    same_keys=all(x[:5]==y[:5]for x,y in zip(old_links,new_links))
    q_error=max(abs(x[5]-y[5])for x,y in zip(old_links,new_links))
    rows=[];audits=[]
    for step in range(1,17):
        x=old.step(sample=True);y,report=pilot.step(sample=True,audit=step in(1,2,8,16))
        rows.append({'step':step,**{k+'_error':float(abs(x[k]-y[k]))for k in('cl','cd','cm')},
                     'legacy':{k:x[k]for k in('cl','cd','cm')},'pilot':{k:y[k]for k in('cl','cd','cm')}})
        if step in(1,2,8,16):audits.append(report)
    pilot.save_research_checkpoint(a.output/'restart_local.npz')
    continued=[]
    for step in range(7):
        y,_=pilot.step(sample=True);continued.append({k:y[k]for k in('cl','cd','cm')})
    states={n:new.data_handling.cpu_arrays[n].copy()for n in(new.pdf_array_name,new._tmp_arr_name)}
    init=pilot.exterior.initial.copy();steps=pilot.steps
    pilot.restore_research_checkpoint(a.output/'restart_local.npz')
    restarted=[]
    for step in range(7):
        y,_=pilot.step(sample=True);restarted.append({k:y[k]for k in('cl','cd','cm')})
    resume_pdf_error=max(float(np.max(abs(v-new.data_handling.cpu_arrays[n])))for n,v in states.items())
    resume_initial_error=float(np.max(abs(init-pilot.exterior.initial)))
    resume_load_error=max(abs(x[k]-y[k])for x,y in zip(continued,restarted)for k in x)
    errors={
      'physical_core_initial_pdf_max_error':initial_error,'physical_solid_masks_equal':not mask_error,
      'wall_link_keys_equal':same_keys,'wall_link_count':len(old_links),'wall_q_max_error':q_error,
      'max_cl_cd_error_first16':max(r[k+'_error']for r in rows for k in('cl','cd')),
      'max_cm_error_first16':max(r['cm_error']for r in rows),
      'max_fluid_budget_per_cell':max(abs(v)for r in audits for v in r['fluid_budget_per_cell']),
      'max_fluid_overwrite_budget_per_cell':max(abs(v)for r in audits for v in r['physical_fluid_pdf_overwrite_budget']),
      'max_wall_force_balance_error':max(abs(v)for r in audits for v in r['wall_force_balance_error']),
      'max_wall_torque_balance_error':max(abs(r['wall_torque_balance_error'])for r in audits),
      'resume_pdf_max_error':resume_pdf_error,'resume_initial_max_error':resume_initial_error,
      'resume_load_max_error':resume_load_error,'resume_step_equal':steps==pilot.steps}
    errors['passed']=bool(initial_error<=2e-15 and not mask_error and same_keys and q_error<=2e-12
        and errors['max_cl_cd_error_first16']<=2e-9 and errors['max_cm_error_first16']<=2e-9
        and errors['max_fluid_budget_per_cell']<=2e-12 and errors['max_fluid_overwrite_budget_per_cell']==0
        and errors['max_wall_force_balance_error']<=2e-10 and errors['max_wall_torque_balance_error']<=2e-9
        and resume_pdf_error==0 and resume_initial_error==0 and resume_load_error==0 and steps==pilot.steps)
    errors.update(identity=pilot.identity(),elapsed_s=time.perf_counter()-t,
        limits='Same-code causal force/wall preflight, not experimental validation')
    (a.output/'CHECKS.json').write_text(json.dumps(errors,indent=2)+'\n')
    (a.output/'loads_first16.json').write_text(json.dumps(rows,indent=2)+'\n')
    (a.output/'audits.json').write_text(json.dumps(audits,indent=2)+'\n')
    print(json.dumps(errors,indent=2),flush=True)


if __name__=='__main__':main()
