from pathlib import Path
import sys
import pystencils as ps
import pystencils.cpu.kernelcreation as cpu
sys.path.insert(0,'/workspace/aerokinetic/tools')
from farfield_checks.preflight import lattice
import boundary_moment
old=cpu.move_constants_before_loop
def observing(ast):
    Path('/workspace/aerokinetic_moment_boundary_20261107/before_hoist.c').write_text(str(ps.get_code_str(ast)))
    return old(ast)
cpu.move_constants_before_loop=observing
s=lattice((12,14,8),(False,False,True))
boundary_moment.install_characteristic_farfield(s,(12,14,8),(False,False,True),(.04,0,0))
print('compiled')
