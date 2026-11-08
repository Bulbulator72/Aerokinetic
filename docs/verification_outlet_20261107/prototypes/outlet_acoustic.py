"""Fixed normal pulse through a genuinely non-glancing downstream face."""
import argparse,csv,importlib,json,math,sys,hashlib
from pathlib import Path
import numpy as np
from lbmpy.boundaries import UBB
sys.path.insert(0,'/workspace/aerokinetic/tools')
from farfield_checks.preflight import lattice,initialize,macros

def main():
    p=argparse.ArgumentParser();p.add_argument('--candidate',choices=('convective','original','copy'),required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    module=importlib.import_module('aerokinetic.farfield'if args.candidate=='original'else'boundary_'+args.candidate)
    results={};cs=math.sqrt(1/3)
    for name in ('ubb','candidate'):
        s=lattice((256,8,8),(False,True,True))
        s.boundary_handling.set_boundary(UBB((.04,0,0),name='inflow'),mask_callback=lambda x,y,z:x<0)
        boundary=UBB((.04,0,0),name='outflow')if name=='ubb'else module.CharacteristicFarfield((1,0,0),(256,8,8),(False,True,True),stepper=s)
        s.boundary_handling.set_boundary(boundary,mask_callback=lambda x,y,z:x>256)
        def pulse(block):
            x,_,_=block.midpoint_arrays;dr=2e-5*np.exp(-.5*((x-180)/10)**2)
            block[s.density_data_name][...]=1+dr
            block[s.velocity_data_name][...,0]=.04+cs*dr
        initialize(s,pulse=pulse);rows=[]
        for step in range(1,361):
            s.time_step();rho,u=macros(s)
            assert np.isfinite(rho).all()and np.isfinite(u).all()
            dr=float(rho[216].mean()-1);du=float(u[216,...,0].mean()-.04)
            rows.append({'step':step,'delta_rho':dr,'delta_ux':du,'r_plus':du+cs*dr,'r_minus':du-cs*dr})
        with(args.output/(name+'.csv')).open('w',newline='')as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        incident=max(abs(r['r_plus'])for r in rows if 20<=r['step']<=130)
        reflected=max(abs(r['r_minus'])for r in rows if 140<=r['step']<=330)
        results[name]={'incident':incident,'reflected':reflected,'ratio':reflected/incident}
    results['passed']=results['candidate']['ratio']<=.1 and results['candidate']['ratio']<=results['ubb']['ratio']/5
    results['source_sha256']=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    (args.output/'CHECKS.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2),flush=True)

if __name__=='__main__':main()
