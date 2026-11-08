"""Preregistered long-time boundedness of tangential shear, including 3D wrapping."""
import csv
import json
from pathlib import Path

import numpy as np

from .preflight import lattice,initialize,macros
from aerokinetic.farfield import install_characteristic_farfield

WORK=Path(__file__).resolve().parent


def run_case(out,wavelength,three_d=False):
    shape=(64,24,32 if three_d else 8);periodic=(True,False,True)
    base=(.04,0,.01 if three_d else 0)
    s=lattice(shape,periodic,base);install_characteristic_farfield(s,shape,periodic,base)
    def initial(block):
        x,_,z=block.midpoint_arrays
        phase=2*np.pi*x/wavelength+(2*np.pi*z/16 if three_d else 0)
        block[s.velocity_data_name][...,1]=2e-5*np.cos(phase)
    initialize(s,base,initial);rows=[];energy0=None;finite=True
    for step in range(10001):
        if step%100==0:
            rho,v=macros(s)
            finite=bool(np.isfinite(rho).all()and np.isfinite(v).all())
            if not finite:break
            energy=float(np.mean(np.sum((v-base)**2,axis=-1)+(rho-1)**2/3))
            if energy0 is None:energy0=energy
            rows.append({'step':step,'energy_over_initial':energy/energy0,
                         'max_density_error':float(abs(rho-1).max())})
            if energy/energy0>100 or abs(rho-1).max()>.1:break
        if step<10000:s.time_step()
    with(out/f'w{wavelength}.csv').open('w',newline='')as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    e=max(r['energy_over_initial']for r in rows);d=max(r['max_density_error']for r in rows)
    return {'last_sampled_step':rows[-1]['step'],'finite':finite,'max_energy_over_initial':e,
        'max_density_error':d,'passed':finite and rows[-1]['step']==10000 and e<=1.1 and d<=2e-6}


def run_glancing():
    out=WORK/'glancing';out.mkdir(exist_ok=False)
    cases={str(w):run_case(out,w)for w in (16,8,4)}
    result={'cases':cases,'passed':all(r['passed']for r in cases.values()),
        'scope':'Boundedness only, not phase accuracy or a full viscous incoming-characteristic model'}
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print('GLANCING',result,flush=True)


def run_glancing_3d():
    out=WORK/'glancing_3d';out.mkdir(exist_ok=False)
    result=run_case(out,16,True)
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print('GLANCING3D',result,flush=True)
