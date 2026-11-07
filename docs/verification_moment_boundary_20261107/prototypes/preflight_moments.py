"""Independent destination moments, link ownership and collision checks."""
import argparse,hashlib,importlib,json,sys,time
from pathlib import Path
import numpy as np
import sympy as sp
sys.path.insert(0,'/workspace/aerokinetic/tools')
from farfield_checks.preflight import lattice,initialize,macros
import boundary_moment as module


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--candidate',choices=('moment','moment_fd'),default='moment');args=parser.parse_args()
    global module
    module=importlib.import_module('boundary_'+args.candidate)
    out=args.output;out.mkdir(exist_ok=False)
    started=time.perf_counter();shape=(12,14,8);periodic=(False,False,True);base=np.array((.04,0,0))
    s=lattice(shape,periodic)
    stencil=list(s.method.stencil);c=np.asarray(stencil);weights=np.array(list(map(float,s.method.weights)))
    identities={}
    for nx,ny in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
        n=sp.Matrix((nx,ny,0))/sp.sqrt(nx*nx+ny*ny);t=sp.Matrix((-n[1],n[0],0))
        unknown=[i for i,v in enumerate(stencil)if nx*v[0]<0 or ny*v[1]<0]
        total=sum(s.method.weights[i]for i in unknown)
        mean=sp.zeros(3,1);moment=sp.zeros(3,3)
        for i in unknown:
            v=sp.Matrix(stencil[i]);w=s.method.weights[i]/total;mean+=w*v;moment+=w*v*v.T
        face=nx*nx+ny*ny==1
        expected_v=-1 if face else -5*sp.sqrt(2)/11
        expected_t=sp.Rational(1,3)if face else sp.Rational(8,11)
        residuals=[total-(sp.Rational(1,6)if face else sp.Rational(11,36)),mean.dot(n)-expected_v,
                   mean.dot(t),mean[2],(n.T*moment*t)[0],(t.T*moment*t)[0]-expected_t,moment[2,2]-sp.Rational(1,3),moment[0,2],moment[1,2]]
        assert all(sp.simplify(v)==0 for v in residuals),(nx,ny,residuals)
        identities[str((nx,ny))]=True
    print('SYMBOLIC MOMENTS PASSED',flush=True)
    for name in (s.pdf_array_name,s._tmp_arr_name):s.data_handling.cpu_arrays[name].fill(0)
    module.install_characteristic_farfield(s,shape,periodic,base);initialize(s)
    rng=np.random.default_rng(9017)
    s.data_handling.cpu_arrays[s.density_data_name][1:-1,1:-1,1:-1]=1+rng.normal(0,3e-5,shape)
    s.data_handling.cpu_arrays[s.velocity_data_name][1:-1,1:-1,1:-1]=base+rng.normal(0,3e-5,shape+(3,))
    s.set_pdf_fields_from_macroscopic_values();s._sync_src();s.boundary_handling.prepare()
    print('BOUNDARY COMPILED',flush=True)
    array=s.data_handling.cpu_arrays[s.pdf_array_name];before=array.copy();records=[]
    for block in s.data_handling.iterate(ghost_layers=1):
        for boundary,a in block[s.boundary_handling._index_array_name].boundary_object_to_index_list.items():
            records.append((boundary,a))
    assert len(records)==1;boundary,a=records[0]
    coordinates=np.column_stack([a[k]-1 for k in ('x','y','z')]);cells=np.unique(coordinates,axis=0)
    provisional=[];normals=[]
    for p in cells:
        nv=np.array((-1 if p[0]==0 else 1 if p[0]==shape[0]-1 else 0,
                     -1 if p[1]==0 else 1 if p[1]==shape[1]-1 else 0,0))
        normals.append(nv/np.linalg.norm(nv))
        missing=np.any(c*nv<0,axis=-1);source=p-c-missing[:,None]*nv
        provisional.append(before[source[:,0]+1,source[:,1]+1,source[:,2]+1,np.arange(27)])
    provisional=np.asarray(provisional);normals=np.asarray(normals);tangents=np.column_stack((-normals[:,1],normals[:,0],np.zeros(len(cells))))
    rhat=1+provisional.sum(axis=-1);vhat=(provisional@c)/rhat[:,None]
    s.boundary_handling(**s.kernel_params)
    owned_error=float(np.max(abs(array[1:-1,1:-1,1:-1]-before[1:-1,1:-1,1:-1])))
    pulled=[];target=[];copy_error=0.
    for p in cells:
        source=p-c
        pulled.append(array[source[:,0]+1,source[:,1]+1,source[:,2]+1,np.arange(27)])
        mask=np.all(coordinates==p,axis=-1)
        history=s.data_handling.cpu_arrays[boundary.incoming_field.name]
        values=history[tuple(p+1)+(a['dir'][mask],)]
        target.append(values[0]);copy_error=max(copy_error,float(np.max(abs(values-values[0]))))
    pulled=np.asarray(pulled);target=np.asarray(target)
    r=1+pulled.sum(axis=-1);v=(pulled@c)/r[:,None]
    invariant=np.sum(v*normals,axis=-1)-np.log(r)/np.sqrt(3)
    expected_t=np.where(normals@base<0,tangents@base,np.sum(vhat*tangents,axis=-1))
    expected_z=np.where(normals@base<0,base[2],vhat[:,2])
    tangent_error=max(float(np.max(abs(np.sum(v*tangents,axis=-1)-expected_t))),float(np.max(abs(v[:,2]-expected_z))))
    pull_error=float(np.max(abs(invariant-target)))
    s.data_handling.run_kernel(s._lbmKernels[0],**s.kernel_params)
    s.data_handling.swap(s._pdf_arr_name,s._tmp_arr_name,False)
    rho,u=macros(s);rr=rho[tuple(cells[:,i]for i in range(3))];uu=u[tuple(cells[:,i]for i in range(3))]
    collision_error=float(np.max(abs(np.sum(uu*normals,axis=-1)-np.log(rr)/np.sqrt(3)-target)))
    result={'symbolic_identities':identities,'boundary_cells':len(cells),'owned_pdf_edit_max':owned_error,
            'coherent_memory_max_error':copy_error,'pulled_Rminus_max_error':pull_error,
            'post_collision_Rminus_max_error':collision_error,'tangent_velocity_target_max_error':tangent_error,
            'tolerance':2e-12,'elapsed_s':time.perf_counter()-started,
            'source_sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}
    result['passed']=owned_error==0 and max(copy_error,pull_error,collision_error,tangent_error)<=2e-12
    (out/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
