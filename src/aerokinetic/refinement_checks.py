"""Executable acceptance experiment for the *remapping* refinement milestone.

Uniform periodic grids are remeshed at synchronized times. There is NO spatial
coarse/fine interface in this experiment. An accepted result cannot establish
interface accuracy, airfoil grid convergence or agreement with wind tunnels.
"""
import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import time

import numpy as np

from ._io import file_hash, write_json
from .refinement import AcousticLevel, D3Q27Transfer, prolong_cell_averages


def periodic_prediction_study():
    """Exact finite-volume averages of a smooth tensor-product scalar field."""
    def averages(n):
        x = (np.arange(n) + 0.5) / n
        scale = np.sinc(1 / n)
        return (scale * np.sin(2 * np.pi * x)[:, None, None]
                * scale * np.cos(2 * np.pi * x)[None, :, None]
                * (1 + 0.1 * scale * np.sin(2 * np.pi * x)[None, None, :]))

    results = []
    for n in (8, 16, 32):
        coarse = averages(n)
        predicted = prolong_cell_averages(np.pad(coarse, 2, mode="wrap"))
        error = predicted - averages(2 * n)
        results.append({"coarse_cells_per_side": n, "fine_cells_per_side": 2 * n,
                        "rms_error": float(np.sqrt(np.mean(error**2))),
                        "max_error": float(np.max(abs(error))),
                        "mean_difference": float(predicted.mean() - coarse.mean())})
    for i in range(1, len(results)):
        ratio = results[i - 1]["rms_error"] / results[i]["rms_error"]
        results[i]["error_ratio"] = ratio
        results[i]["observed_order_scalar_prediction_only"] = math.log2(ratio)
    return results


def _periodic_halo(pdf):
    return np.pad(pdf, ((2, 2), (2, 2), (2, 2), (0, 0)), mode="wrap")


def _read_pdf(lbm):
    return np.asarray(lbm.data_handling.gather_array(lbm.pdf_array_name)).copy()


def _write_pdf(lbm, pdf):
    if pdf.shape != (*lbm.domain_size, 27):
        raise ValueError("Experiment population shape mismatch")
    for block in lbm.data_handling.iterate(ghost_layers=False):
        block[lbm.pdf_array_name][...] = pdf
    lbm.post_run()


def _integrals(pdf, stencil, dx):
    # Independent brute physical-population moments; not the remapper's decoder.
    c = np.array(stencil)
    weights = np.prod(np.where(c == 0, 2 / 3, 1 / 6), axis=1)
    f = pdf + weights
    mass = float(f.sum() * dx**3)
    momentum = np.array([sum(f[..., q].sum() * c[q, a] for q in range(27)) * dx**3
                         for a in range(3)])
    return mass, momentum


def advected_shear_wave(progress=print):
    """Analytic advective diffusion, using actual pinned D3Q27 cumulant kernels.

    A separate fine-grid negative control intentionally uses the wrong shear
    relaxation rate. Detecting its factor-two diffusion error tests sensitivity
    of the acceptance experiment. It is never used by the production solver.
    """
    import sympy as sp
    from lbmpy import LBMConfig, Method, Stencil
    from lbmpy.lbstep import LatticeBoltzmannStep
    from lbmpy.stencils import LBStencil
    from pystencils import CreateKernelConfig

    base = AcousticLevel(1.0, 1.0, 0.59, 1.0)
    fine_level = base.refined()
    nu, amplitude, advective_velocity = base.shear_viscosity, 1e-4, 0.04
    length, total_time = 24.0, 120.0
    k = 2 * np.pi / length
    omega_s, omega_b = sp.symbols("omega_s omega_b")
    lb_config = LBMConfig(stencil=LBStencil(Stencil.D3Q27), method=Method.CUMULANT,
                          # The first two rates control shear and bulk separately.
                          # Extra unit rates are ignored by upstream; length >27
                          # avoids interpreting this as one rate for each cumulant.
                          relaxation_rates=[omega_s, omega_b] + [sp.Integer(1)] * 27,
                          compressible=True, zero_centered=True)

    def create(shape, level):
        return LatticeBoltzmannStep(domain_size=shape, periodicity=True,
            time_step_order="collide_stream", lbm_config=lb_config,
            kernel_params={"omega_s": 1 / level.tau_shear, "omega_b": 1 / level.tau_bulk},
            config=CreateKernelConfig(data_type="float64"))

    progress("Compiling periodic coarse and fine pre-collision cumulant kernels")
    coarse_lbm = create((24, 4, 4), base)
    fine_lbm = create((48, 8, 8), fine_level)
    coupling = D3Q27Transfer(coarse_lbm.method.stencil, base,
                             zero_centered=True, pdf_stage="pre_collision")

    def initial(lbm, level):
        lbm.data_handling.fill(lbm.density_data_name, 1.0, ghost_layers=True)
        for block in lbm.data_handling.iterate(ghost_layers=True):
            x, _, _ = block.midpoint_arrays
            velocity = block[lbm.velocity_data_name]
            velocity[...] = 0
            velocity[..., 0] = advective_velocity
            velocity[..., 1] = amplitude * np.sinc(k * level.dx / (2 * np.pi)) * np.sin(k * x * level.dx)
        lbm.set_pdf_fields_from_macroscopic_values()
        lbm.post_run()

    def harmonic(lbm, level, t):
        lbm.post_run()
        v = np.asarray(lbm.data_handling.gather_array(lbm.velocity_data_name))[..., 1].mean(axis=(1, 2))
        x = (np.arange(v.size) + 0.5) * level.dx
        coefficient = 2 * np.mean(v * np.exp(-1j * k * x))
        normalized = coefficient / (amplitude * np.sinc(k * level.dx / (2 * np.pi)))
        expected = -1j * np.exp(-nu * k**2 * t) * np.exp(-1j * k * advective_velocity * t)
        return {"time": t, "dx": level.dx, "real": float(normalized.real),
                "imag": float(normalized.imag), "amplitude": float(abs(normalized)),
                "expected_real": float(expected.real), "expected_imag": float(expected.imag),
                "relative_complex_error": float(abs(normalized - expected) / abs(expected))}

    def evolve(lbm, level, start, duration, records):
        count = round(duration / level.dt)
        stride = round(10 / level.dt)
        if not records:
            records.append(harmonic(lbm, level, start))
        done = 0
        while done < count:
            steps = min(stride, count - done)
            lbm.run(steps)
            done += steps
            records.append(harmonic(lbm, level, start + done * level.dt))

    def fitted_viscosity(records, after):
        tail = [r for r in records if r["time"] >= after]
        slope = np.polyfit([r["time"] for r in tail], np.log([r["amplitude"] for r in tail]), 1)[0]
        return float(-slope / k**2)

    results, trajectories, remaps = {}, {}, []
    for label, lbm, level in (("uniform_coarse", coarse_lbm, base),
                               ("uniform_fine", fine_lbm, fine_level)):
        progress(f"Running {label}")
        initial(lbm, level)
        records = []
        evolve(lbm, level, 0, total_time, records)
        measured_nu = fitted_viscosity(records, 40)
        results[label] = {"final_relative_complex_error": records[-1]["relative_complex_error"],
                          "fitted_physical_viscosity": measured_nu,
                          "relative_viscosity_error": abs(measured_nu / nu - 1)}
        trajectories[label] = records

    progress("Running synchronized coarse -> fine -> coarse remeshing")
    initial(coarse_lbm, base)
    records = []
    initial_mass, initial_j = _integrals(_read_pdf(coarse_lbm), coarse_lbm.method.stencil, base.dx)
    evolve(coarse_lbm, base, 0, 40, records)
    source = _read_pdf(coarse_lbm)
    destination = coupling.prolong(_periodic_halo(source))

    def record_remap(label, source, source_level, destination, target_level, t):
        m0, j0 = _integrals(source, coarse_lbm.method.stencil, source_level.dx)
        m1, j1 = _integrals(destination, coarse_lbm.method.stencil, target_level.dx)
        volume = length * 4 * 4
        remaps.append({"direction": label, "time": t,
                       "relative_mass_change": abs(m1 - m0) / m0,
                       "momentum_change_per_volume": ((j1 - j0) / volume).tolist()})

    record_remap("coarse_to_fine", source, base, destination, fine_level, 40)
    _write_pdf(fine_lbm, destination)
    records.append(harmonic(fine_lbm, fine_level, 40))
    evolve(fine_lbm, fine_level, 40, 40, records)
    fine_records = [r for r in records if r["dx"] == fine_level.dx and r["time"] >= 50]
    measured_fine_nu = fitted_viscosity(fine_records, 50)
    source = _read_pdf(fine_lbm)
    destination = coupling.restrict(source)
    record_remap("fine_to_coarse", source, fine_level, destination, base, 80)
    _write_pdf(coarse_lbm, destination)
    records.append(harmonic(coarse_lbm, base, 80))
    evolve(coarse_lbm, base, 80, 40, records)
    measured_nu = fitted_viscosity(records, 90)
    final_mass, final_j = _integrals(_read_pdf(coarse_lbm), coarse_lbm.method.stencil, base.dx)
    results["coarse_fine_coarse"] = {
        "final_relative_complex_error": records[-1]["relative_complex_error"],
        "fitted_physical_viscosity": measured_nu,
        "relative_viscosity_error": abs(measured_nu / nu - 1),
        "fitted_viscosity_during_fine_segment": measured_fine_nu,
        "fine_segment_relative_viscosity_error": abs(measured_fine_nu / nu - 1),
        "relative_total_mass_drift": abs(final_mass - initial_mass) / initial_mass,
        "total_momentum_drift_per_volume": ((final_j - initial_j) / (length * 4 * 4)).tolist(),
    }
    trajectories["coarse_fine_coarse"] = records

    progress("Running the deliberately unscaled-relaxation negative control")
    fine_lbm.kernel_params["omega_s"] = 1 / base.tau_shear
    initial(fine_lbm, fine_level)
    records = []
    evolve(fine_lbm, fine_level, 0, total_time, records)
    measured_nu = fitted_viscosity(records, 40)
    results["negative_control_unscaled_tau"] = {
        "fitted_physical_viscosity": measured_nu,
        "ratio_to_requested_viscosity": measured_nu / nu,
        "relative_viscosity_error": abs(measured_nu / nu - 1),
        "final_relative_complex_error": records[-1]["relative_complex_error"],
    }
    trajectories["negative_control_unscaled_tau"] = records
    positives = [results[label] for label in ("uniform_coarse", "uniform_fine", "coarse_fine_coarse")]
    numerical_pass = (all(r["final_relative_complex_error"] <= 0.02
                          and r["relative_viscosity_error"] <= 0.02 for r in positives)
                      and results["coarse_fine_coarse"]["fine_segment_relative_viscosity_error"] <= 0.02)
    conservation_pass = (
        all(r["relative_mass_change"] <= 5e-11 and max(map(abs, r["momentum_change_per_volume"])) <= 2e-12
            for r in remaps)
        and results["coarse_fine_coarse"]["relative_total_mass_drift"] <= 5e-11
        and max(map(abs, results["coarse_fine_coarse"]["total_momentum_drift_per_volume"])) <= 2e-12)
    negative = results["negative_control_unscaled_tau"]
    negative_pass = abs(negative["ratio_to_requested_viscosity"] - 0.5) <= 0.03 and negative["relative_viscosity_error"] > 0.02
    return {"status": "passed" if numerical_pass and conservation_pass and negative_pass else "failed",
            "problem": "Periodic linear advective shear-wave decay; cell-average initialization",
            "coarse_level": asdict(base), "fine_level": asdict(fine_level),
            "physical_viscosity": nu, "amplitude": amplitude, "advective_velocity": advective_velocity,
            "physical_domain": [24, 4, 4], "duration_common_time_units": total_time,
            "initial_lattice_mach_max_approximately": math.sqrt(3) * math.hypot(advective_velocity, amplitude),
            "collision": "lbmpy 1.4 D3Q27 cumulant, pre-collision collide_stream, no forces, no SGS",
            "checks": {"analytic_decay_and_phase": numerical_pass, "conservation": conservation_pass,
                       "negative_control_detected": negative_pass},
            "results": results, "remaps": remaps, "trajectories": trajectories,
            "limitation": "Whole periodic levels change at synchronized times. No simultaneous spatial interface is exercised."}


def write_checks(output, *, plot=True, progress=print):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new/empty refinement-check directory")
    output.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    report = {"status": "running", "scope": "Experimental conservative remapping milestone, not a refined airfoil solver",
              "versions": {p: version(p) for p in ("aerokinetic", "lbmpy", "pystencils", "numpy", "sympy")},
              "platform": platform.platform(), "python": platform.python_version(), "backend": "cpu_float64",
              "source_sha256": {p: file_hash(Path(__file__).with_name(p))
                                for p in ("refinement.py", "refinement_checks.py")},
              "spatial_interface": "not_run", "walls_loads_checkpoints": "not_run",
              "wind_tunnel_validation": "not_run", "windows_msvc": "not_run", "gpu": "not_run"}
    write_json(output / "checks.json", report)
    try:
        report["smooth_cell_average_prediction"] = periodic_prediction_study()
        report["advected_shear_wave"] = advected_shear_wave(progress)
        report["status"] = report["advected_shear_wave"]["status"]
        if plot:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            fig = Figure(figsize=(9, 6), constrained_layout=True)
            FigureCanvasAgg(fig)
            axes = fig.subplots(2, 1)
            for label, records in report["advected_shear_wave"]["trajectories"].items():
                axes[0].plot([r["time"] for r in records], [r["amplitude"] for r in records], label=label)
                axes[1].plot([r["time"] for r in records], [r["relative_complex_error"] for r in records], label=label)
            records = report["advected_shear_wave"]["trajectories"]["uniform_coarse"]
            axes[0].plot([r["time"] for r in records],
                         [math.hypot(r["expected_real"], r["expected_imag"]) for r in records], "k--", label="analytic")
            axes[0].set_ylabel("normalized shear-wave amplitude")
            axes[0].legend(fontsize="small")
            axes[1].set_ylabel("relative complex harmonic error")
            axes[1].set_xlabel("time in common coarse-grid units")
            for ax in axes:
                ax.grid(True, alpha=0.25)
            fig.savefig(output / "shear_wave.png", dpi=140)
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - start
        write_json(output / "checks.json", report)
    progress(f"Refinement remapping checks: {report['status']}; {output / 'checks.json'}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify the experimental remap stage; not a refined airfoil calculation")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args(argv)
    report = write_checks(args.output, plot=not args.no_plot)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
