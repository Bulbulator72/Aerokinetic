import numpy as np
import pytest
from aerokinetic import Section, naca0012, Flow, Grid, Solver
from aerokinetic.solver import coefficients


@pytest.mark.integration
def test_cumulant_shear_wave_viscosity_and_conservation():
    import sympy as sp
    from lbmpy import LBMConfig, Method, Stencil
    from lbmpy.stencils import LBStencil
    from lbmpy.lbstep import LatticeBoltzmannStep
    from pystencils import CreateKernelConfig
    nu, amplitude, steps, length = 0.03, 1e-4, 120, 32
    lbm = LatticeBoltzmannStep(domain_size=(length, 4, 4), periodicity=True,
        lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27), method=Method.CUMULANT,
                             compressible=True, zero_centered=True, relaxation_rate=sp.Symbol("omega")),
        kernel_params={"omega": 1 / (0.5 + 3 * nu)}, config=CreateKernelConfig(data_type="float64"))
    for block in lbm.data_handling.iterate(ghost_layers=True):
        x, _, _ = block.midpoint_arrays
        block[lbm.velocity_data_name][..., 1] = amplitude * np.sin(2 * np.pi * x / length)
    lbm.set_pdf_fields_from_macroscopic_values()
    lbm.run(steps)
    velocity = np.asarray(lbm.velocity[:, :, :])
    rho = np.asarray(lbm.density[:, :, :])
    harmonic = abs(np.fft.fft(velocity[:, :, :, 1].mean(axis=(1, 2)))[1]) * 2 / length
    assert harmonic / amplitude == pytest.approx(np.exp(-nu * (2 * np.pi / length)**2 * steps), rel=0.012)
    np.testing.assert_allclose(rho, 1.0, atol=2e-12)
    np.testing.assert_allclose(velocity.mean(axis=(0, 1, 2)), 0.0, atol=1e-13)


@pytest.fixture(scope="module")
def section_solver():
    return Solver(Section((naca0012(31),)), Flow(perturbation=0),
                  Grid(cells_per_chord=16, upstream_chords=1, downstream_chords=2,
                       transverse_chords=1, memory_limit_gib=1))


@pytest.mark.integration
def test_links_match_exact_geometry_and_closed_surface_pressure_balance(section_solver):
    solver = section_solver
    step = solver._stepper
    step.boundary_handling.prepare()
    for block in step.data_handling.iterate(ghost_layers=1):
        lists = block[step.boundary_handling._index_array_name].boundary_object_to_index_list
        ind = lists[solver._wall_objects[0]]
        dirs = solver._stencil[ind["dir"]]
        points = np.column_stack([ind[k] + block.offset[i] + 0.5 for i, k in enumerate(("x", "y"))])
        points = points * solver.plan.dx + solver.plan.origin
        q = ind["q"]
        assert (q > 0).all()
        wall = points + q[:, None] * dirs[:, :2] * solver.plan.dx
        eps = 1e-6 * dirs[:, :2] * solver.plan.dx
        shape = solver.contours[0]
        assert not shape.contains(*(wall - eps).T).any()
        assert shape.contains(*(wall + eps).T).all()
    # A constant population field has isotropic pressure and exactly zero load.
    # Including nonzero background checks torque cancellation on a closed body.
    from lbmpy.maxwellian_equilibrium import get_weights
    weights = np.array([float(w) for w in get_weights(step.method.stencil)])
    for block in step.data_handling.iterate(ghost_layers=1):
        block[step.pdf_array_name][...] = weights
    loads = solver._loads()
    np.testing.assert_allclose([loads["cl"], loads["cd"], loads["cm"]], 0, atol=2e-12)
    solver._initialize()


@pytest.mark.integration
def test_symmetric_airfoil_startup_drag_and_time_history(section_solver, tmp_path):
    solver = section_solver
    result = solver.run(0.05, tmp_path / "run", sample_every=1, frame_every=5, check_every=1)
    assert result["status"] == "completed"
    assert abs(result["mean"]["cl"]) < 1e-10
    assert abs(result["mean"]["cm"]) < 1e-10
    assert result["mean"]["cd"] > 0
    assert result["experimental_validation"] == "not_performed"
    assert len(list((tmp_path / "run" / "frames").glob("*.npz"))) >= 4
    with np.load(sorted((tmp_path / "run" / "frames").glob("*.npz"))[-1]) as frame:
        assert frame["speed"].ndim == 2
        assert frame["speed"].shape == frame["mask"].shape
        assert np.isfinite(frame["speed"][~frame["mask"]]).all()


def test_force_normalization_and_aerodynamic_moment_sign():
    # A unit upward force applied at x=-1 gives Mz=-1, a positive nose-up moment.
    cl, cd, cm = coefficients([0, 1, 0], -1, 1, 2, 1)
    assert (cl, cd, cm) == pytest.approx((1, 0, 0.5))
