"""Independent moment, polynomial and conservation checks for the remap stage.

These do not pretend to test coarse/fine interface streaming or airfoil loads.
"""
from itertools import product

import numpy as np
import pytest

from aerokinetic.refinement import (
    AcousticLevel, D3Q27Transfer, prolong_cell_averages, restrict_cell_averages,
)


STENCIL = np.array(list(product((-1, 0, 1), repeat=3)))
WEIGHTS = np.prod(np.where(STENCIL == 0, 2 / 3, 1 / 6), axis=1)


def transfer(zero_centered=True, tau=0.59):
    return D3Q27Transfer(STENCIL, AcousticLevel(1, 1, tau, 0.83),
                         zero_centered=zero_centered, pdf_stage="pre_collision")


def raw_moments(pdf, zero_centered=True, stencil=STENCIL):
    """Brute-force physical-population sums, independent of module extraction."""
    weights = np.prod(np.where(stencil == 0, 2 / 3, 1 / 6), axis=1)
    physical = pdf + weights if zero_centered else pdf
    rho = np.sum(physical, axis=-1)
    j = sum(physical[..., q, None] * c for q, c in enumerate(stencil))
    second = sum(physical[..., q, None, None] * np.outer(c, c) for q, c in enumerate(stencil))
    stress = second - j[..., :, None] * j[..., None, :] / rho[..., None, None]
    stress -= rho[..., None, None] * np.eye(3) / 3
    return rho, j, stress


def linear_pdf(rho, j, zero_centered=True):
    """Known density/momentum from the first-order Hermite polynomial."""
    pdf = WEIGHTS * (np.asarray(rho)[..., None] + 3 * np.asarray(j) @ STENCIL.T)
    return pdf - WEIGHTS if zero_centered else pdf


def halo(pdf):
    # Only the fixture is periodic. Production donors need real halo exchange.
    return np.pad(pdf, ((2, 2), (2, 2), (2, 2), (0, 0)), mode="wrap")


def cell_power(centers, width, degree):
    centers = np.asarray(centers)
    return ((centers + width / 2)**(degree + 1) - (centers - width / 2)**(degree + 1)) / (width * (degree + 1))


@pytest.mark.parametrize("tau", [0.50001, 0.59, 1.0, 1.7])
def test_acoustic_scaling_preserves_physical_shear_and_bulk_viscosity(tau):
    coarse = AcousticLevel(0.03125, 0.001, tau, 0.83)
    level = coarse
    for depth in range(1, 5):
        level = level.refined()
        assert level.dx == coarse.dx / 2**depth
        assert level.dt == coarse.dt / 2**depth
        assert level.lattice_velocity_unit == coarse.lattice_velocity_unit
        assert level.shear_viscosity == pytest.approx(coarse.shear_viscosity, rel=2e-12)
        assert level.bulk_viscosity == pytest.approx(coarse.bulk_viscosity, rel=2e-12)


@pytest.mark.parametrize("zero_centered", [False, True])
@pytest.mark.parametrize("tau", [0.50001, 0.59, 1.0, 1.7])
def test_stress_rescaling_keeps_strain_and_conserved_moments(zero_centered, tau):
    coupling = transfer(zero_centered, tau)
    pdf = linear_pdf(np.array(1.01), np.array([0.02, -0.01, 0.005]), zero_centered)
    # Symmetric opposite beams add anisotropic stress, with zero mass and momentum.
    for axis, amount in enumerate((2e-4, -1e-4, 3e-4)):
        for sign in (-1, 1):
            c = np.zeros(3, dtype=int)
            c[axis] = sign
            pdf[np.flatnonzero((STENCIL == c).all(axis=1))[0]] += amount
        pdf[np.flatnonzero((STENCIL == 0).all(axis=1))[0]] -= 2 * amount
    rho, j, pi = raw_moments(pdf, zero_centered)
    fine = coupling.rescale(pdf, to_fine=True)
    rho_f, j_f, pi_f = raw_moments(fine, zero_centered)
    np.testing.assert_allclose(rho_f, rho, atol=2e-13, rtol=0)
    np.testing.assert_allclose(j_f, j, atol=2e-13, rtol=0)
    iso, iso_f = np.trace(pi) / 3, np.trace(pi_f) / 3
    np.testing.assert_allclose((pi_f - np.eye(3) * iso_f) / (coupling.fine.tau_shear * coupling.fine.dt),
                               (pi - np.eye(3) * iso) / (coupling.coarse.tau_shear * coupling.coarse.dt),
                               atol=2e-13, rtol=0)
    assert iso_f / (coupling.fine.tau_bulk * coupling.fine.dt) == pytest.approx(
        iso / (coupling.coarse.tau_bulk * coupling.coarse.dt), abs=2e-13)
    restored = coupling.rescale(fine, to_fine=False)
    for actual, expected in zip(raw_moments(restored, zero_centered), (rho, j, pi)):
        np.testing.assert_allclose(actual, expected, atol=2e-13, rtol=0)


def test_five_point_prediction_reproduces_tensor_degree_four_cell_averages():
    # Cell averages, not values at cell centres. Include cross terms and unequal
    # counts along all three axes to catch interleaving/order mistakes.
    axes = [np.arange(-2, n + 2) / 8 for n in (5, 4, 3)]
    coarse = (cell_power(axes[0], 1 / 8, 4)[:, None, None]
              * cell_power(axes[1], 1 / 8, 3)[None, :, None]
              * cell_power(axes[2], 1 / 8, 2)[None, None, :])
    fine_axes = [(np.arange(2 * n) / 2 - 0.25) / 8 for n in (5, 4, 3)]
    exact = (cell_power(fine_axes[0], 1 / 16, 4)[:, None, None]
             * cell_power(fine_axes[1], 1 / 16, 3)[None, :, None]
             * cell_power(fine_axes[2], 1 / 16, 2)[None, None, :])
    fine = prolong_cell_averages(coarse)
    np.testing.assert_allclose(fine, exact, atol=2e-12, rtol=0)
    np.testing.assert_allclose(restrict_cell_averages(fine), coarse[2:-2, 2:-2, 2:-2], atol=2e-13, rtol=0)


@pytest.mark.parametrize("zero_centered", [False, True])
def test_each_parent_mass_and_momentum_preserved_through_local_fluid_remap(zero_centered):
    rng = np.random.default_rng(54)
    rho = 1 + rng.normal(0, 0.001, (5, 4, 3))
    j = rng.normal(0, 0.003, (5, 4, 3, 3))
    pdf = linear_pdf(rho, j, zero_centered)
    original = pdf.copy()
    coupling = transfer(zero_centered)
    fine = coupling.prolong(halo(pdf))
    rho_f, j_f, _ = raw_moments(fine, zero_centered)
    for index in np.ndindex(rho.shape):
        siblings = tuple(slice(2 * v, 2 * v + 2) for v in index)
        assert rho_f[siblings].mean() == pytest.approx(rho[index], abs=2e-13)
        np.testing.assert_allclose(j_f[siblings].mean(axis=(0, 1, 2)), j[index], atol=2e-13, rtol=0)
    restored = coupling.restrict(fine)
    rho_r, j_r, _ = raw_moments(restored, zero_centered)
    np.testing.assert_allclose(rho_r, rho, atol=2e-13, rtol=0)
    np.testing.assert_allclose(j_r, j, atol=2e-13, rtol=0)
    np.testing.assert_array_equal(pdf, original)


def test_time_interpolation_conserves_density_and_momentum_when_density_changes():
    # Averaging velocities here would produce the wrong interpolated momentum.
    shape = (3, 2, 4)
    a = linear_pdf(np.full(shape, 0.99), np.broadcast_to([0.02, 0.01, 0.0], (*shape, 3)))
    b = linear_pdf(np.full(shape, 1.01), np.broadcast_to([0.06, -0.02, 0.01], (*shape, 3)))
    coupling = transfer()
    fine = coupling.prolong_in_time(halo(a), halo(b), 0.5)
    rho, j, _ = raw_moments(fine)
    np.testing.assert_allclose(rho, 1, atol=2e-13, rtol=0)
    np.testing.assert_allclose(j, np.broadcast_to([0.04, -0.005, 0.005], j.shape), atol=2e-13, rtol=0)


@pytest.mark.parametrize("zero_centered", [False, True])
def test_100_synchronized_uniform_roundtrips_do_not_accumulate_drift(zero_centered):
    coupling = transfer(zero_centered)
    pdf = linear_pdf(np.full((2, 2, 2), 1.001), np.broadcast_to([0.03, -0.01, 0.005], (2, 2, 2, 3)), zero_centered)
    reference = raw_moments(pdf, zero_centered)
    for _ in range(100):
        pdf = coupling.restrict(coupling.prolong(halo(pdf)))
    for actual, expected in zip(raw_moments(pdf, zero_centered), reference):
        np.testing.assert_allclose(actual, expected, atol=2e-12, rtol=0)


def test_population_order_is_not_assumed_to_equal_a_library_specific_order():
    rng = np.random.default_rng(17)
    permutation = rng.permutation(27)
    a = transfer()
    b = D3Q27Transfer(STENCIL[permutation], a.coarse, zero_centered=True, pdf_stage="pre_collision")
    pdf = linear_pdf(1.0, [0.03, -0.02, 0.01])
    np.testing.assert_allclose(b.rescale(pdf[permutation], to_fine=True),
                               a.rescale(pdf, to_fine=True)[permutation], atol=2e-13, rtol=0)


@pytest.mark.parametrize("stage", ["post_collision", "stream_collide", "", None])
def test_production_postcollision_arrays_cannot_be_declared_supported(stage):
    with pytest.raises(ValueError, match="pre_collision"):
        D3Q27Transfer(STENCIL, AcousticLevel(1, 1, 0.59, 0.83), zero_centered=True, pdf_stage=stage)


def test_invalid_donors_and_unphysical_prediction_fail_instead_of_clipping():
    coupling = transfer()
    with pytest.raises(ValueError, match="exactly once"):
        D3Q27Transfer(np.zeros((27, 3)), coupling.coarse, zero_centered=True, pdf_stage="pre_collision")
    with pytest.raises(ValueError, match="halo"):
        prolong_cell_averages(np.ones((4, 6, 6)))
    with pytest.raises(ValueError, match="even"):
        restrict_cell_averages(np.ones((3, 4, 4)))
    with pytest.raises(ValueError, match="finite"):
        coupling.moments(np.full(27, np.nan))
    with pytest.raises(ValueError, match="nonpositive density"):
        coupling.moments(-WEIGHTS)
    with pytest.raises(ValueError, match="Mach"):
        coupling.rescale(linear_pdf(1, [0.3, 0, 0]), to_fine=True)
    moments = coupling.moments(np.zeros(27))
    moments[4] = -1  # far outside admissible stress, cannot silently clip PDFs
    with pytest.raises(ValueError, match="negative physical populations"):
        coupling.populations(moments)
    for invalid in (-0.1, 1.1, np.nan):
        with pytest.raises(ValueError, match="Time fraction"):
            coupling.prolong_in_time(np.zeros((5, 5, 5, 27)), np.zeros((5, 5, 5, 27)), invalid)


def test_positive_density_does_not_hide_a_negative_donor_population():
    pdf = np.zeros(27)
    pdf[np.flatnonzero((STENCIL == 0).all(axis=1))[0]] = -0.31
    with pytest.raises(ValueError, match="Donor has negative"):
        transfer().moments(pdf)


def test_unlimited_prediction_rejects_density_overshoot_without_changing_donors():
    # Two adjacent low-density cells beside a sharp change are outside the
    # smooth-donor contract. Every donor is positive; the high-order prediction
    # itself must be detected, rather than clipped to a positive floor.
    rho = np.ones((7, 7, 7))
    rho[2:4, ...] = 1e-8
    pdf = linear_pdf(rho, np.zeros((7, 7, 7, 3)))
    original = pdf.copy()
    with pytest.raises(ValueError, match="nonpositive density"):
        transfer().prolong(pdf)
    np.testing.assert_array_equal(pdf, original)


@pytest.mark.integration
def test_tensor_product_equilibrium_matches_the_pinned_cumulant_setter():
    from lbmpy import LBMConfig, Method, Stencil
    from lbmpy.lbstep import LatticeBoltzmannStep
    from lbmpy.stencils import LBStencil
    from pystencils import CreateKernelConfig

    for zero_centered in (False, True):
        lbm = LatticeBoltzmannStep(domain_size=(4, 4, 4), periodicity=True,
            time_step_order="collide_stream",
            lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27), method=Method.CUMULANT,
                                 relaxation_rate=1.6, compressible=True, zero_centered=zero_centered),
            config=CreateKernelConfig(data_type="float64"))
        rho, velocity = 1.003, np.array([0.045, -0.025, 0.015])
        lbm.data_handling.fill(lbm.density_data_name, rho, ghost_layers=True)
        for block in lbm.data_handling.iterate(ghost_layers=True):
            block[lbm.velocity_data_name][...] = velocity
        lbm.set_pdf_fields_from_macroscopic_values()
        upstream = lbm.data_handling.gather_array(lbm.pdf_array_name)
        coupling = D3Q27Transfer(lbm.method.stencil, AcousticLevel(1, 1, 0.625, 0.83),
                                 zero_centered=zero_centered, pdf_stage="pre_collision")
        exact_moments = np.zeros(10)
        exact_moments[0], exact_moments[1:4] = rho, rho * velocity
        ours = coupling.populations(exact_moments)
        np.testing.assert_allclose(upstream, np.broadcast_to(ours, upstream.shape), atol=3e-14, rtol=0)
