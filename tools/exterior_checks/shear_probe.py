"""Previous exact convecting/diffusing Gaussian transverse velocity fixture."""
import argparse,csv,hashlib,json
from pathlib import Path
import numpy as np
from exterior_fd import ExteriorFD,NU

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(exist_ok=False)
    m=ExteriorFD((256,8,8),(False,True,True))
    def pulse(block,coords):
        x,_,_=coords;block[m.s.velocity_data_name][...,1]=2e-5*np.exp(-.5*((x-180)/12)**2)
    m.initialize(pulse);x=np.arange(256)+.5;rows=[]
    for i in range(3001):
        if i%100==0:
            rho,u=m.macros();width=np.sqrt(144+2*NU*i);exact=2e-5*12/width*np.exp(-.5*((x-180-.04*i)/width)**2)
            rows.append({'step':i,'max_density_error':float(abs(rho-1).max()),'max_uy_error_vs_gaussian':float(abs(u[...,1]-exact[:,None,None]).max()),'max_interior_abs_uy':float(abs(u[x<220,...,1]).max())})
        if i<3000:m.step()
    with(args.output/'history.csv').open('w',newline='')as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    density=max(r['max_density_error']for r in rows)
    result={'max_density_error':density,'last':rows[-1],'passed':density<=2e-6 and rows[-1]['max_interior_abs_uy']<=2e-7,
            'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest()}
    (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print(result,flush=True)

if __name__=='__main__':main()
