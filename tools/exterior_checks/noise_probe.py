"""Identical physical-core noise with explicitly tracked exterior energy/source."""
import argparse,csv,hashlib,json,time
from pathlib import Path
import numpy as np
from exterior_fd import ExteriorFD


def main():
    p=argparse.ArgumentParser();p.add_argument('case',choices=('straight','rectangle'));p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(exist_ok=False);shape=(96,48,8);periodic=(False,True,True)if args.case=='straight'else(False,False,True)
    rng=np.random.default_rng(3107);dr=rng.normal(0,1e-8,shape);dr-=dr.mean();du=rng.normal(0,1e-8,shape+(3,));du-=du.mean(axis=(0,1,2))
    initial_sha=hashlib.sha256(dr.tobytes()+du.tobytes()).hexdigest()
    m=ExteriorFD(shape,periodic);m.initialize();s=m.s
    s.data_handling.cpu_arrays[s.density_data_name][m.core_pdf]=1+dr
    s.data_handling.cpu_arrays[s.velocity_data_name][m.core_pdf]=m.base+du
    s.set_pdf_fields_from_macroscopic_values()
    rows=[];audits=[];source_rows=[];energy0=None;finite=True;started=time.perf_counter();max_moment=0.
    for step in range(10001):
        if step%100==0:
            rho,u=m.macros(full=True);finite=bool(np.isfinite(rho).all()and np.isfinite(u).all())
            if not finite:break
            energy=np.sum((u-m.base)**2,axis=-1)+(rho-1)**2/3
            if energy0 is None:energy0=float(energy[m.core].mean())
            core=float(energy[m.core].mean()/energy0);total=float(energy.sum()/(np.prod(shape)*energy0))
            row={'step':step,'core_energy_over_initial':core,'combined_energy_over_initial':total,'max_density_error_all':float(np.max(abs(rho-1))),
                 'max_mach_all':float(np.sqrt(3)*np.linalg.norm(u,axis=-1).max())}
            rows.append(row)
            if step%1000==0:print(args.case,step,core,total,flush=True)
            if max(core,total)>10000 or row['max_density_error_all']>.05 or row['max_mach_all']>.3:break
        if step<10000:
            r=m.step(audit=step in (0,1,9,99,499,999,4999,9999));max_moment=max(max_moment,r['reconstruction_moment_error'])
            if 'core_budget_per_cell'in r:audits.append(r)
            if step%100==0:source_rows.append(r)
    for name,data in [('history',rows)]:
        with(args.output/(name+'.csv')).open('w',newline='')as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    max_core=max(r['core_energy_over_initial']for r in rows);max_total=max(r['combined_energy_over_initial']for r in rows)
    budget=max(abs(v)for r in audits for v in r['core_budget_per_cell']);overwrite=max(r['core_pdf_overwrite_max']for r in audits)
    result={'case':args.case,'physical_shape':shape,'augmented_shape':m.shape,'periodic':periodic,'initial_noise_sha256':initial_sha,
            'last_sampled_step':rows[-1]['step'],'finite':finite,'max_core_energy_over_initial':max_core,'max_combined_energy_over_initial':max_total,
            'max_reconstruction_moment_error':max_moment,'max_core_budget_per_cell':budget,'max_core_overwrite':overwrite,
            'elapsed_s':time.perf_counter()-started,'audits':audits,'source_samples':source_rows,
            'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest()}
    result['passed']=finite and rows[-1]['step']==10000 and max(max_core,max_total)<=1.1 and budget<=2e-12 and overwrite==0 and max_moment<=2e-12
    (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k not in ('audits','source_samples')},indent=2),flush=True)

if __name__=='__main__':main()
