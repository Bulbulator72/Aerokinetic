"""Linearized convecting/diffusing vortex, not an exact nonlinear solution."""
from pathlib import Path
import csv,json,math
import numpy as np
from .preflight import lattice,install,initialize,macros,WORK


def main():
    out=WORK/'vortex';out.mkdir(exist_ok=False)
    shape=(160,160,8);periodic=(False,False,True);base=(.04,.04,0)
    s=lattice(shape,periodic,base);install(s,shape,periodic,base)
    A=2e-4;sigma=10.;nu=.04*32/30000
    def pulse(block):
        x,y,_=block.midpoint_arrays;rx,ry=x-110,y-110
        envelope=np.exp(-.5*(rx*rx+ry*ry)/100)
        block[s.velocity_data_name][...,0]=base[0]-A*ry/sigma*envelope
        block[s.velocity_data_name][...,1]=base[1]+A*rx/sigma*envelope
        block[s.density_data_name][...]=np.exp(-1.5*A*A*envelope**2)
    initialize(s,base,pulse);records=[]
    x=(np.arange(160)+.5)[:,None,None];y=(np.arange(160)+.5)[None,:,None]
    initial_norm=None;initial_peak=None
    for step in range(3001):
        if step%100==0:
            rho,v=macros(s);assert np.isfinite(rho).all()and np.isfinite(v).all()
            rx=x-110-.04*step;ry=y-110-.04*step;b2=100+2*nu*step
            amplitude=A*1000/(b2*b2)*np.exp(-.5*(rx*rx+ry*ry)/b2)
            exact=np.broadcast_to(np.stack(np.broadcast_arrays(-ry*amplitude,rx*amplitude,np.zeros_like(amplitude)),axis=-1),v.shape)
            rot=v-base
            if step==0:initial_norm=float(np.linalg.norm(rot));initial_peak=float(np.max(abs(rot)))
            records.append({'step':step,'density_max_error':float(np.max(abs(rho-1))),
                'velocity_l2_error_over_initial_rotational_l2':float(np.linalg.norm(rot-exact)/initial_norm),
                'velocity_max_error_over_initial_rotational_peak':float(np.max(abs(rot-exact))/initial_peak)})
            if step in (0,500,1500,3000):np.savez_compressed(out/f'field_{step:04d}.npz',rho=rho[:,:,4],velocity=v[:,:,4])
        if step<3000:s.time_step()
    with(out/'history.csv').open('w',newline='')as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    half=next(r for r in records if r['step']==500);last=records[-1]
    result={'max_density_error':max(r['density_max_error']for r in records),'step500':half,'step3000':last,
        'max_transit_velocity_l2_error_over_initial':max(r['velocity_l2_error_over_initial_rotational_l2']for r in records),
        'passed':max(r['velocity_l2_error_over_initial_rotational_l2']for r in records)<=.1 and max(r['density_max_error']for r in records)<=2e-6 and half['velocity_max_error_over_initial_rotational_peak']<=.05 and last['velocity_l2_error_over_initial_rotational_l2']<=.05}
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
