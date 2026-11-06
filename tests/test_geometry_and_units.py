import numpy as np
import pytest
from aerokinetic import Contour, Section, naca0012, Atmosphere, Flow, Grid, Plan


def test_curved_wall_link_distances_against_exact_plane():
    box = Contour([[0.25, -1], [2, -1], [2, 1], [0.25, 1]])
    p = np.array([[0, 0], [0, 0.5], [0, -0.25]])
    offsets = np.array([[1, 0], [1, 1], [1, -1]])
    np.testing.assert_allclose(box.link_fraction(p, offsets), 0.25)


def test_adapter_preserves_shared_element_frame():
    class Airfoil:
        x, y, name = [2, 3, 3, 2], [0, 0, 0.1, 0.1], "flap"
    section = Section.from_aero2d([Airfoil()])
    np.testing.assert_allclose(section.elements[0].xy[:, 0], [2, 3, 3, 2])


def test_geometry_rejects_crossing_and_overlapping_elements():
    with pytest.raises(ValueError):
        Contour([[0, 0], [1, 1], [0, 1], [1, 0]])
    a = Contour([[0, 0], [1, 0], [1, 1], [0, 1]])
    b = Contour([[0.5, 0.5], [2, 0.5], [2, 2], [0.5, 2]])
    with pytest.raises(ValueError, match="overlap"):
        Section((a, b))


def test_reference_uses_full_assembly_chord_and_requires_actual_length_for_seconds():
    a = Contour([[0, 0], [1, 0], [1, 0.1], [0, 0.1]])
    b = Contour([[1.2, 0], [2, -0.1], [2, -0.2], [1.2, -0.1]])
    section = Section((a, b))
    assert section.chord == pytest.approx(np.hypot(2, 0.15))
    flow = Flow(reynolds=30000, physical_chord_m=0.15)
    grid = Grid(cells_per_chord=64)
    plan = Plan.create(section, flow, grid)
    # Kinematic similarity must preserve Re exactly, including the discrete span.
    assert flow.lattice_speed * grid.cells_per_chord / plan.viscosity == pytest.approx(30000)
    assert flow.physical_speed * flow.physical_chord_m / flow.atmosphere.nu == pytest.approx(30000)
    assert plan.dt_seconds * flow.physical_speed / flow.physical_chord_m == pytest.approx(plan.dt_convective)
    assert Plan.create(section, Flow(), grid).dt_seconds is None
    assert Atmosphere().rho == pytest.approx(1.225, rel=1e-5)


def test_physical_regime_and_memory_planning():
    with pytest.raises(ValueError, match="Mach"):
        Flow(reynolds=100000, physical_chord_m=0.001)
    section = Section((naca0012(31),))
    plan = Plan.create(section, Flow(), Grid(cells_per_chord=512))
    assert plan.estimated_memory_gib > 1000
    assert plan.shape[2] == 256
