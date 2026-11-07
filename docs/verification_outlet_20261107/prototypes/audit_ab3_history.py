"""Independent check of old/new boundary-history ordering in generated C."""
import hashlib
import json
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, '/workspace/aerokinetic/tools')
from farfield_checks.preflight import lattice, initialize, macros
import boundary_ab3 as boundary_module

shape = (24, 8, 8)
s = lattice(shape, (False, True, True))
for name in (s.pdf_array_name, s._tmp_arr_name):
    s.data_handling.cpu_arrays[name].fill(0)
boundary_module.install_characteristic_farfield(s, shape, (False, True, True), (.04, 0, 0))
initialize(s)
yy, zz = np.meshgrid(np.arange(8), np.arange(8), indexing='ij')
phase = 2*np.pi*(yy+2*zz)/8
vel = s.data_handling.cpu_arrays[s.velocity_data_name]
vel[1:-1, 1:-1, 1:-1, 1] = 1e-5*np.sin(phase)
vel[1:-1, 1:-1, 1:-1, 2] = 2e-5*np.cos(phase)
s.set_pdf_fields_from_macroscopic_values()
s.boundary_handling.prepare()
rho, u = macros(s)
F = .04*((np.roll(u[-1, :, :, 1], -1, axis=0)-np.roll(u[-1, :, :, 1], 1, axis=0))/2
         +(np.roll(u[-1, :, :, 2], -1, axis=1)-np.roll(u[-1, :, :, 2], 1, axis=1))/2)
stencil = np.asarray(list(s.method.stencil))
outlet = None
for block in s.data_handling.iterate(ghost_layers=1):
    for boundary, a in block[s.boundary_handling._index_array_name].boundary_object_to_index_list.items():
        if boundary.normal == (1, 0, 0):
            outlet = a
assert outlet is not None
foot = np.column_stack([outlet[a]-1 for a in ('x', 'y', 'z')])+stencil[outlet['dir']]-np.array((1,0,0))
foot[:,1:] %= 8
y,z=foot[:,1],foot[:,2]
outlet['incoming'] = .04+3e-6*np.cos(phase[y,z])
outlet['derivative'] = 2e-6*np.sin(phase[y,z])
outlet['derivative2'] = -1e-6*np.cos(phase[y,z])
old = outlet.copy()
expected = old['incoming']+(23*F[y,z]-16*old['derivative']+5*old['derivative2'])/12
s._sync_src()
s.boundary_handling(**s.kernel_params)
errors = {
    'incoming_ab3': float(np.max(abs(outlet['incoming']-expected))),
    'derivative_is_current_F': float(np.max(abs(outlet['derivative']-F[y,z]))),
    'derivative2_is_old_derivative': float(np.max(abs(outlet['derivative2']-old['derivative']))),
}
result = {'errors':errors, 'absolute_tolerance':2e-13,
          'passed':max(errors.values())<=2e-13,
          'source_sha256':hashlib.sha256(Path(boundary_module.__file__).read_bytes()).hexdigest()}
Path('/workspace/aerokinetic_outlet_20261107/AB3_HISTORY_AUDIT.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
