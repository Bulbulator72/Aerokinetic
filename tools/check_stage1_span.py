"""Measure spanwise variation in an owned uniform D3Q27 checkpoint field."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('checkpoint',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import numpy as np
    from lbmpy.stencils import LBStencil
    from lbmpy.enums import Stencil
    with np.load(args.checkpoint,allow_pickle=False)as a:
        m=json.loads(str(a['metadata']))
        assert m['format']=='aerokinetic_cpu_checkpoint'and m['numerics_revision']==1
        idx=m['fields'].index('lbm_pdfSrc')
        f=a[f'field_{idx}'][1:-1,1:-1,1:-1]
        rho=f.sum(axis=-1)+1
        assert np.isfinite(rho).all()and np.min(rho)>0
        velocity=np.einsum('...q,qi->...i',f,np.asarray(list(LBStencil(Stencil.D3Q27))),optimize=False)/rho[...,None]
        speed=m['project']['flow']['lattice_speed']
        cells=m['project']['grid']['cells_per_chord']
        result={'checkpoint_sha256':hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
            'steps':m['steps'],'t_star':m['steps']*speed/cells,
            'max_abs_span_velocity_over_freestream':float(np.max(abs(velocity[...,2]))/speed),
            'max_spanwise_velocity_variation_over_freestream':float(np.max(np.ptp(velocity,axis=2)))/speed,
            'scope':'All owned nodes including solids: an upper bound for the fluid subset. No temporal/span/grid convergence inferred.',
            'interpretation':'Describe this one terminal field only; a spanwise-invariant field does not demonstrate resolved 3D turbulence.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
