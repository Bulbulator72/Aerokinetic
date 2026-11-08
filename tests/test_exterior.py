"""Ownership, physical budgets and restart of the explicit exterior mode."""
import csv
from dataclasses import replace
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from aerokinetic import Contour, Flow, Grid, Plan, Project, RefinedPlan, RefinementSettings, Section, Solver, naca4
from aerokinetic.convergence import conservative_samples


MODE = "exterior_regularized_fd_v1"
TOOLS = Path(__file__).resolve().parents[1]/"tools/exterior_library_checks/budget.py"
spec = importlib.util.spec_from_file_location("independent_exterior_budget", TOOLS)
budget_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget_module)


def make_solver(*, threads=1, alpha=4):
    return Solver(Section((naca4("0012", 31),)),
        Flow(reynolds=30000, alpha_deg=alpha, perturbation=1e-3),
        Grid(cells_per_chord=16, upstream_chords=1, downstream_chords=2,
             transverse_chords=1, span_chords=.5, memory_limit_gib=1,
             farfield=MODE, openmp_threads=threads))


def pdf(s):
    return s._stepper.data_handling.cpu_arrays[s._stepper.pdf_array_name]


def test_plan_preserves_physical_domain_and_rejects_unsupported_models():
    p = Project(Section((naca4("0012", 31),)), Flow(reynolds=30000),
                Grid(cells_per_chord=16, upstream_chords=1, downstream_chords=2, transverse_chords=1))
    legacy = p.plan()
    explicit = replace(p, grid=replace(p.grid, farfield=MODE))
    new = explicit.plan()
    for name in ("shape", "origin", "dx", "span", "viscosity", "dt_convective", "dt_seconds"):
        assert getattr(new, name) == getattr(legacy, name)
    assert new.estimated_memory_gib > legacy.estimated_memory_gib
    with pytest.raises(ValueError, match="CPU"):
        replace(p.grid, farfield=MODE, target="gpu")
    with pytest.raises(ValueError, match="sgs=none"):
        Plan.create(p.section, replace(p.flow, sgs="smagorinsky"), explicit.grid)
    with pytest.raises(ValueError, match="uniform"):
        RefinedPlan.create(explicit, RefinementSettings())


@pytest.mark.integration
def test_frames_and_residuals_own_only_original_physical_cells(tmp_path):
    s = make_solver()
    assert s._storage_shape == (s.plan.shape[0]+2, s.plan.shape[1]+2, s.plan.shape[2])
    for _ in range(5):
        s.step(sample=True)
    s.save_frame(tmp_path/"frame.npz")
    with np.load(tmp_path/"frame.npz", allow_pickle=False) as f:
        assert f["speed"].shape == s.plan.shape[:2]
        np.testing.assert_array_equal(f["origin"], s.plan.origin)
        np.testing.assert_array_equal(f["mask"], s._solid_xy)
    values, volumes = conservative_samples(s)
    x = (np.arange(s.plan.shape[0])+.5)*s.plan.dx + s.plan.origin[0]
    y = (np.arange(s.plan.shape[1])+.5)*s.plan.dx + s.plan.origin[1]
    solid = s.contours[0].contains(x[:, None], y[None, :])
    expected = int((~solid).sum())*s.plan.shape[2]
    assert values[0].shape == (expected, 4)
    assert len(values) == len(volumes) == 1 and volumes[0] == s.plan.dx**3


@pytest.mark.integration
def test_independent_fluid_budget_wall_mass_force_and_torque():
    s = make_solver()
    observer = budget_module.FluidBudget(s)
    for _ in range(12):
        load, audit = observer.observe()
        assert max(abs(v) for v in audit["fluid_budget_per_cell"]) <= 2e-12
        assert max(abs(v) for v in audit["physical_fluid_overwrite_budget"]) == 0
        wall = np.asarray(audit["wall_flux"])
        body = load["element_lattice_loads"].sum(axis=0)
        assert np.max(abs(body[:3] + wall[1:4])) <= 2e-10
        assert abs(body[3] + wall[4]) <= 2e-9
        assert abs(load["wall_mass_exchange_lattice"] - wall[0]) <= 2e-10


@pytest.mark.integration
def test_two_elements_keep_common_chord_and_global_budgets():
    flap = Contour(naca4("0020",31).xy*.5 + (1.15,-.05), "flap")
    section = Section((naca4("0012",31),flap))
    s = Solver(section, Flow(reynolds=30000,alpha_deg=4),
        Grid(cells_per_chord=32,upstream_chords=1,downstream_chords=2,
             transverse_chords=1,span_chords=.25,memory_limit_gib=1,farfield=MODE,openmp_threads=2))
    # Section reference chord joins the assembly leading/trailing midpoints;
    # its endpoints differ vertically by.05 as well as horizontally by1.65.
    assert s.section.chord == pytest.approx(np.hypot(1.65,.05))
    observer = budget_module.FluidBudget(s)
    for _ in range(8):
        load,audit = observer.observe()
        assert load["element_lattice_loads"].shape == (2,4)
        assert max(abs(v)for v in audit["fluid_budget_per_cell"]) <= 2e-12
        assert abs(load["wall_mass_exchange_lattice"]-audit["wall_flux"][0]) <= 2e-10
        body = load["element_lattice_loads"].sum(axis=0)
        np.testing.assert_allclose(body[:3],-np.asarray(audit["wall_flux"])[1:4],atol=2e-10,rtol=0)
    assert s.diagnostics()["local_mach_max"] < .3


@pytest.mark.integration
@pytest.mark.parametrize("split", [0, 41, 42])
def test_checkpoint_restores_phase_trace_pdfs_loads_and_cumulative_diagnostics(tmp_path, split):
    s = make_solver()
    for _ in range(split):
        s.step(sample=True)
    s.save_checkpoint(tmp_path/"state.npz")
    reference = [s.step(sample=True) for _ in range(9)]
    source = pdf(s).copy()
    temporary = s._stepper.data_handling.cpu_arrays[s._stepper._tmp_arr_name].copy()
    diagnostics = s.boundary_diagnostics()
    restored = Solver.from_checkpoint(tmp_path/"state.npz")
    measured = [restored.step(sample=True) for _ in range(9)]
    np.testing.assert_array_equal(pdf(restored), source)
    np.testing.assert_array_equal(restored._stepper.data_handling.cpu_arrays[restored._stepper._tmp_arr_name], temporary)
    np.testing.assert_array_equal(restored._exterior.initial, s._exterior.initial)
    assert restored.boundary_diagnostics() == diagnostics
    for a, b in zip(reference, measured):
        for name in ("cl", "cd", "cm", "t_star", "wall_mass_exchange_lattice",
                     "wall_mass_exchange_accumulated", "exterior_source_mass_accumulated"):
            assert a[name] == b[name]


@pytest.mark.integration
def test_wrong_checkpoint_phase_is_rejected_before_rebuilding_case(tmp_path):
    s = make_solver()
    s.save_checkpoint(tmp_path/"good.npz")
    with np.load(tmp_path/"good.npz", allow_pickle=False) as f:
        arrays = {name: f[name] for name in f.files}
    meta = json.loads(str(arrays["metadata"]))
    assert meta["numerics_revision"] == 2
    meta["exterior_state"]["pdf_stage"] = "post_collision"
    arrays["metadata"] = np.asarray(json.dumps(meta))
    np.savez(tmp_path/"wrong.npz", **arrays)
    with pytest.raises(ValueError, match="phase/signature"):
        Solver.from_checkpoint(tmp_path/"wrong.npz")


@pytest.mark.integration
def test_openmp_two_threads_reproduce_fields_and_loads():
    a, b = make_solver(threads=1), make_solver(threads=2)
    for _ in range(64):
        x, y = a.step(sample=True), b.step(sample=True)
        for key in ("cl", "cd", "cm"):
            assert abs(x[key] - y[key]) <= 2e-10
    np.testing.assert_allclose(pdf(a), pdf(b), rtol=0, atol=2e-12)


@pytest.mark.integration
def test_stop_resume_preserves_complete_mass_history_and_physical_frames(tmp_path):
    s = make_solver()
    settings = dict(duration_convective=17*s.plan.dt_convective,
                    sample_every=3, check_every=4, frame_every=4, checkpoint_every=4)
    result = s.run(output=tmp_path/"split", callback=lambda solver, row: solver.steps < 6, **settings)
    assert result["status"] == "stopped" and result["steps"] == 6
    result = Solver.resume_run(tmp_path/"split")
    assert result["status"] == "completed" and result["steps"] == 17
    reference = make_solver()
    reference.run(output=tmp_path/"full", **settings)
    assert (tmp_path/"split/history.csv").read_bytes() == (tmp_path/"full/history.csv").read_bytes()
    with (tmp_path/"split/history.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [int(r["step"]) for r in rows] == [3, 6, 9, 12, 15]
    assert "wall_mass_exchange_accumulated" in rows[0]
    assert result["boundary_diagnostics"] == reference.boundary_diagnostics()


@pytest.mark.integration
def test_nonfinite_boundary_diagnostic_does_not_mask_the_flow_failure(tmp_path):
    from aerokinetic.solver import NumericalFailure
    s = make_solver()
    def fail(**_):
        s._exterior.source_total[0] = np.nan
        raise NumericalFailure("injected nonfinite exterior state")
    s._exterior.step = fail
    with pytest.raises(NumericalFailure,match="injected nonfinite"):
        s.run(s.plan.dt_convective,tmp_path/"failed",sample_every=1,check_every=1,frame_every=1)
    metadata = json.loads((tmp_path/"failed/metadata.json").read_text())
    summary = json.loads((tmp_path/"failed/summary.json").read_text())
    assert metadata["status"] == summary["status"] == "failed"
    assert "injected nonfinite" in metadata["failure"]
    assert summary["boundary_diagnostics"]["exterior_source_mass_momentum_accumulated"][0] is None
