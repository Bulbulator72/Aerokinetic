"""Reproduce preregistered open-boundary checks against the installed wheel.

Example: python tools/verify_farfield.py --case all --output NEW_DIRECTORY
See docs/verification_farfield_20261107/ for fixed cases, criteria and limits.
The historical CSV label 'riemann' means the currently installed research boundary.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    names=('preflight','budget','shear','radiation','vortex','seam_vortex','glancing','glancing_3d')
    parser.add_argument('--case',choices=(*names,'all'),default='all')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import aerokinetic
    from farfield_checks import preflight,verification,vortex,seam_vortex,glancing
    package=Path(aerokinetic.__file__).resolve().parent
    if not package.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError('Use an installed wheel, not an editable/source import')
    args.output.mkdir(parents=True,exist_ok=True)
    preflight.WORK=verification.WORK=vortex.WORK=glancing.WORK=args.output
    functions={'preflight':preflight.main,'budget':verification.run_budget,
        'shear':verification.run_shear,'radiation':verification.run_radiation,
        'vortex':vortex.main,'seam_vortex':seam_vortex.main,
        'glancing':glancing.run_glancing,'glancing_3d':glancing.run_glancing_3d}
    selected=names if args.case=='all'else(args.case,)
    runtime={'version':aerokinetic.__version__,'installed':str(package),'python':sys.version,
        'runtime_sha256':{p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(package.rglob('*.py'))},
        'verification_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__),*sorted((Path(__file__).parent/'farfield_checks').glob('*.py'))]}}
    for name in selected:
        provenance=args.output/f'RUNTIME_{name}.json'
        if provenance.exists():raise FileExistsError(provenance)
        provenance.write_text(json.dumps(runtime,indent=2)+'\n')
        functions[name]()
        result=json.loads((args.output/name/'CHECKS.json').read_text())
        gates=[v for k,v in result.items()if k in ('passed','wave_gate','budget_passed','no_owned_boundary_edit')]
        if name=='preflight':gates.extend(v['passed']for v in result.values()if isinstance(v,dict)and 'passed'in v)
        if not gates or not all(gates):raise AssertionError(f'{name} failed; retain the result and original criteria')


if __name__=='__main__':main()
