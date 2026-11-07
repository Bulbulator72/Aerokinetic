"""Identical numerical cases/criteria to the preregistered prototype checks."""
from pathlib import Path
import csv
import itertools
import json
import math
import sys
import time
import numpy as np
import sympy as sp
from lbmpy import LBMConfig,Method,Stencil
from lbmpy.stencils import LBStencil
from lbmpy.lbstep import LatticeBoltzmannStep
from lbmpy.boundaries import UBB
from pystencils import AssignmentCollection,CreateKernelConfig
from aerokinetic.farfield import CharacteristicFarfield, install_characteristic_farfield

WORK=Path(__file__).resolve().parent
NU=.04*32/30000


def lattice(shape,periodic,velocity=(.04,0,0)):
    return LatticeBoltzmannStep(domain_size=shape,periodicity=periodic,
        lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27),method=Method.CUMULANT,
            relaxation_rate=sp.Symbol('omega'),compressible=True,zero_centered=True),
        kernel_params={'omega':1/(.5+3*NU)},config=CreateKernelConfig(data_type='float64'))


def install(lbm,shape,periodic,velocity=(.04,0,0)):
    install_characteristic_farfield(lbm,shape,periodic,velocity)


def initialize(lbm,velocity=(.04,0,0),pulse=None):
    for block in lbm.data_handling.iterate(ghost_layers=True):
        block[lbm.density_data_name][...]=1
        block[lbm.velocity_data_name][...]=velocity
        if pulse:pulse(block)
    lbm.set_pdf_fields_from_macroscopic_values();lbm.pre_run()


def macros(lbm):
    lbm.post_run()
    return lbm.data_handling.gather_array(lbm.density_data_name),lbm.data_handling.gather_array(lbm.velocity_data_name)


def check_equilibrium(method):
    eq=method.get_equilibrium(conserved_quantity_equations=AssignmentCollection([]))
    cqc=method.conserved_quantity_computation;rho=cqc.density_symbol;u=cqc.velocity_symbols
    differences=[]
    for c,w,a in zip(method.stencil,method.weights,eq.main_assignments):
        terms=[sp.Rational(2,3)-v*v+(sp.Rational(3,2)*v*v-sp.Rational(1,2))*k*k+k*v/2 for k,v in zip(c,u)]
        differences.append(sp.simplify(rho*sp.prod(terms)-w-a.rhs))
    assert all(d==0 for d in differences),differences


def main():
    out=WORK/'preflight';out.mkdir(exist_ok=False)
    results={}
    for velocity in ((.04,0,0),(.04,.01,.005)):
        started=time.perf_counter();s=lattice((32,40,8),(False,False,True),velocity)
        check_equilibrium(s.method)
        install(s,(32,40,8),(False,False,True),velocity);initialize(s,velocity)
        for _ in range(100):s.time_step()
        rho,v=macros(s)
        result={'density_max_error':float(np.max(abs(rho-1))),
            'velocity_max_error':float(np.max(abs(v-velocity))),'elapsed_s':time.perf_counter()-started}
        result['passed']=max(result['density_max_error'],result['velocity_max_error'])<=5e-13
        results[str(velocity)]=result;print('UNIFORM',velocity,result,flush=True)
    cs=math.sqrt(1/3)
    for boundary in ('ubb','riemann'):
        s=lattice((8,256,8),(True,False,True))
        s.boundary_handling.set_boundary(UBB((.04,0,0),name='lower'),mask_callback=lambda x,y,z:y<0)
        upper=UBB((.04,0,0),name='upper')if boundary=='ubb'else CharacteristicFarfield((0,1,0),(8,256,8),(True,False,True),stepper=s)
        s.boundary_handling.set_boundary(upper,mask_callback=lambda x,y,z:y>256)
        def pulse(block):
            _,y,_=block.midpoint_arrays
            dr=2e-5*np.exp(-.5*((y-180)/10)**2)
            block[s.density_data_name][...]=1+dr
            block[s.velocity_data_name][...,1]=cs*dr
        initialize(s,pulse=pulse)
        records=[]
        for step in range(1,361):
            s.time_step();rho,v=macros(s)
            assert np.isfinite(rho).all()and np.isfinite(v).all()
            dr=float(rho[:,216,:].mean()-1);du=float(v[:,216,:,1].mean())
            records.append({'step':step,'delta_rho':dr,'delta_uy':du,'r_plus':du+cs*dr,'r_minus':du-cs*dr})
        with(out/(boundary+'.csv')).open('w',newline='')as f:
            writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
        incident=max(abs(r['r_plus'])for r in records if 20<=r['step']<=130)
        reflected=max(abs(r['r_minus'])for r in records if 140<=r['step']<=330)
        results[boundary]={'incident':incident,'reflected':reflected,'ratio':reflected/incident}
        print('ACOUSTIC',boundary,results[boundary],flush=True)
    results['wave_gate']=results['riemann']['ratio']<=.1 and results['riemann']['ratio']<=results['ubb']['ratio']/5
    (out/'CHECKS.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':main()
