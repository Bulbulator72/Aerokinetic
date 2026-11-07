"""Independent wave/shear/open-link checks of the research farfield."""
from pathlib import Path
import argparse
import csv
import json
import math
import time
import numpy as np
from lbmpy.boundaries import UBB
from .preflight import lattice,install,initialize,macros,WORK


def uniform_ubb(s,shape,periodic):
    def mask(x,y,z):
        outer=False
        for a,(p,v)in enumerate(zip(periodic,(x,y,z))):
            if not p:outer=outer|((v<0)|(v>shape[a]))
        return outer
    s.boundary_handling.set_boundary(UBB((.04,0,0),name='all_farfield_ubb'),mask_callback=mask)


def totals(s):
    g=s.data_handling.cpu_arrays[s.pdf_array_name][1:-1,1:-1,1:-1]
    c=np.asarray(list(s.method.stencil))
    return np.r_[np.prod(g.shape[:-1])+g.sum(),np.einsum('...q,qa->a',g,c,optimize=True)]


def external_flux(s,shape,periodic):
    pdf=s.data_handling.cpu_arrays[s.pdf_array_name];owned=pdf[1:-1,1:-1,1:-1]
    coord=np.ogrid[tuple(slice(0,n)for n in shape)]
    flux=np.zeros(4)
    for q,(c,w)in enumerate(zip(s.method.stencil,s.method.weights)):
        leaving=np.zeros(shape,dtype=bool);entering=leaving.copy()
        for axis,p in enumerate(periodic):
            if p:continue
            leaving|=(coord[axis]+c[axis]<0)|(coord[axis]+c[axis]>=shape[axis])
            entering|=(coord[axis]-c[axis]<0)|(coord[axis]-c[axis]>=shape[axis])
        incoming=pdf[tuple(slice(1-c[a],shape[a]+1-c[a])for a in range(3))+(q,)]+float(w)
        outgoing=owned[...,q]+float(w)
        mass=incoming[entering].sum()-outgoing[leaving].sum()
        flux+=mass*np.r_[1.,c]
    return flux


def audited_step(s,shape,periodic):
    before=totals(s);s._sync_src();s.boundary_handling(**s.kernel_params)
    edited=totals(s)-before;flux=external_flux(s,shape,periodic)
    s.data_handling.run_kernel(s._lbmKernels[0],**s.kernel_params)
    s.data_handling.swap(s._pdf_arr_name,s._tmp_arr_name,False)
    return {'residual_per_cell':((totals(s)-before-edited-flux)/np.prod(shape)).tolist(),
            'owned_boundary_edit':edited.tolist(),'external_flux':flux.tolist()}


def run_budget():
    out=WORK/'budget';out.mkdir(exist_ok=False)
    shape=(8,256,8);periodic=(True,False,True)
    s=lattice(shape,periodic);install(s,shape,periodic)
    def pulse(block):
        _,y,_=block.midpoint_arrays;dr=2e-5*np.exp(-.5*((y-180)/10)**2)
        block[s.density_data_name][...]=1+dr;block[s.velocity_data_name][...,1]=math.sqrt(1/3)*dr
    initialize(s,pulse=pulse);records=[]
    for step in range(1,361):
        if step<=12 or step in (100,130,160,200,250,300,360):
            r=audited_step(s,shape,periodic);r['step']=step;records.append(r)
        else:s.time_step()
    maximum=max(abs(x)for r in records for x in r['residual_per_cell'])
    edits=max(abs(x)for r in records for x in r['owned_boundary_edit'])
    result={'records':records,'max_abs_budget_per_cell':maximum,'max_owned_boundary_edit':edits,
        'budget_passed':maximum<=2e-12,'no_owned_boundary_edit':edits==0}
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n')
    print('BUDGET',maximum,edits,flush=True)


def run_shear():
    out=WORK/'shear';out.mkdir(exist_ok=False)
    shape=(256,8,8);periodic=(False,True,True);s=lattice(shape,periodic);install(s,shape,periodic)
    def pulse(block):
        x,_,_=block.midpoint_arrays
        block[s.velocity_data_name][...,1]=2e-5*np.exp(-.5*((x-180)/12)**2)
    initialize(s,pulse=pulse);records=[]
    x=np.arange(256)+.5;nu=.04*32/30000
    for step in range(3001):
        if step%100==0:
            rho,v=macros(s);assert np.isfinite(rho).all()and np.isfinite(v).all()
            width=math.sqrt(144+2*nu*step)
            exact=2e-5*12/width*np.exp(-.5*((x-180-.04*step)/width)**2)
            records.append({'step':step,'max_density_error':float(np.max(abs(rho-1))),
                'max_uy_error_vs_gaussian':float(np.max(abs(v[...,1]-exact[:,None,None]))),
                'max_interior_abs_uy':float(np.max(abs(v[x<220,...,1])))})
        if step<3000:s.time_step()
    with(out/'history.csv').open('w',newline='')as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    result={'max_density_error':max(r['max_density_error']for r in records),'last':records[-1],
        'passed':max(r['max_density_error']for r in records)<=2e-6 and records[-1]['max_interior_abs_uy']<=2e-7,
        'scope':'Convected transverse Gaussian is an exact NS mode; finite LBM truncation/boundary errors remain. This is not a tunnel validation.'}
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print('SHEAR',result,flush=True)


def run_radiation():
    out=WORK/'radiation';out.mkdir(exist_ok=False);records={};snapshots=(0,80,160,240,320,360)
    for name,size in (('control',384),('ubb',128),('riemann',128)):
        started=time.perf_counter();shape=(size,size,8);periodic=(False,False,True)
        s=lattice(shape,periodic)
        install(s,shape,periodic)if name=='riemann'else uniform_ubb(s,shape,periodic)
        def pulse(block):
            x,y,_=block.midpoint_arrays
            block[s.density_data_name][...]=1+2e-5*np.exp(-.5*((x-size/2)**2+(y-size/2)**2)/100)
        initialize(s,pulse=pulse);start=(size-128)//2;case=[]
        for step in range(361):
            if step in snapshots:
                rho,v=macros(s);assert np.isfinite(rho).all()and np.isfinite(v).all()
                rho=rho[start:start+128,start:start+128];v=v[start:start+128,start:start+128]
                np.savez_compressed(out/f'{name}_{step:04d}.npz',rho=rho[:,:,4],velocity=v[:,:,4])
                case.append({'step':step,'max_core_density_perturbation':float(np.max(abs(rho-1))),
                             'max_core_velocity_perturbation':float(np.max(abs(v-(.04,0,0))))})
            if step<360:s.time_step()
        records[name]={'snapshots':case,'elapsed_s':time.perf_counter()-started};print('RADIATION',name,'done',flush=True)
    errors=[]
    with np.load(out/'control_0000.npz',allow_pickle=False)as a:norm=np.linalg.norm(a['rho']-1)
    for step in snapshots:
        with np.load(out/f'control_{step:04d}.npz',allow_pickle=False)as a:reference=a['rho']
        row={'step':step}
        for name in ('ubb','riemann'):
            with np.load(out/f'{name}_{step:04d}.npz',allow_pickle=False)as a:
                row[name+'_density_l2_error_over_initial_pulse']=float(np.linalg.norm(a['rho']-reference)/norm)
                row[name+'_density_max_error']=float(np.max(abs(a['rho']-reference)))
        errors.append(row)
    last=errors[-1];value=last['riemann_density_l2_error_over_initial_pulse'];baseline=last['ubb_density_l2_error_over_initial_pulse']
    result={'cases':records,'errors':errors,'passed':value<=.2 and value<=baseline/3,
        'scope':'Larger-domain numerical truncation control at identical dx/dt/collision/initial field, not experimental truth.'}
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print('RADIATION RESULT',result,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('case',choices=('budget','shear','radiation'));args=parser.parse_args()
    {'budget':run_budget,'shear':run_shear,'radiation':run_radiation}[args.case]()
