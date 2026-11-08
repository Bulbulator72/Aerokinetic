"""Prior long-time glancing shear gate on the original physical core."""
import argparse,csv,hashlib,json,time
from pathlib import Path
import numpy as np
from exterior_fd import ExteriorFD

def main():
    p=argparse.ArgumentParser();p.add_argument('--wavelength',type=int,choices=(16,8,4),default=16);p.add_argument('--three-d',action='store_true');p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(exist_ok=False)
    shape=(64,24,32 if args.three_d else 8);base=(.04,0,.01 if args.three_d else 0)
    m=ExteriorFD(shape,(True,False,True),base)
    def pulse(block,coords):
        x,_,z=coords;phase=2*np.pi*x/args.wavelength+(2*np.pi*z/16 if args.three_d else 0)
        block[m.s.velocity_data_name][...,1]=2e-5*np.cos(phase)
    m.initialize(pulse);rows=[];e0=None;total0=None;started=time.perf_counter();finite=True
    for step in range(10001):
        if step%100==0:
            rho,u=m.macros(full=True);finite=bool(np.isfinite(rho).all()and np.isfinite(u).all())
            if not finite:break
            energy=np.sum((u-base)**2,axis=-1)+(rho-1)**2/3
            if e0 is None:e0=float(energy[m.core].mean());total0=float(energy.sum())
            r={'step':step,'core_energy_over_initial':float(energy[m.core].mean()/e0),'combined_energy_over_initial':float(energy.sum()/total0),
               'core_density_max_error':float(abs(rho[m.core]-1).max()),'all_density_max_error':float(abs(rho-1).max())}
            rows.append(r)
            if step%1000==0:print(r,flush=True)
            # An exceeded running maximum cannot be repaired by later samples.
            # Same acceptance thresholds; fail-fast preserves the failed prefix.
            if max(r['core_energy_over_initial'],r['combined_energy_over_initial'])>1.1 or r['core_density_max_error']>2e-6:break
        if step<10000:m.step()
    with(args.output/'history.csv').open('w',newline='')as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    result={'wavelength':args.wavelength,'three_d':args.three_d,'last_sampled_step':rows[-1]['step'],'finite':finite,
            'max_core_energy_over_initial':max(r['core_energy_over_initial']for r in rows),
            'max_combined_energy_over_initial':max(r['combined_energy_over_initial']for r in rows),
            'max_core_density_error':max(r['core_density_max_error']for r in rows),'elapsed_s':time.perf_counter()-started,
            'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest()}
    result['passed']=finite and rows[-1]['step']==10000 and max(result['max_core_energy_over_initial'],result['max_combined_energy_over_initial'])<=1.1 and result['max_core_density_error']<=2e-6
    (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
