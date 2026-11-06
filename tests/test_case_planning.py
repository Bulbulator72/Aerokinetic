import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("case_planning", Path(__file__).resolve().parents[1] / "tools/plan_case.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


def test_upper_re_mach_case_requires_large_physical_chord_and_new_solver():
    case = planner.resolve_case(reynolds=1e8, mach=0.4, alpha_deg=30)
    assert case["chord_m"] == pytest.approx(10.731416978, rel=1e-9)
    assert case["speed_m_s"] == pytest.approx(136.11759521, rel=1e-9)
    assert case["matches_legacy_solver_input_limits"] is False
    assert case["solver_executed"] is False
    assert case["aerodynamic_validation"] == "not_performed"


def test_physical_geometry_is_not_silently_rescaled_to_fit_mach():
    with pytest.raises(ValueError, match="Mach=.*exceeds"):
        planner.resolve_case(reynolds=1e8, chord_m=1)


def test_contradictory_overdetermined_case_is_rejected():
    with pytest.raises(ValueError, match="Inconsistent"):
        planner.resolve_case(reynolds=1e8, chord_m=1, mach=0.4)


def test_speed_and_mach_are_not_two_independent_scale_inputs():
    with pytest.raises(ValueError, match="independent"):
        planner.resolve_case(speed_m_s=30, mach=0.1)


def test_all_input_modes_recover_the_same_freestream():
    original = planner.resolve_case(reynolds=1e5, mach=0.05, alpha_deg=-10)
    via_physical = planner.resolve_case(chord_m=original["chord_m"], speed_m_s=original["speed_m_s"], alpha_deg=-10)
    via_re = planner.resolve_case(chord_m=original["chord_m"], reynolds=1e5, alpha_deg=-10)
    for key in ("reynolds", "mach", "chord_m", "speed_m_s"):
        assert via_physical[key] == pytest.approx(original[key])
        assert via_re[key] == pytest.approx(original[key])
    assert original["matches_legacy_solver_input_limits"] is True


@pytest.mark.parametrize("values", [
    {"reynolds": float("nan"), "mach": 0.1},
    {"reynolds": 1e5, "mach": 0},
    {"reynolds": 1e8, "mach": 0.41},
    {"reynolds": 1e9, "mach": 0.2},
    {"reynolds": 1e5, "mach": 0.1, "alpha_deg": 31},
])
def test_invalid_application_inputs(values):
    with pytest.raises(ValueError):
        planner.resolve_case(**values)
