"""Same normal acoustic acceptance gate on the unchanged physical core."""
import argparse,csv,hashlib,json
from pathlib import Path
import numpy as np
from lbmpy.boundaries import UBB
from exterior_fd import ExteriorFD,lattice,CS

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--axis',choices=('x','y'),default='y');args=p.parse_args();args.output.mkdir(exist_ok=False)
    result={}
    axis=0 if args.axis=='x'else 1
    shape=(256,8,8)if axis==0 else(8,256,8)
    periodic=(False,True,True)if axis==0 else(True,False,True)
    selector=[slice(None)]*3;selector[axis]=216;selector=tuple(selector)
    for name in ('ubb','exterior_fd'):
        rows=[]
        if name=='exterior_fd':
            m=ExteriorFD(shape,periodic)
            def pulse(block,coords):
                q=coords[axis];dr=2e-5*np.exp(-.5*((q-180)/10)**2)
                block[m.s.density_data_name][...]=1+dr;block[m.s.velocity_data_name][...,axis]=(.04 if axis==0 else 0)+CS*dr
            m.initialize(pulse)
            step=m.step;macros=m.macros
        else:
            s=lattice(shape,periodic)
            s.boundary_handling.set_boundary(UBB((.04,0,0),name='ubb_all'),mask_callback=lambda x,y,z:((x<0)|(x>256))if axis==0 else((y<0)|(y>256)))
            for block in s.data_handling.iterate(ghost_layers=True):
                q=block.midpoint_arrays[axis];dr=2e-5*np.exp(-.5*((q-180)/10)**2)
                block[s.density_data_name][...]=1+dr;block[s.velocity_data_name][...]=(.04,0,0);block[s.velocity_data_name][...,axis]=(.04 if axis==0 else 0)+CS*dr
            s.set_pdf_fields_from_macroscopic_values();s.pre_run();step=s.time_step
            def macros():
                s.post_run();return s.data_handling.gather_array(s.density_data_name),s.data_handling.gather_array(s.velocity_data_name)
        for i in range(1,361):
            step();rho,u=macros();assert np.isfinite(rho).all()and np.isfinite(u).all()
            dr=float(rho[selector].mean()-1);du=float(u[selector+(axis,)].mean()-(.04 if axis==0 else 0))
            rows.append({'step':i,'delta_rho':dr,'delta_ux':du,'r_plus':du+CS*dr,'r_minus':du-CS*dr})
        with(args.output/(name+'.csv')).open('w',newline='')as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        incident=max(abs(r['r_plus'])for r in rows if 20<=r['step']<=130);reflected=max(abs(r['r_minus'])for r in rows if 140<=r['step']<=330)
        result[name]={'incident':incident,'reflected':reflected,'ratio':reflected/incident}
        print(name,result[name],flush=True)
    result['passed']=bool(result['exterior_fd']['ratio']<=.1 and result['exterior_fd']['ratio']<=result['ubb']['ratio']/5)
    result['source_sha256']=hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest()
    (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print('PASSED',result['passed'],flush=True)

if __name__=='__main__':main()
