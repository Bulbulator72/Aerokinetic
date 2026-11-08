"""Prior corner/seam vortex fixtures, sampled on the same physical coordinates."""
import argparse,csv,hashlib,json,time
from pathlib import Path
import numpy as np
from exterior_fd import ExteriorFD,NU

def main():
    p=argparse.ArgumentParser();p.add_argument('case',choices=('corner','seam'));p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(exist_ok=False)
    corner=args.case=='corner';shape=(160,160,8)if corner else(160,8,160)
    periodic=(False,False,True)if corner else(False,True,True);base=(.04,.04,0)if corner else(.04,0,0)
    m=ExteriorFD(shape,periodic,base);A=2e-4;started=time.perf_counter()
    def pulse(block,coords):
        x,y,z=coords;rx=x-110;ry=y-110 if corner else(z-3+80)%160-80
        env=np.exp(-.5*(rx*rx+ry*ry)/100)
        block[m.s.velocity_data_name][...,0]=base[0]-A*ry/10*env
        block[m.s.velocity_data_name][...,1 if corner else 2]=(base[1]if corner else 0)+A*rx/10*env
        block[m.s.density_data_name][...]=np.exp(-1.5*A*A*env**2)
    m.initialize(pulse);rows=[];audits=[];norm=None;peak=None
    x=(np.arange(160)+.5)[:,None,None]
    y=(np.arange(160)+.5)[None,:,None]if corner else(np.arange(160)+.5)[None,None,:]
    for step in range(3001):
        if step%100==0:
            rho,u=m.macros();assert np.isfinite(rho).all()and np.isfinite(u).all()
            rx=x-110-.04*step;ry=y-110-.04*step if corner else(y-3+80)%160-80;b2=100+2*NU*step
            amp=A*1000/(b2*b2)*np.exp(-.5*(rx*rx+ry*ry)/b2)
            terms=(-ry*amp,rx*amp,np.zeros_like(amp))if corner else(-ry*amp,np.zeros_like(amp),rx*amp)
            exact=np.broadcast_to(np.stack(np.broadcast_arrays(*terms),axis=-1),u.shape);rot=u-base
            if norm is None:norm=float(np.linalg.norm(rot));peak=float(np.max(abs(rot)))
            rows.append({'step':step,'density_max_error':float(np.max(abs(rho-1))),
                         'velocity_l2_error_over_initial':float(np.linalg.norm(rot-exact)/norm),
                         'velocity_max_error_over_initial_peak':float(np.max(abs(rot-exact))/peak)})
            if step%500==0:print(args.case,rows[-1],flush=True)
        if step<3000:
            r=m.step(audit=step in (0,1,10,1000,1200,1500,2999))
            if 'core_budget_per_cell'in r:audits.append(r)
    with(args.output/'history.csv').open('w',newline='')as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    density=max(r['density_max_error']for r in rows);transit=max(r['velocity_l2_error_over_initial']for r in rows)
    budget=max(abs(v)for r in audits for v in r['core_budget_per_cell']);edits=max(r['core_pdf_overwrite_max']for r in audits)
    step500=next(r for r in rows if r['step']==500)
    result={'case':args.case,'physical_shape':shape,'augmented_shape':m.shape,'max_density_error':density,
            'max_transit_velocity_l2_error_over_initial':transit,'step500':step500,'last':rows[-1],
            'max_core_budget_per_cell':budget,'max_core_overwrite':edits,'elapsed_s':time.perf_counter()-started,'audits':audits,
            'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest()}
    result['passed']=density<=2e-6 and transit<=.1 and rows[-1]['velocity_l2_error_over_initial']<=.05 and budget<=2e-12 and edits==0 and(not corner or step500['velocity_max_error_over_initial_peak']<=.05)
    (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k!='audits'},indent=2),flush=True)

if __name__=='__main__':main()
