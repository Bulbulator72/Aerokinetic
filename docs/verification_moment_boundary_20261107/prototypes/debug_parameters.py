import sys,runpy
from pathlib import Path
import pystencils.cpu.cpujit as jit
old=jit.generate_c
def observe(ast,**kwargs):
    bad=[(str(p.symbol),type(p.symbol).__name__)for p in ast.get_parameters()if not hasattr(p.symbol,'dtype')]
    if bad:print('UNTYPED PARAMETERS',bad,flush=True)
    return old(ast,**kwargs)
jit.generate_c=observe
sys.argv=['preflight_moments.py','--output','/workspace/aerokinetic_moment_boundary_20261107/preflight_06']
runpy.run_path(str(Path(__file__).with_name('preflight_moments.py')),run_name='__main__')
