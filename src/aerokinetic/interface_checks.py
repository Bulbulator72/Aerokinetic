"""Reproducible verification of actual periodic coarse/fine spatial interfaces.

References are analytic smooth solutions and our own uniform-grid kernels,
never another CFD product. No airfoil/tunnel validation is implied.
"""
import argparse
from dataclasses import asdict
from importlib.metadata import version
import math
from pathlib import Path
import platform
import time

import numpy as np

from ._io import file_hash, write_json
from .interface import FineBox, PeriodicRefinedBox, _make_lattice, _read_pdf, _write_pdf
from .refinement import AcousticLevel


LENGTH = 16.0
NU = 0.03
ZETA = 0.02
AMPLITUDE = 1e-4


def make_solver(n, *, reflux=True, progress=None, box_factory=PeriodicRefinedBox):
    dx = LENGTH / n
    level = AcousticLevel(dx, dx, 0.5 + 3 * NU / dx, 0.5 + 4.5 * ZETA / dx)
    lower = tuple(round(6 / dx) for _ in range(3))
    upper = tuple(round(10 / dx) for _ in range(3))
    if any(abs(v * dx - 6) > 1e-12 for v in lower) or any(abs(v * dx - 10) > 1e-12 for v in upper):
        raise ValueError("This verification box needs grid-aligned [6,10] bounds")
    return box_factory((n, n, n), FineBox(lower, upper), level,
                              memory_limit_gib=1.0, reflux=reflux, progress=progress)


def _balance(solver, initial):
    current = solver.conserved_integrals()
    return {"relative_mass_drift": abs(current["mass"] / initial["mass"] - 1),
            "momentum_drift_per_volume": ((np.array(current["momentum"]) - initial["momentum"]) / LENGTH**3).tolist()}


def _run(solver, duration, observer, *, sample_interval=4.0):
    count = round(duration / solver.coarse_level.dt)
    if abs(count * solver.coarse_level.dt - duration) > 1e-12:
        raise ValueError("Verification duration must end on a synchronized coarse time")
    stride = max(1, round(sample_interval / solver.coarse_level.dt))
    initial = solver.conserved_integrals()
    history = [dict(time=solver.time, **observer(solver))]
    worst_mass, worst_momentum = 0., 0.
    max_reflux = 0.
    for step in range(1, count + 1):
        solver.step()
        max_reflux = max(max_reflux, float(np.max(abs(solver.last_reflux_moments))))
        if step % stride == 0 or step == count:
            balance = _balance(solver, initial)
            worst_mass = max(worst_mass, balance["relative_mass_drift"])
            worst_momentum = max(worst_momentum, max(map(abs, balance["momentum_drift_per_volume"])))
            history.append(dict(time=solver.time, **observer(solver), **balance))
    return {"history": history, "max_sampled_relative_mass_drift": worst_mass,
            "max_sampled_momentum_drift_per_volume": worst_momentum,
            "max_absolute_local_reflux_density_or_momentum": max_reflux,
            "coarse_steps": solver.steps, "fine_steps": solver.fine_steps,
            "coarse_level": asdict(solver.coarse_level), "fine_level": asdict(solver.fine_level),
            "coarse_shape": solver.shape, "fine_owned_shape": solver.fine_shape,
            "fine_allocated_shape_with_donors": solver.padded_shape,
            "allocated_lattice_cells": math.prod(solver.shape) + math.prod(solver.padded_shape),
            "uniform_finest_lattice_cells_for_same_domain": (2 * solver.shape[0])**3,
            "estimated_memory_gib": solver.estimated_memory_gib,
            "conservation_passed": worst_mass <= 5e-11 and worst_momentum <= 2e-12,
            "subcycling_passed": solver.fine_steps == 2 * solver.steps}


def shear_wave(n, progress=print, *, box_factory=PeriodicRefinedBox):
    progress(f"Oblique shear wave, coarse {n}^3 and finite refined box")
    solver = make_solver(n,box_factory=box_factory)
    k = 2 * np.pi / LENGTH * np.array([1, 1, 1])
    polarization = np.array([1., -1., 0.]) / math.sqrt(2)
    velocity = np.array([0.04, 0.02, 0.03])

    def initial(x, y, z, dx):
        phase = k[0] * x + k[1] * y + k[2] * z
        cell_filter = np.prod(np.sinc(k * dx / (2 * np.pi)))
        return 1., velocity + AMPLITUDE * cell_filter * np.sin(phase)[..., None] * polarization

    def observe(state):
        squared_error, squared_reference = 0., 0.
        gram, rhs = np.zeros((2, 2)), np.zeros(2)
        for fields, (x, y, z), owned, dx in state.leaf_moments():
            phase = k[0] * x + k[1] * y + k[2] * z
            cell_filter = np.prod(np.sinc(k * dx / (2 * np.pi)))
            s, c = cell_filter * np.sin(phase), cell_filter * np.cos(phase)
            perturbation = fields[..., 1:4] / fields[..., 0, None] - velocity
            expected = (AMPLITUDE * math.exp(-NU * np.dot(k, k) * state.time)
                        * np.sin(phase - np.dot(k, velocity) * state.time)[..., None] * cell_filter * polarization)
            squared_error += np.sum((perturbation - expected)[owned]**2) * dx**3
            squared_reference += np.sum(expected[owned]**2) * dx**3
            scalar = perturbation @ polarization
            basis = np.column_stack((s[owned], c[owned]))
            gram += (basis.T @ basis) * dx**3
            rhs += (basis.T @ scalar[owned]) * dx**3
        sine, cosine = np.linalg.solve(gram, rhs)
        return {"relative_velocity_l2_error": float(math.sqrt(squared_error / squared_reference)),
                "fitted_relative_amplitude": float(math.hypot(sine, cosine) / AMPLITUDE),
                "fitted_sine_coefficient": float(sine), "fitted_cosine_coefficient": float(cosine)}

    solver.initialize(initial)
    result = _run(solver, 40., observe)
    tail = [r for r in result["history"] if r["time"] >= 12]
    slope = np.polyfit([r["time"] for r in tail], np.log([r["fitted_relative_amplitude"] for r in tail]), 1)[0]
    measured = float(-slope / np.dot(k, k))
    result.update(n=n, fitted_physical_viscosity=measured, relative_viscosity_error=abs(measured / NU - 1),
                  final_relative_velocity_l2_error=result["history"][-1]["relative_velocity_l2_error"])
    progress(f"  velocity error {result['final_relative_velocity_l2_error']:.3%}; viscosity error {result['relative_viscosity_error']:.3%}")
    return result


def gaussian_average_and_derivative(coordinate, center, sigma, dx):
    """Exact cell averages of a periodic Gaussian and its derivative.

    Three nearest images in each 1D factor suffice for the stated widths and
    domain; the omitted-image contribution is negligible at the fixed tolerance.
    math.erf is evaluated on sparse one-coordinate arrays, without SciPy.
    """
    erf = np.vectorize(math.erf, otypes=[float])
    average = np.zeros_like(coordinate, dtype=float)
    derivative = np.zeros_like(coordinate, dtype=float)
    center %= LENGTH
    for image in (-1, 0, 1):
        displacement = coordinate - center - image * LENGTH
        lower, upper = displacement - dx / 2, displacement + dx / 2
        average += sigma * math.sqrt(np.pi / 2) / dx * (erf(upper / (math.sqrt(2) * sigma))
                                                       - erf(lower / (math.sqrt(2) * sigma)))
        derivative += (np.exp(-upper**2 / (2 * sigma**2)) - np.exp(-lower**2 / (2 * sigma**2))) / dx
    return average, derivative


def vortex_field(x, y, z, dx, t, center, velocity, sigma0=1.6):
    """Curl of a diffusing 3D periodic Gaussian vector potential along z."""
    sigma = math.sqrt(sigma0**2 + 2 * NU * t)
    center = np.asarray(center) + np.asarray(velocity) * t
    gx, dgx = gaussian_average_and_derivative(x, center[0], sigma, dx)
    gy, dgy = gaussian_average_and_derivative(y, center[1], sigma, dx)
    gz, _ = gaussian_average_and_derivative(z, center[2], sigma, dx)
    factor = AMPLITUDE * sigma0 * (sigma0 / sigma)**3
    ux, uy, uz = np.broadcast_arrays(factor * gx * dgy * gz,
                                     -factor * dgx * gy * gz, np.zeros_like(x + y + z))
    return np.stack((ux, uy, uz), axis=-1)


def gaussian_vortex(n, topology="corner", progress=print, frame_directory=None, *, box_factory=PeriodicRefinedBox):
    settings = {"face": ((4., 8., 8.), (0.06, 0., 0.)),
                "edge": ((4., 4., 8.), (0.05, 0.05, 0.)),
                "corner": ((4., 4., 4.), (0.05, 0.05, 0.05))}
    center, velocity = settings[topology]
    velocity = np.array(velocity)
    progress(f"3D Gaussian vortex through {topology}, coarse {n}^3")
    solver = make_solver(n,box_factory=box_factory)
    if frame_directory is not None:
        frame_directory = Path(frame_directory)
        frame_directory.mkdir(parents=True, exist_ok=False)

    def initial(x, y, z, dx):
        return 1., velocity + vortex_field(x, y, z, dx, 0, center, velocity)

    def observe(state):
        error, norm = 0., 0.
        for fields, coordinates, owned, dx in state.leaf_moments():
            actual = fields[..., 1:4] / fields[..., 0, None] - velocity
            exact = vortex_field(*coordinates, dx, state.time, center, velocity)
            error += np.sum((actual - exact)[owned]**2) * dx**3
            norm += np.sum(exact[owned]**2) * dx**3
        result = {"relative_velocity_l2_error": float(math.sqrt(error / norm))}
        if frame_directory is not None:
            from .interface_visualization import save_slice
            path = frame_directory / f"frame_{state.steps:09d}.npz"
            save_slice(state, path, z_plane=8., background_velocity_lattice=velocity)
            result["slice_file"] = path.name
        return result

    solver.initialize(initial)
    result = _run(solver, 80., observe)
    result.update(n=n, topology=topology, initial_center=center, background_velocity=velocity.tolist(),
                  final_relative_velocity_l2_error=result["history"][-1]["relative_velocity_l2_error"],
                  reference="Linear periodic advection-diffusion of a 3D Gaussian vector potential; finite-amplitude and compressibility effects remain")
    progress(f"  velocity error {result['final_relative_velocity_l2_error']:.3%}")
    return result


def acoustic_packet(direction, progress=print, *, box_factory=PeriodicRefinedBox):
    progress(f"Acoustic packet through finite 3D box, direction {direction:+d}")
    solver = make_solver(32,box_factory=box_factory)
    cs, amplitude = math.sqrt(1 / 3), 1e-5
    center = 3. if direction > 0 else 13.

    def initial(x, y, z, dx):
        g, _ = gaussian_average_and_derivative(x, center, 1.2, dx)
        perturbation = amplitude * g
        shape = np.broadcast_shapes(x.shape, y.shape, z.shape)
        j = np.zeros((*shape, 3))
        j[..., 0] = direction * cs * perturbation
        return 1 + perturbation, j

    solver.initialize(initial)
    initial_coarse = solver.transfer.moments(solver.states()[0])
    normalizer = initial_coarse[..., 1] + direction * cs * (initial_coarse[..., 0] - 1)
    run = _run(solver, 12., lambda _: {})
    actual = solver.transfer.moments(solver.states()[0])

    references = {}
    for name, n, level in (("uniform_coarse", 32, solver.coarse_level),
                            ("uniform_fine", 64, solver.fine_level)):
        # Exact transverse invariance of this 1D state permits a periodic 4x4
        # transverse representation of the reference. The AMR test is fully 3D.
        lbm = _make_lattice((n, 4, 4), level)
        xyz = solver.coordinates((n, 4, 4), level.dx)
        rho, j = initial(*xyz, level.dx)
        fields = np.zeros((n, 4, 4, 10))
        fields[..., 0], fields[..., 1:4] = rho, j
        _write_pdf(lbm, solver.transfer.populations(fields))
        lbm.run(round(12 / level.dt))
        moments = solver.transfer.moments(_read_pdf(lbm))[..., :4].mean(axis=(1, 2))
        if n == 64:
            moments = moments.reshape(32, 2, 4).mean(axis=1)
        references[name] = moments

    x, y, z = solver.coordinates(solver.shape, solver.coarse_level.dx)
    footprint = np.broadcast_to((y >= 6) & (y < 10) & (z >= 6) & (z < 10), solver.shape)
    upstream = np.broadcast_to(x < 5 if direction > 0 else x > 11, solver.shape) & footprint
    denominator = float(np.sum(normalizer[footprint]**2))
    backward = actual[..., 1] - direction * cs * (actual[..., 0] - 1)
    reflection = {}
    for label, reference in references.items():
        expected = reference[:, 1] - direction * cs * (reference[:, 0] - 1)
        difference = backward - expected[:, None, None]
        reflection[label] = float(math.sqrt(np.sum(difference[upstream]**2) / denominator))
    run.update(direction=direction, reflected_characteristic_excess_vs_uniform_coarse=reflection["uniform_coarse"],
               backward_characteristic_difference_vs_uniform_fine=reflection["uniform_fine"],
               reference_transverse_invariance="Uniform reference uses periodic 4x4 transverse cells for a one-dimensional state",
               amplitude_density=amplitude, sigma=1.2)
    progress(f"  excess upstream backward characteristic {reflection['uniform_coarse']:.3%}")
    return run


def negative_control(progress=print, *, box_factory=PeriodicRefinedBox):
    progress("Deliberately disabling reflux: the balance acceptance test must fail")
    solver = make_solver(16, reflux=False,box_factory=box_factory)

    def initial(x, y, z, dx):
        rho = 1 + 1e-3 * np.exp(-((x - 6.3)**2 + (y - 7.1)**2 + (z - 8.2)**2) / 2)
        return rho, rho[..., None] * np.array([0.03, 0.02, 0.01])

    solver.initialize(initial)
    result = _run(solver, 8., lambda _: {}, sample_interval=1)
    result["expected_balance_violation_detected"] = not result["conservation_passed"]
    return result


def write_checks(output, *, plot=True, progress=print, box_factory=PeriodicRefinedBox):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new/empty interface-check directory")
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "scope": "Periodic fluid-only spatial coarse/fine interfaces; no airfoil walls or loads",
              "versions": {p: version(p) for p in ("aerokinetic", "lbmpy", "pystencils", "numpy", "sympy")},
              "python": platform.python_version(), "platform": platform.platform(), "backend": "cpu_float64",
              "source_sha256": {p: file_hash(Path(__file__).with_name(p))
                                for p in ("refinement.py", "interface.py", "interface_checks.py", "interface_visualization.py")},
              "nu_physical_common_units": NU, "zeta_physical_common_units": ZETA,
              "airfoil_validation": "not_run", "windows_msvc": "not_run", "gpu": "not_run"}
    start = time.perf_counter()
    report['box_factory']=box_factory.__name__
    if box_factory is not PeriodicRefinedBox:
        report['source_sha256']['parallel.py']=file_hash(Path(__file__).with_name('parallel.py'))
    write_json(output / "checks.json", report)
    try:
        report["shear_wave"] = []
        for n in (16, 24, 32):
            report["shear_wave"].append(shear_wave(n, progress,box_factory=box_factory))
            write_json(output / "checks.json", report)
        report["vortex_corner"] = []
        for n in (16, 24, 32):
            report["vortex_corner"].append(gaussian_vortex(n, "corner", progress,
                frame_directory=output / "vortex_frames" if n == 32 and plot else None,box_factory=box_factory))
            write_json(output / "checks.json", report)
        report["vortex_face_edge"] = []
        for topology in ("face", "edge"):
            report["vortex_face_edge"].append(gaussian_vortex(32, topology, progress,box_factory=box_factory))
            write_json(output / "checks.json", report)
        report["acoustic"] = []
        for direction in (1, -1):
            report["acoustic"].append(acoustic_packet(direction, progress,box_factory=box_factory))
            write_json(output / "checks.json", report)
        report["negative_control"] = negative_control(progress,box_factory=box_factory)
        positive = (report["shear_wave"] + report["vortex_corner"]
                    + report["vortex_face_edge"] + report["acoustic"])
        checks = {
            "composite_conservation": all(r["conservation_passed"] for r in positive),
            "integer_subcycling": all(r["subcycling_passed"] for r in positive),
            "finest_oblique_wave_error": report["shear_wave"][-1]["final_relative_velocity_l2_error"] <= 0.05,
            "finest_oblique_wave_viscosity": report["shear_wave"][-1]["relative_viscosity_error"] <= 0.05,
            "oblique_wave_error_reduced_with_resolution": report["shear_wave"][-1]["final_relative_velocity_l2_error"] < report["shear_wave"][0]["final_relative_velocity_l2_error"],
            "finest_vortex_errors": all(r["final_relative_velocity_l2_error"] <= 0.05 for r in [report["vortex_corner"][-1], *report["vortex_face_edge"]]),
            "corner_vortex_error_reduced_with_resolution": report["vortex_corner"][-1]["final_relative_velocity_l2_error"] < report["vortex_corner"][0]["final_relative_velocity_l2_error"],
            "acoustic_excess_reflection": all(r["reflected_characteristic_excess_vs_uniform_coarse"] <= 0.03 for r in report["acoustic"]),
            "disabled_reflux_detected": report["negative_control"]["expected_balance_violation_detected"],
        }
        report["acceptance"] = checks
        report["status"] = "passed" if all(checks.values()) else "failed"
        if plot:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            fig = Figure(figsize=(10, 6), constrained_layout=True)
            FigureCanvasAgg(fig)
            wave_ax, vortex_ax = fig.subplots(1, 2)
            for case in report["shear_wave"]:
                wave_ax.plot([r["time"] for r in case["history"]],
                              [100 * r["relative_velocity_l2_error"] for r in case["history"]], label=f"N={case['n']}")
            for case in report["vortex_corner"]:
                vortex_ax.plot([r["time"] for r in case["history"]],
                                [100 * r["relative_velocity_l2_error"] for r in case["history"]], label=f"N={case['n']}")
            wave_ax.set_title("Oblique shear wave through a finite refined box")
            vortex_ax.set_title("3D vortex entering through a corner")
            for ax in (wave_ax, vortex_ax):
                ax.set_xlabel("time in common units")
                ax.set_ylabel("relative velocity L2 error, %")
                ax.grid(True, alpha=0.25)
                ax.legend()
            fig.savefig(output / "interface_errors.png", dpi=140)
            from .interface_visualization import animate_slices
            report["field_exports"] = animate_slices(output / "vortex_frames", output / "verification_vortex.gif",
                                                      speed_scale=AMPLITUDE)
            from PIL import Image
            with Image.open(output / "verification_vortex.gif") as animation:
                actual_frames = animation.n_frames
            exports = report["field_exports"]
            exports["gif_frames_read_back"] = actual_frames
            checks["field_exports"] = (exports["frame_count"] == actual_frames == 21
                                       and exports["times"][0] == 0 and exports["times"][-1] == 80)
            report["status"] = "passed" if all(checks.values()) else "failed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - start
        try:
            if platform.system() == "Linux":
                import resource
                report["process_peak_rss_gib_linux"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
            else:
                report["process_peak_rss"] = "not_measured"
        except ImportError:
            report["process_peak_rss"] = "not_measured"
        write_json(output / "checks.json", report)
    progress(f"Spatial interface checks: {report['status']}; {output / 'checks.json'}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify genuine periodic fluid-only 2:1 spatial interfaces")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args(argv)
    result = write_checks(args.output, plot=not args.no_plot)
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
