"""Independent owned-cell PDF budgets and fluid/solid adjacency load checks.

This audit uses geometric adjacency, not the production boundary index list.
It performs exactly one boundary call and the existing update kernel per step.
It does not replace the physical solution or calibrate experimental coefficients.
"""
import argparse
import json
import os
from pathlib import Path
import sys

import numpy as np


def totals(solver):
    lbm=solver._stepper
    n=solver.plan.shape
    pdf=lbm.data_handling.cpu_arrays[lbm.pdf_array_name]
    a=pdf[1:n[0]+1,1:n[1]+1,1:n[2]+1,:]
    mask=solver._fluid
    j=np.einsum('...q,qk->...k',a,solver._stencil,optimize=True)
    rx=(np.arange(n[0])+.5+solver.plan.origin[0]/solver.plan.dx)[:,None,None]
    ry=(np.arange(n[1])+.5+solver.plan.origin[1]/solver.plan.dx)[None,:,None]
    return np.array([mask.sum()+a[mask].sum(),*j[mask].sum(axis=0),
                     (rx*j[...,1]-ry*j[...,0])[mask].sum()])


def adjacency_fluxes(solver):
    from lbmpy.maxwellian_equilibrium import get_weights
    lbm=solver._stepper;nx,ny,nz=solver.plan.shape
    pdf=lbm.data_handling.cpu_arrays[lbm.pdf_array_name]
    weights=np.array([float(x)for x in get_weights(lbm.method.stencil)])
    f=pdf[1:nx+1,1:ny+1,1:nz+1,:]
    fluid=~solver._solid_xy
    solid=np.pad(solver._solid_xy,1,constant_values=False)
    ix,iy=np.arange(nx)[:,None],np.arange(ny)[None,:]
    rx=ix+.5+solver.plan.origin[0]/solver.plan.dx
    ry=iy+.5+solver.plan.origin[1]/solver.plan.dx
    body=np.zeros(5);external=np.zeros(5)
    for d,e in enumerate(solver._stencil):
        cx,cy,cz=map(int,e)
        out=f[...,d]+weights[d]
        wall=fluid&solid[1+cx:nx+1+cx,1+cy:ny+1+cy]
        inverse=solver._inverse[d]
        reflected=pdf[1+cx:nx+1+cx,1+cy:ny+1+cy,1+cz:nz+1+cz,inverse]+weights[inverse]
        pair=(out+reflected)[wall]
        value=pair.sum()
        body[1:4]+=value*e
        body[4]+=((rx*cy-ry*cx)[wall]*pair.sum(axis=-1)).sum()
        body[0]+=(reflected-out)[wall].sum()  # mass entering fluid at wall
        leaving=fluid&((ix+cx<0)|(ix+cx>=nx)|(iy+cy<0)|(iy+cy>=ny))
        entering=fluid&((ix-cx<0)|(ix-cx>=nx)|(iy-cy<0)|(iy-cy>=ny))
        incoming=pdf[1-cx:nx+1-cx,1-cy:ny+1-cy,1-cz:nz+1-cz,d]+weights[d]
        mass=incoming[entering].sum()-out[leaving].sum()
        external[0]+=mass;external[1:4]+=mass*e
        external[4]+=((rx*cy-ry*cx)[entering]*incoming[entering].sum(axis=-1)).sum()
        external[4]-=((rx*cy-ry*cx)[leaving]*out[leaving].sum(axis=-1)).sum()
    return body,external


def audited_step(solver):
    lbm=solver._stepper
    before=totals(solver)
    lbm._sync_src()
    lbm.boundary_handling(**lbm.kernel_params)
    edited=totals(solver)-before
    body,external=adjacency_fluxes(solver)
    loads=solver._loads()
    from aerokinetic.solver import coefficients
    expected=coefficients(body[1:4],body[4],solver.flow.lattice_speed,
                          solver.grid.cells_per_chord,solver.plan.shape[2])
    lbm.data_handling.run_kernel(lbm._lbmKernels[0],**lbm.kernel_params)
    lbm.data_handling.swap(lbm._pdf_arr_name,lbm._tmp_arr_name,False)
    solver.steps+=1;lbm.time_steps_run=solver.steps
    change=totals(solver)-before
    wall_fluid=np.array([body[0],*-body[1:4],-body[4]])
    residual=change-edited-external-wall_fluid
    volume=int(solver._fluid.sum())
    scaled=residual/volume;scaled[-1]/=solver.grid.cells_per_chord
    return {'step':solver.steps,'production_loads':[loads[x]for x in ('cl','cd','cm')],
        'adjacency_loads':list(expected),'production_minus_adjacency':(np.array([loads[x]for x in ('cl','cd','cm')])-expected).tolist(),
        'wall_mass_in':body[0],'body_force':body[1:4].tolist(),'body_moment_z':body[4],
        'external_flux':external.tolist(),'owned_boundary_edit':edited.tolist(),
        'fluid_change':change.tolist(),'residual_per_cell_angular_scaled':scaled.tolist()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('MPLBACKEND','Agg')
    from aerokinetic import Flow,Grid,Section,Solver,naca0012
    import aerokinetic
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    records={}
    def make(alpha,reference=(.25,0)):
        return Solver(Section((naca0012(51),),1.,reference),Flow(reynolds=30000,alpha_deg=alpha,perturbation=0),
            Grid(cells_per_chord=16,upstream_chords=1.5,downstream_chords=3,
                 transverse_chords=1.5,span_chords=.5,memory_limit_gib=1,openmp_threads=1))
    for alpha in (0,4,-4):
        solver=make(alpha)
        case=[]
        for i in range(12):
            r=audited_step(solver);case.append(r)
            np.testing.assert_allclose(r['production_minus_adjacency'],0,atol=2e-10,rtol=0)
            np.testing.assert_allclose(r['residual_per_cell_angular_scaled'],0,atol=2e-12,rtol=0)
        bounds=[]
        for block in solver._stepper.data_handling.iterate(ghost_layers=1):
            lists=block[solver._stepper.boundary_handling._index_array_name].boundary_object_to_index_list
            for wall in solver._wall_objects:
                ind=lists[wall]
                bounds.append({a:[int((ind[a]+block.offset[k]).min()),int((ind[a]+block.offset[k]).max())]
                               for k,a in enumerate(('x','y','z'))})
        records[str(alpha)]={'records':case,'wall_index_extents':bounds,'shape':solver.plan.shape}
        print('AUDIT',alpha,'max load discrepancy',np.max(np.abs([r['production_minus_adjacency']for r in case])),flush=True)
    positive=np.array([r['production_loads']for r in records['4']['records']])
    negative=np.array([r['production_loads']for r in records['-4']['records']])
    np.testing.assert_allclose(positive,negative*np.array([-1,1,-1]),atol=2e-10,rtol=0)
    np.testing.assert_allclose(np.array([r['production_loads']for r in records['0']['records']])[:,[0,2]],0,atol=2e-10,rtol=0)
    shifted=make(4,(.4,-.03));shift=[]
    angle=-np.deg2rad(4)
    rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    dx,dy=rotation@np.array([.15,-.03])
    for i in range(12):
        r=audited_step(shifted);value=np.array(r['production_loads']);old=positive[i]
        np.testing.assert_allclose(value[:2],old[:2],atol=2e-10,rtol=0)
        np.testing.assert_allclose(value[2],old[2]+dx*old[0]-dy*old[1],atol=2e-10,rtol=0)
        shift.append(r)
    result={'runtime_version':aerokinetic.__version__,'installed':aerokinetic.__file__,'cases':records,
        'shifted_reference':shift,'independent_budget':'passed','force_adjacency':'passed',
        'mirror_and_reference_shift':'passed','validation':'not_performed; numerical audit only'}
    (args.output/'CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k not in ('cases','shifted_reference')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
