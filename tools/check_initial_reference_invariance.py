"""Record the reference-point invariant before/after a numerical initialization fix."""
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from aerokinetic import Solver,Section,naca0012,Flow,Grid
    import aerokinetic
    solvers=[]
    for reference in ((.25,0),(.4,-.03)):
        solvers.append(Solver(Section((naca0012(51),),1,reference),Flow(reynolds=30000,alpha_deg=4,perturbation=.002,seed=31),
            Grid(cells_per_chord=16,upstream_chords=1.5,downstream_chords=3,transverse_chords=1.5,
                 span_chords=.5,memory_limit_gib=1,openmp_threads=1)))
    a,b=solvers
    va=a._stepper.data_handling.gather_array(a._stepper.velocity_data_name)
    vb=b._stepper.data_handling.gather_array(b._stepper.velocity_data_name)
    delta=float(np.max(np.abs(va-vb)))
    angle=-np.deg2rad(4)
    rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    dx,dy=rotation@np.array([.15,-.03])
    loads=[]
    for _ in range(12):
        x,y=a.step(sample=True),b.step(sample=True)
        loads.append([y['cl']-x['cl'],y['cd']-x['cd'],y['cm']-x['cm']-dx*x['cl']+dy*x['cd']])
    report={'runtime':aerokinetic.__version__,'path':aerokinetic.__file__,
        'max_initial_velocity_difference':delta,'load_transfer_residuals':loads,
        'max_load_transfer_residual':float(np.max(np.abs(loads))),
        'initial_passed':delta<=2e-14,'loads_passed':bool(np.max(np.abs(loads))<=2e-10)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items()if k!='load_transfer_residuals'},indent=2))


if __name__=='__main__':main()
