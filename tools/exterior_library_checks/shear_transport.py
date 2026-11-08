"""Analytic shear decay for the viscosity parameters needed at N32/N64.

The manufactured lattice stays fixed. These are transport checks, not an
airfoil grid study. An inlet perturbation cannot convect to the mid-plane
within the specified interval (120 < 192cells).
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import sympy as sp
from lbmpy import LBMConfig, Method, Stencil
from lbmpy.lbstep import LatticeBoltzmannStep
from lbmpy.stencils import LBStencil
from pystencils import CreateKernelConfig
import aerokinetic
from aerokinetic.exterior import ExteriorBoundary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    result = []
    for label in (32, 64):
        started = time.perf_counter()
        nu = .04*label/30000
        s = LatticeBoltzmannStep(domain_size=(386,32,8),periodicity=(False,True,True),
            time_step_order="collide_stream",kernel_params={"omega":1/(.5+3*nu)},
            lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27),method=Method.CUMULANT,
                relaxation_rate=sp.Symbol("omega"),compressible=True,zero_centered=True),
            config=CreateKernelConfig(data_type="float64"))
        model = ExteriorBoundary(s,(384,32,8),(False,True,True))
        k = 2*np.pi/32
        def pulse(block, coords):
            block[s.velocity_data_name][...,2] = 1e-5*np.sin(k*coords[1])
        model.initialize(pulse)
        sine = np.sin(k*(np.arange(32)+.5))
        rows = []
        max_density = 0.
        for step in range(3001):
            if step in (0,500,1000,2000,3000):
                rho,velocity = model.macros(full=True)
                if not np.isfinite(rho).all() or not np.isfinite(velocity).all():
                    raise RuntimeError("Non-finite manufactured shear field")
                max_density = max(max_density,float(np.max(abs(rho-1))))
                core = velocity[model.core]
                amplitude = float(2*np.mean(core[192,:,:,2]*sine[:,None]))
                rows.append({"step":step,"amplitude":amplitude,"density_error_max":float(np.max(abs(rho-1)))})
            if step<3000:
                model.step()
        measured = float(-np.log(rows[-1]["amplitude"]/rows[0]["amplitude"])/(k*k*3000))
        relative = abs(measured/nu-1)
        check = {"label_N":label,"nu_specified":nu,"nu_measured":measured,
            "nu_relative_error":relative,"max_density_error":max_density,
            "passed":bool(relative<=.05 and max_density<=2e-6),"elapsed_s":time.perf_counter()-started}
        result.append(check)
        with (a.output/f"history_N{label}.csv").open("w",newline="")as f:
            w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
        print(json.dumps(check),flush=True)
    source = Path(__import__("aerokinetic.exterior",fromlist=[""]).__file__)
    summary = {"cases":result,"passed":all(r["passed"]for r in result),
        "version":aerokinetic.__version__,"installed_source":str(source),
        "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
        "scope":"Analytic viscosity check on one manufactured lattice; not airfoil convergence or tunnel validation"}
    (a.output/"CHECKS.json").write_text(json.dumps(summary,indent=2)+"\n")


if __name__=="__main__":main()
