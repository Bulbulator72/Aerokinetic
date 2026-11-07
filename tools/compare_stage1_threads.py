"""Describe a failed bitwise thread check without changing its acceptance rule."""
import json
from pathlib import Path


def main():
    import numpy as np
    from lbmpy.stencils import LBStencil
    from lbmpy.enums import Stencil
    work=Path('/workspace/aerokinetic_stage1_20261106')
    pilots=[json.loads((work/f'profile_threads{i}.json').read_text())for i in (1,2)]
    repeats=[json.loads((work/f'profile_snapshot_threads{i}.json').read_text())for i in (1,2)]
    with np.load(work/'profile_snapshot_threads1.npz',allow_pickle=False)as a,np.load(work/'profile_snapshot_threads2.npz',allow_pickle=False)as b:
        mask=a['fluid_mask'];np.testing.assert_array_equal(mask,b['fluid_mask'])
        x,y=a['pdf'],b['pdf'];inner=(slice(1,-1),)*3
        fluid_x,fluid_y=x[inner][mask],y[inner][mask]
        rho_x,rho_y=fluid_x.sum(axis=-1)+1,fluid_y.sum(axis=-1)+1
        stencil=np.asarray(list(LBStencil(Stencil.D3Q27)))
        ux=fluid_x@stencil/rho_x[:,None];uy=fluid_y@stencil/rho_y[:,None]
        ghost=np.ones(x.shape[:-1],dtype=bool);ghost[inner]=False
        result={'version':'0.5.1','original_exact_hash_criterion':'failed',
            'exact_equality':{'full':bool(np.array_equal(x,y)),
                'owned_fluid':bool(np.array_equal(fluid_x,fluid_y)),
                'owned_solid':bool(np.array_equal(x[inner][~mask],y[inner][~mask])),
                'ghost':bool(np.array_equal(x[ghost],y[ghost]))},
            'max_abs_fluid_pdf_difference':float(np.max(abs(fluid_x-fluid_y))),
            'max_abs_fluid_density_difference':float(np.max(abs(rho_x-rho_y))),
            'max_abs_fluid_velocity_difference':float(np.max(abs(ux-uy))),
            'max_abs_fluid_velocity_difference_over_U':float(np.max(abs(ux-uy))/.04),
            'last_load_differences_2_minus_1':{key:repeats[1]['last_sample'][key]-repeats[0]['last_sample'][key]for key in ('cl','cd','cm')},
            'repeat_same_thread_count_pdf_exact':[p['pdf_sha256']==r['pdf_sha256']for p,r in zip(pilots,repeats)],
            'timing_pilot_mlups':[p['compute_loop_mlups']for p in pilots],
            'timing_repeat_mlups':[p['compute_loop_mlups']for p in repeats],
            'two_thread_speedup_pilot':pilots[0]['compute_seconds']/pilots[1]['compute_seconds'],
            'two_thread_speedup_repeat':repeats[0]['compute_seconds']/repeats[1]['compute_seconds'],
            'scope':'Descriptive follow-up. No replacement tolerance/pass label introduced after the failed exact-hash criterion. No accuracy or workstation scaling claim.'}
    from pystencils.cpu.cpujit import get_compiler_config
    result['compiler_configuration']=dict(get_compiler_config())
    (work/'RESOURCE_CHECK.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
