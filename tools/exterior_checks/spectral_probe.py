"""Linearize the actual complete pre-collision PDF map, including the ring.

The wave-vector is the unstable mode of the previous boundary. The matrix
is local evidence; only its hash and text eigenvalue tables are published.
"""
import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from exterior_fd import ExteriorFD


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--nx',type=int,default=24)
    p.add_argument('--ny',type=int,default=48)
    p.add_argument('--nz',type=int,default=8)
    p.add_argument('--mode',type=int,nargs=2,default=(23,2))
    p.add_argument('--epsilon',type=float,default=1e-6)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter()
    m=ExteriorFD((a.nx,a.ny,a.nz),(False,True,True));m.initialize()
    s=m.s;arrays=s.data_handling.cpu_arrays
    base=arrays[s.pdf_array_name].copy();initial=m.state().copy()
    yy,zz=np.meshgrid(np.arange(a.ny),np.arange(a.nz),indexing='ij')
    wave=np.exp(2j*np.pi*(a.mode[0]*yy/a.ny+a.mode[1]*zz/a.nz))
    norm=np.sum(wave.real**2);dimension=m.shape[0]*27

    def evaluate(v,eps):
        for n in(s.pdf_array_name,s._tmp_arr_name):arrays[n][...]=base
        m.step_count=0;m.initial=initial.copy()
        owned=arrays[s.pdf_array_name][1:-1,1:-1,1:-1]
        owned+=eps*np.real(v.reshape(m.shape[0],1,1,27)*wave[None,:,:,None])
        m.step()
        return (arrays[s.pdf_array_name][1:-1,1:-1,1:-1]-base[1:-1,1:-1,1:-1]).copy()

    def central(v,eps):
        d=(evaluate(v,eps)-evaluate(v,-eps))/(2*eps)
        return(np.sum(d*wave.conj()[None,:,:,None],axis=(1,2))/norm).reshape(-1)

    operator=np.empty((dimension,dimension),complex);basis=np.zeros(dimension,complex)
    for i in range(dimension):
        basis[i]=1;operator[:,i]=central(basis,a.epsilon);basis[i]=0
        if i%100==0:print('column',i,'/',dimension,flush=True)
    values,vectors=np.linalg.eig(operator);order=np.argsort(abs(values))[::-1]
    values=values[order];vectors=vectors[:,order]
    rng=np.random.default_rng(9431);v=rng.normal(size=dimension)+1j*rng.normal(size=dimension)
    if all(2*k%n==0 for k,n in zip(a.mode,(a.ny,a.nz))):v=v.real
    v/=np.linalg.norm(v);predicted=operator@v;measured=central(v,a.epsilon/2)
    error=float(np.linalg.norm(measured-predicted)/np.linalg.norm(predicted))
    weights=np.linalg.norm(vectors[:,0].reshape(m.shape[0],27),axis=1)
    weights/=np.linalg.norm(weights)
    with(a.output/'eigenvalues.csv').open('w',newline='')as f:
        w=csv.writer(f);w.writerow(('rank','real','imaginary','magnitude'))
        w.writerows((i,v.real,v.imag,abs(v))for i,v in enumerate(values))
    with(a.output/'leading_mode_x.csv').open('w',newline='')as f:
        w=csv.writer(f);w.writerow(('augmented_x','normalized_pdf_norm'))
        w.writerows(enumerate(weights))
    result={'shape_core':m.core_shape,'shape_augmented':m.shape,'mode':a.mode,
        'epsilon':a.epsilon,'dimension':dimension,'storage':'all27 pre-collision PDFs including exterior; no independent history at this non-glancing outlet',
        'spectral_radius':float(abs(values[0])),
        'leading_eigenvalue':[float(values[0].real),float(values[0].imag)],
        'unstable_modes_over_1_plus_1e_6':int(np.sum(abs(values)>1+1e-6)),
        'leading_mode_last4_augmented_x_fraction':float(np.sum(weights[-4:]**2)),
        'modal_map_relative_error':error,'map_checks_passed':error<=2e-5,
        'passed':bool(error<=2e-5 and np.max(abs(values))<=1+1e-6),
        'operator_sha256':hashlib.sha256(operator.tobytes()).hexdigest(),
        'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest(),
        'elapsed_s':time.perf_counter()-started,
        'limits':'A single Fourier block of the linearized body-free map; not proof of all-mode or nonlinear airfoil stability.'}
    np.save(a.output/'operator.npy',operator)
    (a.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
