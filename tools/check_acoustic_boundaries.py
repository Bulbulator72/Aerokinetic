"""Small-amplitude plane-wave characterization of the pinned transverse boundaries."""
import csv
import json
import os
from pathlib import Path
import numpy as np


def main():
    work=Path('/workspace/aerokinetic_stage1_20261106/acoustics');work.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('MPLBACKEND','Agg')
    import sympy as sp
    from lbmpy import LBMConfig,Method,Stencil
    from lbmpy.stencils import LBStencil
    from lbmpy.lbstep import LatticeBoltzmannStep
    from lbmpy.boundaries import UBB,ExtrapolationOutflow
    from pystencils import CreateKernelConfig
    cs=np.sqrt(1/3);nu=.04*32/30000
    result={}
    for name in ('ubb','extrapolation'):
        lbm=LatticeBoltzmannStep(domain_size=(8,256,8),periodicity=(True,False,True),
            lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27),method=Method.CUMULANT,
                relaxation_rate=sp.Symbol('omega'),compressible=True,zero_centered=True),
            kernel_params={'omega':1/(.5+3*nu)},config=CreateKernelConfig(data_type='float64'))
        lbm.boundary_handling.set_boundary(UBB((.04,0,0),name='lower'),mask_callback=lambda x,y,z:y<0)
        upper=UBB((.04,0,0),name='upper')if name=='ubb'else ExtrapolationOutflow((0,1,0),lbm.method,initial_density=1.,initial_velocity=(.04,0,0))
        lbm.boundary_handling.set_boundary(upper,mask_callback=lambda x,y,z:y>256)
        for block in lbm.data_handling.iterate(ghost_layers=True):
            x,y,z=block.midpoint_arrays
            pulse=2e-5*np.exp(-.5*((y-180)/10)**2)
            block[lbm.density_data_name][...]=1+pulse
            block[lbm.velocity_data_name][...,0]=.04
            block[lbm.velocity_data_name][...,1]=cs*pulse
            block[lbm.velocity_data_name][...,2]=0
        lbm.set_pdf_fields_from_macroscopic_values();lbm.pre_run()
        records=[]
        for step in range(1,361):
            lbm.time_step();lbm.post_run()
            rho=lbm.data_handling.gather_array(lbm.density_data_name)
            velocity=lbm.data_handling.gather_array(lbm.velocity_data_name)
            assert np.isfinite(rho).all()and np.isfinite(velocity).all()
            dr=float(rho[:,216,:].mean()-1)
            du=float(velocity[:,216,:,1].mean())
            records.append({'step':step,'delta_rho':dr,'delta_uy':du,'r_plus':du+cs*dr,'r_minus':du-cs*dr})
        with (work/(name+'.csv')).open('w',newline='')as handle:
            writer=csv.DictWriter(handle,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
        incident=max(abs(r['r_plus'])for r in records if 20<=r['step']<=130)
        reflected=max(abs(r['r_minus'])for r in records if 140<=r['step']<=330)
        result[name]={'incident_probe_peak':incident,'reflected_probe_peak':reflected,
                      'probe_amplitude_ratio_including_propagation':reflected/incident}
        print('ACOUSTIC',name,result[name],flush=True)
    result['interpretation']='Probe reflection sensitivity only; no universal coefficient or replacement farfield validation inferred'
    (work/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
