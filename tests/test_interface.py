"""Conservative space/time coupling checks, distinct from single-time remapping."""
from itertools import product

import numpy as np
import pytest

from aerokinetic.interface import FineBox, PeriodicRefinedBox, StreamingFaceFlux
from aerokinetic.refinement import AcousticLevel


STENCIL = np.array(list(product((-1, 0, 1), repeat=3)))


def face_arrays(pdf, stencil):
    shape = pdf.shape[:3]
    result = []
    for axis in range(3):
        flux = np.zeros((*shape, 4))
        ranges = [(0, shape[a]) for a in range(3) if a != axis]
        for index in range(shape[axis]):
            at = [slice(None)] * 4
            at[axis] = index
            flux[tuple(at)] = StreamingFaceFlux(shape, stencil, axis, index, ranges, periodic=True)(pdf)
        result.append(flux)
    return result


def direct_stream(pdf, stencil):
    streamed = np.empty_like(pdf)
    for q, direction in enumerate(stencil):
        streamed[..., q] = np.roll(pdf[..., q], tuple(direction), axis=(0, 1, 2))
    return streamed


def test_symmetric_cartesian_flux_exactly_reproduces_diagonal_pull_streaming():
    rng = np.random.default_rng(541)
    pdf = rng.uniform(-1e-3, 1e-3, (7, 6, 5, 27))
    streamed = direct_stream(pdf, STENCIL)
    actual = np.concatenate(((streamed - pdf).sum(axis=-1, keepdims=True), (streamed - pdf) @ STENCIL), axis=-1)
    flux = face_arrays(pdf, STENCIL)
    divergence = sum(np.roll(f, 1, axis=a) - f for a, f in enumerate(flux))
    np.testing.assert_allclose(actual, divergence, atol=2e-13, rtol=0)


def test_flux_decomposition_is_invariant_to_cartesian_axis_permutations():
    rng = np.random.default_rng(61)
    pdf = rng.uniform(-1e-3, 1e-3, (5, 6, 7, 27))
    original = face_arrays(pdf, STENCIL)
    permutation = (2, 0, 1)
    permuted = face_arrays(pdf.transpose(*permutation, 3), STENCIL[:, permutation])
    for axis, flux in enumerate(permuted):
        expected = original[permutation[axis]].transpose(*permutation, 3)
        expected = expected[..., [0, *(1 + p for p in permutation)]]
        np.testing.assert_allclose(flux, expected, atol=2e-13, rtol=0)


def test_uniform_face_flux_agrees_on_four_children_and_two_substeps_including_box_corners():
    weights = np.prod(np.where(STENCIL == 0, 2 / 3, 1 / 6), axis=1)
    uniform_pdf = 3 * weights * (STENCIL @ np.array([0.04, 0.02, -0.01]))
    coarse_shape, fine_shape = (12, 10, 8), (12, 12, 12)
    coarse = np.broadcast_to(uniform_pdf, (*coarse_shape, 27))
    fine = np.broadcast_to(uniform_pdf, (*fine_shape, 27))
    for axis in range(3):
        for sign in (-1, 1):
            fc = StreamingFaceFlux(coarse_shape, STENCIL, axis, 2 if sign < 0 else 6,
                                    [(3, 7), (2, 6)], periodic=True)(coarse)
            ff = StreamingFaceFlux(fine_shape, STENCIL, axis, 1 if sign < 0 else 9,
                                    [(2, 10), (2, 10)], periodic=False)(fine)
            integrated_fine = 2 * (0.5**3) * ff.reshape(4, 2, 4, 2, 4).sum(axis=(1, 3))
            np.testing.assert_allclose(fc, integrated_fine, atol=2e-13, rtol=0)


def test_invalid_geometry_and_memory_fail_before_any_kernel_allocation():
    level = AcousticLevel(1, 1, 0.59, 0.59)
    with pytest.raises(ValueError, match="integer"):
        FineBox((1.5, 1, 1), (3, 3, 3))
    with pytest.raises(ValueError, match="lower"):
        FineBox((2, 2, 2), (2, 4, 4))
    with pytest.raises(ValueError, match="wholly periodic"):
        PeriodicRefinedBox((12, 12, 12), FineBox((0, 3, 3), (8, 8, 8)), level)
    with pytest.raises(MemoryError, match="Estimated"):
        PeriodicRefinedBox((1000, 1000, 1000), FineBox((300, 300, 300), (700, 700, 700)), level,
                           memory_limit_gib=0.5)


@pytest.fixture(scope="module")
def coupled():
    return PeriodicRefinedBox((12, 12, 12), FineBox((4, 4, 4), (8, 8, 8)),
                              AcousticLevel(1, 1, 0.59, 0.59))


def raw_integrals(solver):
    """Independent sum over true owned populations, with no virtual double count."""
    coarse, fine = solver.states()
    c = solver.transfer.stencil
    weights = np.prod(np.where(c == 0, 2 / 3, 1 / 6), axis=1)
    mass, momentum = 0., np.zeros(3)
    for pdf, owned, level in ((coarse, solver.coarse_owned, solver.coarse_level),
                               (fine, np.ones(fine.shape[:3], bool), solver.fine_level)):
        full = (pdf + weights)[owned]
        mass += full.sum() * level.dx**3
        momentum += (full @ c).sum(axis=0) * level.dx**3 * level.lattice_velocity_unit
    return mass, momentum


@pytest.mark.integration
@pytest.mark.parametrize("velocity", [(0, 0, 0), (0.04, 0.02, -0.01)])
def test_uniform_equilibrium_survives_interfaces_edges_corners_and_subcycling(coupled, velocity):
    rho = 1.003
    coupled.initialize(lambda x, y, z, dx: (rho, rho * np.array(velocity)))
    initial_mass, initial_momentum = raw_integrals(coupled)
    for _ in range(40):
        coupled.step()
    assert coupled.steps == 40 and coupled.fine_steps == 80
    assert coupled.last_donor_time_fractions == (0.0, 0.5)
    for pdf in coupled.states():
        np.testing.assert_allclose(pdf.sum(axis=-1) + 1, rho, atol=2e-12, rtol=0)
        actual = pdf @ coupled.transfer.stencil
        np.testing.assert_allclose(actual, np.broadcast_to(rho * np.array(velocity), actual.shape), atol=2e-12, rtol=0)
    final_mass, final_momentum = raw_integrals(coupled)
    assert abs(final_mass / initial_mass - 1) <= 5e-11
    np.testing.assert_allclose((final_momentum - initial_momentum) / 12**3, 0, atol=2e-12, rtol=0)


@pytest.mark.integration
def test_nonuniform_reflux_is_local_and_balances_the_composite_domain(coupled):
    def initial(x, y, z, dx):
        rho = 1 + 1e-3 * np.exp(-((x - 4.6)**2 + (y - 5.2)**2 + (z - 6.3)**2) / 2)
        return rho, rho[..., None] * np.array([0.03, 0.02, 0.01])
    coupled.initialize(initial)
    mass0, j0 = raw_integrals(coupled)
    max_correction = 0
    for _ in range(8):
        coupled.step()
        np.testing.assert_array_equal(coupled.last_reflux_moments[~coupled.reflux_support], 0)
        max_correction = max(max_correction, np.max(abs(coupled.last_reflux_moments)))
        mass, momentum = raw_integrals(coupled)
        assert abs(mass / mass0 - 1) <= 5e-11
        np.testing.assert_allclose((momentum - j0) / 12**3, 0, atol=2e-12, rtol=0)
    assert max_correction > 1e-12  # this balance test must exercise a real mismatch


@pytest.mark.integration
def test_disabled_reflux_negative_control_is_detected_by_the_balance_test(coupled):
    coupled.reflux_enabled = False
    def initial(x, y, z, dx):
        rho = 1 + 1e-3 * np.exp(-((x - 4.6)**2 + (y - 5.2)**2 + (z - 6.3)**2) / 2)
        return rho, rho[..., None] * np.array([0.03, 0.02, 0.01])
    try:
        coupled.initialize(initial)
        m0, j0 = raw_integrals(coupled)
        violated = False
        for _ in range(8):
            coupled.step()
            mass, momentum = raw_integrals(coupled)
            violated |= abs(mass / m0 - 1) > 5e-11 or np.max(abs(momentum - j0)) / 12**3 > 2e-12
        assert violated, "The acceptance balance test must detect the disabled-reflux bug"
    finally:
        coupled.reflux_enabled = True


@pytest.mark.integration
@pytest.mark.parametrize("box", [FineBox((4, 4, 0), (8, 8, 8)),
                                  FineBox((4, 0, 0), (8, 12, 8)),
                                  FineBox((0, 0, 0), (12, 12, 8))])
def test_whole_periodic_axes_use_fine_periodic_padding(box):
    solver = PeriodicRefinedBox((12, 12, 8), box, AcousticLevel(1, 1, 0.59, 0.59))
    velocity = np.array([0.03, -0.01, 0.02])
    solver.initialize(lambda x, y, z, dx: (1., velocity))
    for _ in range(40):
        solver.step()
    for pdf in solver.states():
        actual = pdf @ solver.transfer.stencil
        np.testing.assert_allclose(actual, np.broadcast_to(velocity, actual.shape), atol=2e-12, rtol=0)
        np.testing.assert_allclose(pdf.sum(axis=-1) + 1, 1, atol=2e-12, rtol=0)


@pytest.mark.integration
def test_nonuniform_whole_fine_periodicity_matches_an_independent_uniform_fine_lattice():
    from aerokinetic.interface import _make_lattice, _read_pdf, _write_pdf
    solver = PeriodicRefinedBox((8, 8, 8), FineBox((0, 0, 0), (8, 8, 8)),
                                AcousticLevel(1, 1, 0.59, 0.59))
    def initial(x, y, z, dx):
        rho = 1 + 1e-5 * np.sin(2 * np.pi * x / 8) * np.cos(2 * np.pi * y / 8)
        j = np.zeros((*np.broadcast_shapes(x.shape, y.shape, z.shape), 3))
        j[..., 0] = rho * 0.03
        j[..., 1] = 1e-5 * np.sin(2 * np.pi * z / 8)
        return rho, j
    solver.initialize(initial)
    reference = _make_lattice(solver.fine_shape, solver.fine_level)
    _write_pdf(reference, solver.states()[1])
    for _ in range(40):
        solver.step()
    reference.run(80)
    np.testing.assert_allclose(solver.states()[1], _read_pdf(reference), atol=2e-13, rtol=0)


@pytest.mark.integration
def test_slice_uses_one_plane_preserves_source_state_and_exports_an_offline_gif(coupled, tmp_path):
    from aerokinetic.interface_visualization import save_slice, animate_slices
    from PIL import Image
    background = np.array([0.03, 0.02, 0.01])
    def initial(x, y, z, dx):
        j = np.broadcast_to(background, (*np.broadcast_shapes(x.shape, y.shape, z.shape), 3)).copy()
        j[..., 0] += 1e-4 * z
        return 1., j
    coupled.initialize(initial)
    original = coupled.states()
    first = save_slice(coupled, tmp_path / 'frame_000.npz', z_plane=6., background_velocity_lattice=background)
    for before, after in zip(original, coupled.states()):
        np.testing.assert_array_equal(before, after)
    assert coupled.steps == 0 and coupled.fine_steps == 0
    with np.load(first, allow_pickle=False) as frame:
        assert frame['z_plane'] == 6 and frame['time'] == 0
        assert frame['coarse_dx'] == 2 * frame['fine_dx']
        assert frame['coarse_x_edges'][0] == 0 and frame['coarse_x_edges'][-1] == 12
        assert frame['fine_x_edges'][0] == 4 and frame['fine_x_edges'][-1] == 8
        for label in ('coarse', 'fine'):
            np.testing.assert_allclose(frame[f'{label}_speed'], 6e-4, atol=2e-13, rtol=0)
    coupled.step()
    save_slice(coupled, tmp_path / 'frame_001.npz', z_plane=6., background_velocity_lattice=background)
    result = animate_slices(tmp_path, tmp_path / 'check.gif', speed_scale=1e-3)
    assert result['frame_count'] == 2
    assert (tmp_path / 'check.png').is_file()
    with Image.open(tmp_path / 'check.gif') as animation:
        assert animation.n_frames == 2
