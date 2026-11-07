"""Previous128-square radiation gate versus the same384-square truncation control."""
import argparse,csv,hashlib,json,time
from pathlib import Path
import numpy as np
from lbmpy.boundaries import UBB
from exterior_fd import ExteriorFD,lattice

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(exist_ok=False)
    snapshots=(0,80,160,240,320,360);fields={};records={}
    for name,size in [('control',384),('ubb',128),('exterior_fd',128)]:
        begin=time.perf_counter()
        if name=='exterior_fd':
            m=ExteriorFD((size,size,8),(False,False,True))
            def pulse(block,coords):
                x,y,_=coords;block[m.s.density_data_name][...]=1+2e-5*np.exp(-.5*((x-size/2)**2+(y-size/2)**2)/100)
            m.initialize(pulse);step=m.step;macros=m.macros
        else:
            s=lattice((size,size,8),(False,False,True))
            s.boundary_handling.set_boundary(UBB((.04,0,0),name='all_ubb'),mask_callback=lambda x,y,z:(x<0)|(x>size)|(y<0)|(y>size))
            for block in s.data_handling.iterate(ghost_layers=True):
                x,y,_=block.midpoint_arrays
                block[s.density_data_name][...]=1+2e-5*np.exp(-.5*((x-size/2)**2+(y-size/2)**2)/100)
                block[s.velocity_data_name][...]=(.04,0,0)
            s.set_pdf_fields_from_macroscopic_values();s.pre_run();step=s.time_step
            def macros():
                s.post_run();return s.data_handling.gather_array(s.density_data_name),s.data_handling.gather_array(s.velocity_data_name)
        start=(size-128)//2;rows=[]
        for i in range(361):
            if i in snapshots:
                rho,u=macros();rho=rho[start:start+128,start:start+128,4];u=u[start:start+128,start:start+128,4]
                assert np.isfinite(rho).all()and np.isfinite(u).all()
                fields[name,i]=rho.copy()
                rows.append({'step':i,'max_core_density_perturbation':float(abs(rho-1).max()),'max_core_velocity_perturbation':float(abs(u-(.04,0,0)).max()),
                             'density_slice_sha256':hashlib.sha256(rho.tobytes()).hexdigest()})
            if i<360:step()
        records[name]={'elapsed_s':time.perf_counter()-begin,'snapshots':rows};print(name,'done',flush=True)
    norm=np.linalg.norm(fields['control',0]-1);errors=[]
    for i in snapshots:
        r={'step':i}
        for name in ('ubb','exterior_fd'):
            diff=fields[name,i]-fields['control',i]
            r[name+'_density_l2_error_over_initial_pulse']=float(np.linalg.norm(diff)/norm)
            r[name+'_density_max_error']=float(abs(diff).max())
        errors.append(r)
    value=errors[-1]['exterior_fd_density_l2_error_over_initial_pulse'];baseline=errors[-1]['ubb_density_l2_error_over_initial_pulse']
    result={'cases':records,'errors':errors,'passed':value<=.2 and value<=baseline/3,
            'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest(),
            'scope':'Numerical truncation control at unchanged dx/dt/collision, not experimental validation'}
    (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print('RESULT',value,baseline,result['passed'],flush=True)

if __name__=='__main__':main()
