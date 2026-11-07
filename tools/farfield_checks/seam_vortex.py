import argparse,csv,json
from pathlib import Path
import numpy as np
from .preflight import lattice,initialize,macros
from aerokinetic.farfield import CharacteristicFarfield
from .verification import audited_step


def main():
    from .preflight import WORK
    out=WORK/'seam_vortex';out.mkdir(exist_ok=False)
    shape=(160,8,160);periodic=(False,True,True);base=(.04,0,0)
    s=lattice(shape,periodic,base)
    for normal in ((-1,0,0),(1,0,0)):
        sign=normal[0]
        s.boundary_handling.set_boundary(CharacteristicFarfield(normal,shape,periodic,base,stepper=s),
            mask_callback=lambda x,y,z,sign=sign:(x<0)if sign<0 else(x>160))
    A=2e-4;nu=.04*32/30000
    def pulse(block):
        x,_,z=block.midpoint_arrays;rx=x-110;rz=(z-3+80)%160-80
        envelope=np.exp(-.5*(rx*rx+rz*rz)/100)
        block[s.velocity_data_name][...,0]=base[0]-A*rz/10*envelope
        block[s.velocity_data_name][...,2]=A*rx/10*envelope
        block[s.density_data_name][...]=np.exp(-1.5*A*A*envelope**2)
    initialize(s,base,pulse);records=[];audits=[]
    x=(np.arange(160)+.5)[:,None,None];z=(np.arange(160)+.5)[None,None,:]
    norm=None
    for step in range(3001):
        if step%100==0:
            rho,v=macros(s);assert np.isfinite(rho).all()and np.isfinite(v).all()
            rx=x-110-.04*step;rz=(z-3+80)%160-80;b2=100+2*nu*step
            amplitude=A*1000/(b2*b2)*np.exp(-.5*(rx*rx+rz*rz)/b2)
            exact=np.broadcast_to(np.stack(np.broadcast_arrays(-rz*amplitude,np.zeros_like(amplitude),rx*amplitude),axis=-1),v.shape)
            rot=v-base
            if step==0:norm=float(np.linalg.norm(rot))
            records.append({'step':step,'max_density_error':float(np.max(abs(rho-1))),
                'velocity_l2_error_over_initial_rotational_l2':float(np.linalg.norm(rot-exact)/norm)})
            if step in (0,500,1500,3000):np.savez_compressed(out/f'field_{step:04d}.npz',rho=rho[:,4,:],velocity=v[:,4,:])
        if step<3000:
            if step in (0,1,10,1000,1200,1500,2999):
                a=audited_step(s,shape,periodic);a['step']=step+1;audits.append(a)
            else:s.time_step()
    with(out/'history.csv').open('w',newline='')as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    density=max(r['max_density_error']for r in records);transit=max(r['velocity_l2_error_over_initial_rotational_l2']for r in records)
    budget=max(abs(x)for r in audits for x in r['residual_per_cell'])
    result={'max_density_error':density,'max_transit_velocity_l2_error_over_initial':transit,'last':records[-1],
        'max_budget_per_cell':budget,'audits':audits,'passed':density<=2e-6 and transit<=.1 and records[-1]['velocity_l2_error_over_initial_rotational_l2']<=.05 and budget<=2e-12}
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k!='audits'},indent=2),flush=True)


if __name__=='__main__':main()
