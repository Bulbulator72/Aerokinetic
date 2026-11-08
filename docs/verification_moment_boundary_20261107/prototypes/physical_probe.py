"""Run existing physical verification protocols with a separate prototype.

Only the boundary factory/class and output root are replaced. Numerical
fixtures, collision parameters, sampling and acceptance gates are unchanged.
"""
import argparse
import hashlib
import importlib
import json
import sys
import time
from pathlib import Path

PROJECT=Path('/workspace/aerokinetic')
sys.path.insert(0,str(PROJECT/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parent))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--candidate', choices=('moment','moment_fd'), required=True)
    p.add_argument('--groups', nargs='+', choices=('preflight','budget','shear','radiation','vortex','seam_vortex','glancing','glancing_3d'), required=True)
    p.add_argument('--output', type=Path, required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    candidate=importlib.import_module('boundary_'+args.candidate)
    from farfield_checks import preflight, verification, vortex, seam_vortex, glancing
    modules=(preflight,verification,vortex,seam_vortex,glancing)
    protocol_hashes={m.__name__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()for m in modules}
    for m in modules:
        m.WORK=args.output
        if hasattr(m,'CharacteristicFarfield'):m.CharacteristicFarfield=candidate.CharacteristicFarfield
        if hasattr(m,'install_characteristic_farfield'):m.install_characteristic_farfield=candidate.install_characteristic_farfield
    functions={'preflight':preflight.main,'budget':verification.run_budget,'shear':verification.run_shear,
               'radiation':verification.run_radiation,'vortex':vortex.main,'seam_vortex':seam_vortex.main,
               'glancing':glancing.run_glancing,'glancing_3d':glancing.run_glancing_3d}
    context={'candidate':args.candidate,'source':candidate.__file__,
             'candidate_sha256':hashlib.sha256(Path(candidate.__file__).read_bytes()).hexdigest(),
             'protocol_hashes':protocol_hashes,'groups':args.groups,'scope':'Prototype code verification; not a replacement installed wheel or tunnel validation'}
    (args.output/'RUN_CONTEXT.json').write_text(json.dumps(context,indent=2)+'\n')
    for name in args.groups:
        start=time.perf_counter();print('START',name,flush=True)
        functions[name]()
        print('FINISH',name,'seconds',time.perf_counter()-start,flush=True)

if __name__=='__main__':main()
