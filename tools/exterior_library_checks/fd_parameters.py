"""Analytic full-population FD stress at two molecular viscosity parameters."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import sympy as sp
from lbmpy import LBMConfig, Method, Stencil
from lbmpy.lbstep import LatticeBoltzmannStep
from lbmpy.stencils import LBStencil
from pystencils import CreateKernelConfig

import aerokinetic
from aerokinetic.exterior import ExteriorBoundary


def check(label):
    nu = .04 * label / 30000
    core, periodic = (16, 20, 8), (False, False, True)
    stepper = LatticeBoltzmannStep(domain_size=(18, 22, 8), periodicity=periodic,
        time_step_order="collide_stream", kernel_params={"omega": 1 / (.5 + 3 * nu)},
        lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27), method=Method.CUMULANT,
            relaxation_rate=sp.Symbol("omega"), compressible=True, zero_centered=True),
        config=CreateKernelConfig(data_type="float64"))
    boundary = ExteriorBoundary(stepper, core, periodic)
    boundary.initialize()
    for _ in range(20):
        boundary.step()
    rho, velocity = boundary.macros(full=True)
    equilibrium_error = max(float(np.max(abs(rho - 1))), float(np.max(abs(velocity - (.04, 0, 0)))))
    x, y, _ = np.meshgrid(*(np.arange(n) - p + .5 for n, p in zip(boundary.shape, boundary.pad)), indexing="ij")
    a, b, c, d, e = 1e-6, 2e-7, -3e-7, 4e-8, 5e-7
    state = np.zeros(boundary.shape + (4,))
    state[..., 0] = 1e-6 * x - 2e-6 * y
    state[..., 1] = .04 + a * x + b * y * y
    state[..., 2] = c * x * y
    state[..., 3] = d * x * x + e * y
    px, py = x[boundary.index], y[boundary.index]
    gradient = np.zeros((len(px), 3, 3))
    gradient[:, 0, 0] = a; gradient[:, 1, 0] = 2 * b * py
    gradient[:, 0, 1] = c * py; gradient[:, 1, 1] = c * px
    gradient[:, 0, 2] = 2 * d * px; gradient[:, 1, 2] = e
    gradient_error = float(np.max(abs(boundary.gradients(state)[:, :, 1:] - gradient)))
    zero_centered_pdf, _ = boundary.regularized(state)
    full_pdf = zero_centered_pdf + boundary.weights
    expected_rho = np.exp(state[boundary.index][:, 0])
    expected_u = state[boundary.index][:, 1:]
    mass_error = float(np.max(abs(full_pdf.sum(axis=1) - expected_rho)))
    momentum_error = float(np.max(abs(full_pdf @ boundary.c - expected_rho[:, None] * expected_u)))
    measured_stress = np.einsum("nq,qa,qb->nab", full_pdf, boundary.c, boundary.c)
    measured_stress -= expected_rho[:, None, None] * (expected_u[:, :, None] * expected_u[:, None, :] + np.eye(3) / 3)
    # Continuum stress for pre-collision PDFs: shear tau=.5+3nu, bulk tau=1.
    dilation = np.trace(gradient, axis1=1, axis2=2)[:, None, None] * np.eye(3)
    deviator = gradient + np.swapaxes(gradient, 1, 2) - 2 * dilation / 3
    expected_stress = -expected_rho[:, None, None] / 3 * ((.5 + 3 * nu) * deviator + 2 * dilation / 3)
    stress_error = float(np.max(abs(measured_stress - expected_stress)))
    return {"label_N": label, "molecular_nu": nu, "equilibrium_20step_max_error": equilibrium_error,
            "polynomial_gradient_max_error": gradient_error, "full_pdf_mass_max_error": mass_error,
            "full_pdf_momentum_max_error": momentum_error, "pre_collision_FD_stress_max_error": stress_error,
            "passed": equilibrium_error <= 5e-13 and max(gradient_error, mass_error, momentum_error, stress_error) <= 2e-12}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    cases = [check(label) for label in (32, 64)]
    result = {"cases": cases, "passed": all(case["passed"] for case in cases),
              "version": aerokinetic.__version__, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Two viscosity parameters on the same analytic lattice; no airfoil grid convergence"}
    (args.output / "CHECKS.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise RuntimeError("FD parameter verification failed")


if __name__ == "__main__":
    main()
