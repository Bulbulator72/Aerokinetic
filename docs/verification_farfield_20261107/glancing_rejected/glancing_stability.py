from pathlib import Path
import argparse,csv,json,hashlib
import numpy as np
from preflight import lattice,initialize,macros
from aerokinetic.farfield import install_characteristic_farfield
from ab2_boundary import install_characteristic_farfield as install_ab2
root=Path(__file__).resolve().parent
out=root/'glancing_stability';out.mkdir(exist_ok=False)
result={}
for candidate,install in [('trapezoid',install_characteristic_farfield),('ab2',install_ab2)]:
 result[candidate]={}
 for wavelength in (16,8,4):
  shape=(64,24,8);periodic=(True,False,True);s=lattice(shape,periodic);install(s,shape,periodic,(.04,0,0))
  def initial(block):
   x,_,_=block.midpoint_arrays;block[s.velocity_data_name][...,1]=2e-5*np.cos(2*np.pi*x/wavelength)
  initialize(s,pulse=initial);history=[];energy0=None
  for step in range(10001):
   if step%100==0:
    rho,v=macros(s);finite=np.isfinite(rho).all()and np.isfinite(v).all()
    if not finite:
     history.append({'step':step,'energy_over_initial':float('inf'),'max_density_error':float('inf')});break
    energy=float(np.mean(np.sum((v-(.04,0,0))**2,axis=-1)+(rho-1)**2/3))
    if energy0 is None:energy0=energy
    history.append({'step':step,'energy_over_initial':energy/energy0,'max_density_error':float(abs(rho-1).max())})
    if energy/energy0>100 or abs(rho-1).max()>.1:break
   if step<10000:s.time_step()
  with(out/f'{candidate}_w{wavelength}.csv').open('w',newline='')as f:
   w=csv.DictWriter(f,fieldnames=list(history[0]));w.writeheader();w.writerows(history)
  e=max(r['energy_over_initial']for r in history);d=max(r['max_density_error']for r in history)
  result[candidate][str(wavelength)]={'last_step':history[-1]['step'],'max_energy_over_initial':e,'max_density_error':d,'passed':history[-1]['step']==10000 and e<=1.1 and d<=2e-6}
  print(candidate,wavelength,result[candidate][str(wavelength)],flush=True)
  (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n')
