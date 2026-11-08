"""Fourier blocks of the ACTUAL lbmpy step, including characteristic memory."""
import argparse
import csv
import hashlib
import importlib
import json
import sys
import time
from pathlib import Path

import numpy as np

PROJECT=Path('/workspace/aerokinetic')
sys.path.insert(0,str(PROJECT/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from farfield_checks.preflight import initialize, lattice


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--candidate', choices=('moment','moment_fd'), default='moment')
    p.add_argument('--mode', type=int, nargs=2, default=(1, 0))
    p.add_argument('--epsilon', type=float, default=1e-6)
    p.add_argument('--nx', type=int, default=24)
    p.add_argument('--ny', type=int, default=8)
    p.add_argument('--nz', type=int, default=8)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    module = importlib.import_module('aerokinetic.farfield' if args.candidate == 'original'
                                     else 'boundary_' + args.candidate)
    shape = (args.nx, args.ny, args.nz)
    s = lattice(shape, (False, True, True))
    for n in (s.pdf_array_name, s._tmp_arr_name):
        s.data_handling.cpu_arrays[n].fill(0)
    module.install_characteristic_farfield(s, shape, (False, True, True), (.04, 0, 0))
    initialize(s)
    s.boundary_handling.prepare()
    arrays = s.data_handling.cpu_arrays
    pdf_base = arrays[s.pdf_array_name].copy()
    boundary_data = []
    outlet_all = None
    outlet_indices = None
    for block in s.data_handling.iterate(ghost_layers=1):
        for boundary, a in block[s.boundary_handling._index_array_name].boundary_object_to_index_list.items():
            boundary_data.append((a, a.copy()))
            if boundary.normal is None:
                outlet_all = a
                outlet_boundary=boundary
                outlet_indices=np.flatnonzero((a['n0']==1)&(a['n1']==0))
    assert outlet_all is not None and len(outlet_indices)
    outlet=outlet_all[outlet_indices]
    stencil = np.asarray(list(s.method.stencil))
    coords = np.column_stack([outlet[a] - 1 for a in ('x', 'y', 'z')])
    foot = coords.copy()
    assert np.all(foot[:, 0] == shape[0] - 1), (foot.min(axis=0), foot.max(axis=0))
    foot[:, 1] %= shape[1]
    foot[:, 2] %= shape[2]
    histories=['incoming']
    history=arrays[outlet_boundary.incoming_field.name]
    history_base=history.copy()
    clock=arrays[outlet_boundary.clock_field.name]
    clock_base=clock.copy()
    history_location=tuple(outlet[k]for k in ('x','y','z'))+(outlet['dir'],)
    yy, zz = np.meshgrid(np.arange(shape[1]), np.arange(shape[2]), indexing='ij')
    phase = 2 * np.pi * (args.mode[0] * yy / shape[1] + args.mode[1] * zz / shape[2])
    wave = np.exp(1j * phase)
    cos = wave.real
    norm = np.sum(cos * cos)
    wave_links = wave[foot[:, 1], foot[:, 2]]
    cos_links = wave_links.real
    link_norm = np.sum(cos_links * cos_links)
    dimension = shape[0] * 27 + len(histories)
    max_copy_error = 0.

    def evaluate(state, epsilon):
        nonlocal max_copy_error
        for n in (s.pdf_array_name, s._tmp_arr_name):
            arrays[n][...] = pdf_base
        history[...]=history_base
        clock[...]=clock_base
        for a, base in boundary_data:
            a[...] = base
        owned = arrays[s.pdf_array_name][1:-1, 1:-1, 1:-1]
        amplitudes = state[:shape[0]*27].reshape(shape[0], 27)
        owned += epsilon * np.real(amplitudes[:, None, None, :] * wave[None, :, :, None])
        for i, name in enumerate(histories):
            history[history_location] += epsilon * np.real(state[shape[0]*27+i] * wave_links)
        s.time_step()
        result = (arrays[s.pdf_array_name][1:-1, 1:-1, 1:-1] - pdf_base[1:-1, 1:-1, 1:-1]).copy()
        bresult = [history[history_location].copy() - 0.04]
        for data in bresult:
            indices = foot[:, 1]*shape[2]+foot[:, 2]
            means = np.bincount(indices, weights=data, minlength=shape[1]*shape[2])
            counts = np.bincount(indices, minlength=shape[1]*shape[2])
            max_copy_error = max(max_copy_error, float(np.max(abs(data-means[indices]/counts[indices]))))
        return result, bresult

    def central(state, epsilon):
        plus, bp = evaluate(state, epsilon)
        minus, bm = evaluate(state, -epsilon)
        pdf = (plus-minus)/(2*epsilon)
        values = [np.sum(pdf * wave.conj()[None, :, :, None], axis=(1, 2)).reshape(-1)/norm]
        for vp, vm in zip(bp, bm):
            values.append(np.array([np.sum((vp-vm)*wave_links.conj())/(2*epsilon*link_norm)]))
        return np.concatenate(values)

    operator = np.empty((dimension, dimension), dtype=complex)
    basis = np.zeros(dimension, dtype=complex)
    for i in range(dimension):
        basis[i] = 1
        operator[:, i] = central(basis, args.epsilon)
        basis[i] = 0
        if i % 100 == 0:
            print(args.candidate, args.mode, 'column', i, '/', dimension, flush=True)
    eigenvalues, eigenvectors = np.linalg.eig(operator)
    order = np.argsort(abs(eigenvalues))[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]
    # Independent complex modal map: arbitrary coherent state, including sine.
    rng = np.random.default_rng(9431)
    test = rng.normal(size=dimension) + 1j*rng.normal(size=dimension)
    test /= np.linalg.norm(test)
    self_conjugate = all(2*k % size == 0 for k,size in zip(args.mode,shape[1:]))
    if self_conjugate:
        test = test.real
    measured = central(test, args.epsilon/2)
    predicted = operator @ test
    map_error = float(np.linalg.norm(measured-predicted)/np.linalg.norm(predicted))
    with (args.output/'eigenvalues.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(('rank', 'real', 'imaginary', 'magnitude'))
        w.writerows((i, v.real, v.imag, abs(v)) for i,v in enumerate(eigenvalues))
    leading = eigenvectors[:shape[0]*27, 0].reshape(shape[0], 27)
    weights = np.linalg.norm(leading, axis=-1)
    weights /= np.linalg.norm(weights)
    with (args.output/'leading_mode_x.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(('x', 'normalized_pdf_norm'))
        w.writerows(enumerate(weights))
    check = {
        'candidate': args.candidate, 'shape': shape, 'mode': args.mode,
        'epsilon': args.epsilon, 'dimension': dimension,
        'histories': histories, 'outlet_links': len(outlet),
        'spectral_radius': float(abs(eigenvalues[0])),
        'leading_eigenvalue': [float(eigenvalues[0].real),float(eigenvalues[0].imag)],
        'unstable_modes_over_1_plus_1e_6': int(np.sum(abs(eigenvalues)>1+1e-6)),
        'leading_mode_last_4_columns_fraction': float(np.sum(weights[-4:]**2)),
        'modal_map_relative_error': map_error,
        'history_copy_max_error': max_copy_error,
        'operator_sha256': hashlib.sha256(operator.tobytes()).hexdigest(),
        'source': module.__file__,
        'source_sha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        'elapsed_s': time.perf_counter()-started,
    }
    check['map_checks_passed'] = map_error<=2e-5 and max_copy_error<=2e-12
    (args.output/'CHECKS.json').write_text(json.dumps(check, indent=2)+'\n')
    # Matrix stays local; only text tables, checks and hashes are publishable.
    np.save(args.output/'operator.npy', operator)
    print(json.dumps(check, indent=2), flush=True)


if __name__ == '__main__':
    main()
