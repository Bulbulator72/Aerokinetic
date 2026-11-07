"""Real guard failure/replay using the unchanged released0.5.1 runtime."""
from dataclasses import asdict
from pathlib import Path
import importlib.util,json
import numpy as np
import aerokinetic
from aerokinetic import Flow,Grid,Project,Section,Solver,RunSettings,naca0012
from aerokinetic.solver import NumericalFailure
assert aerokinetic.__version__=='0.5.1'
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
work=args.output;work.mkdir(parents=True,exist_ok=False)
p=Project(Section((naca0012(31),)),Flow(reynolds=30000,alpha_deg=0,lattice_speed=.04,perturbation=0),
 Grid(cells_per_chord=16,upstream_chords=1,downstream_chords=2,transverse_chords=1,memory_limit_gib=2),
 RunSettings(duration_convective=.2,sample_every=1,frame_every=25,check_every=1,checkpoint_every=1,average_after=0))
s=Solver(p.section,p.flow,p.grid)
# Two individually admissible density pulses collide; the unchanged guard
# should reject their combined amplitude. This is a diagnostic fixture,
# not an airfoil validation or a proposed flow initial condition.
for block in s._stepper.data_handling.iterate(ghost_layers=True):
 _,y,_=block.midpoint_arrays
 a=.085*np.exp(-.5*((y-5.5)/1.5)**2);b=.085*np.exp(-.5*((y-12.5)/1.5)**2)
 block[s._stepper.density_data_name][...]=1+a+b
 u=block[s._stepper.velocity_data_name];u[...,1]=np.sqrt(1/3)*(a-b)
 u[...,1][u[...,0]==0]=0
s._stepper.set_pdf_fields_from_macroscopic_values()
s.diagnostics()
(work/'INITIAL_CONDITION.json').write_text(json.dumps({'kind':'synthetic colliding acoustic pulses for guard/replay verification only','density_amplitudes':[.085,.085],'centers_y_lattice':[5.5,12.5],'sigma_lattice':1.5,'velocity_uy':'cs*(pulse1-pulse2)','validation':'not an experimental benchmark'},indent=2)+'\n')
try:s.run(output=work/'parent',**asdict(p.run))
except NumericalFailure:pass
summary=json.loads((work/'parent/summary.json').read_text())
assert summary['status']=='failed'and(work/'parent/checkpoint.npz').is_file(),summary
spec=importlib.util.spec_from_file_location('capture_failure',str(Path(__file__).resolve().parents[2]/'tools/capture_failure.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
result=module.capture(work/'parent',work/'replay')
assert result['parent_files_unchanged']and result['status']=='reproduced'
print('RELEASED051 REAL FAILURE REPLAY PASSED',flush=True)
