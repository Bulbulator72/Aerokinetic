"""Independent storage-phase equivalence and exterior/core ownership checks."""
import hashlib,json,time
from pathlib import Path
import numpy as np
from exterior_fd import ExteriorFD,lattice


def initialize(stepper):
    for block in stepper.data_handling.iterate(ghost_layers=True):
        x,y,z=block.midpoint_arrays
        block[stepper.density_data_name][...]=1+1e-5*np.cos(2*np.pi*x/16)*np.sin(2*np.pi*y/16)
        block[stepper.velocity_data_name][...]=(.04,0,0)
        block[stepper.velocity_data_name][...,2]=2e-5*np.cos(2*np.pi*y/16)
    stepper.set_pdf_fields_from_macroscopic_values();stepper.pre_run()

def macros(s):
    s.post_run();return(s.data_handling.gather_array(s.density_data_name),s.data_handling.gather_array(s.velocity_data_name))

def main():
    root=Path(__file__).resolve().parent;out=root/'preflight';out.mkdir(exist_ok=False)
    t=time.perf_counter();a=lattice((16,16,8),(True,True,True),'stream_collide');b=lattice((16,16,8),(True,True,True),'collide_stream')
    initialize(a);initialize(b);errors=[]
    for step in range(1,101):
        a.time_step();b.time_step()
        if step in (1,10,100):
            ar,av=macros(a);br,bv=macros(b);errors.append({'step':step,'rho_max_error':float(np.max(abs(ar-br))),'u_max_error':float(np.max(abs(av-bv)))})
    print('PHASE',errors,flush=True)
    uniforms=[]
    for velocity in ((.04,0,0),(.04,.01,.005)):
        m=ExteriorFD((16,20,8),(False,False,True),velocity);m.initialize();audits=[]
        for step in range(100):
            r=m.step(audit=step in (0,1,9,99))
            if step in (0,1,9,99):audits.append(r)
        rho,u=m.macros(full=True)
        uniforms.append({'velocity':velocity,'rho_max_error':float(np.max(abs(rho-1))),'u_max_error':float(np.max(abs(u-velocity))),
                         'moment_error_max':max(r['reconstruction_moment_error']for r in audits),'budget_max':max(abs(v)for r in audits for v in r['core_budget_per_cell']),
                         'overwrite_max':max(r['core_pdf_overwrite_max']for r in audits),'audits':audits})
        print('UNIFORM',velocity,uniforms[-1]['rho_max_error'],uniforms[-1]['u_max_error'],flush=True)
    result={'phase_errors':errors,'uniforms':uniforms,'elapsed_s':time.perf_counter()-t,
            'source_sha256':hashlib.sha256((root/'exterior_fd.py').read_bytes()).hexdigest()}
    result['passed']=all(max(r['rho_max_error'],r['u_max_error'])<=2e-12 for r in errors)and all(max(r['rho_max_error'],r['u_max_error'])<=5e-13 and r['budget_max']<=2e-12 and r['moment_error_max']<=2e-12 and r['overwrite_max']==0 for r in uniforms)
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print('PASSED',result['passed'],flush=True)

if __name__=='__main__':main()
