"""Independent periodic decay and full27-PDF prediction of kinetic startup.

Preserve the failed equilibrium-A0 protocol. These confirmatory controls
keep its viscosity target/tolerance and quantify the initial projection.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import sympy as sp
from lbmpy import LBMConfig,Method,Stencil
from lbmpy.lbstep import LatticeBoltzmannStep
from lbmpy.stencils import LBStencil
from pystencils import CreateKernelConfig
import aerokinetic


def make(nu):
    return LatticeBoltzmannStep(domain_size=(8,32,8),periodicity=(True,True,True),
        time_step_order="collide_stream",kernel_params={"omega":1/(.5+3*nu)},
        lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27),method=Method.CUMULANT,
            relaxation_rate=sp.Symbol("omega"),compressible=True,zero_centered=True),
        config=CreateKernelConfig(data_type="float64"))


def initialize(s, *, sine=False, z=0.):
    for name in(s.pdf_array_name,s._tmp_arr_name):
        s.data_handling.cpu_arrays[name].fill(0)
    for block in s.data_handling.iterate(ghost_layers=True):
        block[s.density_data_name][...]=1
        block[s.velocity_data_name][...]=(.04,0,0)
        block[s.velocity_data_name][...,2]=1e-5*np.sin(2*np.pi*block.midpoint_arrays[1]/32)if sine else z
    s.set_pdf_fields_from_macroscopic_values();s.pre_run();s.time_steps_run=0


def operator(s,epsilon=1e-6):
    initialize(s,z=epsilon)
    plus=s.data_handling.cpu_arrays[s.pdf_array_name][1,1,1].copy()
    initialize(s,z=-epsilon)
    minus=s.data_handling.cpu_arrays[s.pdf_array_name][1,1,1].copy()
    eq_derivative=(plus-minus)/(2*epsilon)
    initialize(s)
    arrays=s.data_handling.cpu_arrays;base=arrays[s.pdf_array_name].copy()
    wave=np.exp(2j*np.pi*(np.arange(32)+.5)/32)[None,:,None,None]
    norm=float(np.sum(wave.real**2)*8*8)
    def evaluate(state,eps):
        for name in(s.pdf_array_name,s._tmp_arr_name):arrays[name][...]=base
        arrays[s.pdf_array_name][1:-1,1:-1,1:-1]+=eps*np.real(state[None,None,None,:]*wave)
        s.time_steps_run=0;s.time_step()
        return(arrays[s.pdf_array_name][1:-1,1:-1,1:-1]-base[1:-1,1:-1,1:-1]).copy()
    def central(state,eps):
        difference=(evaluate(state,eps)-evaluate(state,-eps))/(2*eps)
        return np.sum(difference*wave.conj(),axis=(0,1,2))/norm
    matrix=np.empty((27,27),complex);basis=np.zeros(27,complex)
    for i in range(27):
        basis[i]=1;matrix[:,i]=central(basis,epsilon);basis[i]=0
    rng=np.random.default_rng(81032);test=rng.normal(size=27)+1j*rng.normal(size=27);test/=np.linalg.norm(test)
    predicted=matrix@test
    error=float(np.linalg.norm(central(test,epsilon/2)-predicted)/np.linalg.norm(predicted))
    c=np.asarray(list(s.method.stencil));values,vectors=np.linalg.eig(matrix)
    momentum=c.T@vectors
    polarization=abs(momentum[2])/np.maximum(np.linalg.norm(momentum,axis=0),1e-30)
    candidates=np.flatnonzero((polarization>=.99)&(abs(momentum[2])>.1)&(values.real>.9)&(abs(values.imag)<1e-5))
    if not len(candidates):raise RuntimeError("No classified positive hydrodynamic z-shear mode")
    chosen=candidates[np.argmax(abs(values[candidates]))]
    return matrix,eq_derivative,values[chosen],error,c,values


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);cases=[];k=2*np.pi/32
    for label in(32,64):
        started=time.perf_counter();nu=.04*label/30000;s=make(nu)
        matrix,eq,lam,map_error,c,eigenvalues=operator(s)
        nu_spectral=float(-np.log(abs(lam))/(k*k))
        initialize(s,sine=True);sine=np.sin(k*(np.arange(32)+.5))[None,:,None]
        rows=[];rho_error=0.
        for step in range(3001):
            if step in(0,500,1000,2000,3000):
                s.post_run();rho=s.data_handling.gather_array(s.density_data_name)
                velocity=s.data_handling.gather_array(s.velocity_data_name)
                if not np.isfinite(rho).all()or not np.isfinite(velocity).all():raise RuntimeError("Non-finite periodic field")
                rho_error=max(rho_error,float(np.max(abs(rho-1))))
                amplitude=float(2*np.mean(velocity[...,2]*sine))
                prediction=float((c[:,2]@np.linalg.matrix_power(matrix,step)@eq).real/(c[:,2]@eq))
                rows.append({"step":step,"amplitude":amplitude,"predicted_amplitude_ratio":prediction})
            if step<3000:s.time_step()
        amplitude0=rows[0]["amplitude"]
        early=float(-np.log(rows[-1]["amplitude"]/amplitude0)/(k*k*3000))
        late=float(-np.log(rows[-1]["amplitude"]/rows[2]["amplitude"])/(k*k*2000))
        prediction_error=max(abs(r["amplitude"]/amplitude0-r["predicted_amplitude_ratio"])for r in rows)
        check={"label_N":label,"nu_specified":nu,"nu_from_A0":early,"nu_from_1000_3000":late,
            "nu_from_shear_eigenvalue":nu_spectral,"shear_eigenvalue":[float(lam.real),float(lam.imag)],
            "late_relative_error":abs(late/nu-1),"spectral_relative_error":abs(nu_spectral/nu-1),
            "modal_map_relative_error":map_error,"transient_prediction_relative_error":prediction_error,
            "density_error_max":rho_error,"elapsed_s":time.perf_counter()-started}
        check["passed"]=bool(check["late_relative_error"]<=.05 and check["spectral_relative_error"]<=.05
            and map_error<=2e-5 and prediction_error<=2e-5 and rho_error<=2e-6)
        cases.append(check)
        with(a.output/f"periodic_N{label}.csv").open("w",newline="")as f:
            w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
        with(a.output/f"eigenvalues_N{label}.csv").open("w",newline="")as f:
            w=csv.writer(f);w.writerow(("real","imaginary","magnitude"));w.writerows((v.real,v.imag,abs(v))for v in eigenvalues)
        np.save(a.output/f"operator_local_N{label}.npy",matrix)
        print(json.dumps(check),flush=True)
    result={"cases":cases,"passed":all(r["passed"]for r in cases),"version":aerokinetic.__version__,
        "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope":"Fresh periodic controls and one-step discrete-mode prediction; original A0-based open check remains failed"}
    (a.output/"CHECKS.json").write_text(json.dumps(result,indent=2)+"\n")


if __name__=="__main__":main()
