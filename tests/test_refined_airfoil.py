"""Independent wall/force/budget/restart checks; short runs are not tunnel validation."""
from dataclasses import replace
import json

import numpy as np
import pytest

from aerokinetic import Contour, Section
from aerokinetic.project import Project, RunSettings
from aerokinetic.refined_solver import RefinedPlan, RefinedSolver, RefinementSettings, refined_project_dict, load_refined_project
from aerokinetic.refined_checkpoint import boundary_arrays


def project(alpha=0., span=.5, reference=None, run=None):
    p = Project.from_dict({"geometry": {"naca0012": {"n_side": 51}},
        "flow": {"reynolds": 10000, "alpha_deg": alpha, "physical_chord_m": .15, "perturbation": 0},
        "grid": {"cells_per_chord": 16, "upstream_chords": 1, "downstream_chords": 2,
                 "transverse_chords": 1, "span_chords": span, "memory_limit_gib": 2},
        "run": {"duration_convective": .05, "sample_every": 2, "frame_every": 5,
                "check_every": 2, "checkpoint_every": 4, "average_after": .025}})
    if reference is not None:
        p = replace(p, section=Section(p.section.elements, p.section.chord, reference))
    return replace(p, run=run) if run is not None else p


def test_refined_plan_preserves_units_and_rejects_unsupported_or_unsafe_settings(tmp_path):
    p = project()
    plan = RefinedPlan.create(p, RefinementSettings())
    assert plan.fine_level.shear_viscosity == pytest.approx(1 / p.flow.reynolds, rel=2e-12)
    assert plan.coarse_level.shear_viscosity == pytest.approx(plan.fine_level.shear_viscosity, rel=2e-12)
    assert plan.coarse_level.bulk_viscosity == pytest.approx(plan.fine_level.bulk_viscosity, rel=2e-12)
    assert plan.minimum_wall_interface_distance_coarse_cells >= 4
    finer=replace(p,grid=replace(p.grid,cells_per_chord=32))
    same_bulk=RefinedPlan.create(finer,RefinementSettings(coarse_bulk_relaxation_time=1.5))
    assert same_bulk.coarse_level.bulk_viscosity==pytest.approx(plan.coarse_level.bulk_viscosity,rel=2e-12)
    with pytest.raises(ValueError, match="guard"):
        RefinedPlan.create(p, RefinementSettings(.05, .05, .05))
    with pytest.raises(ValueError, match="SGS"):
        RefinedPlan.create(replace(p, flow=replace(p.flow, sgs="smagorinsky")), RefinementSettings())
    with pytest.raises(ValueError, match="CPU"):
        RefinedPlan.create(replace(p, grid=replace(p.grid, target="gpu")), RefinementSettings())
    data = refined_project_dict(p, RefinementSettings())
    path = tmp_path / "project.json"
    path.write_text(json.dumps(data))
    loaded, options = load_refined_project(path)
    assert loaded.case_fingerprint == p.case_fingerprint
    assert options == RefinementSettings()
    with pytest.raises(ValueError, match="format"):
        Project.load(path)  # old GUI must never silently run this case on a coarse uniform grid


@pytest.fixture(scope="module")
def solver():
    return RefinedSolver(project())


@pytest.mark.integration
def test_fine_wall_links_intersect_exact_geometry_and_isotropic_pressure_cancels(solver):
    from lbmpy.maxwellian_equilibrium import get_weights
    for block, pdf, indices in solver.walls.owned_links():
        e = solver.transfer.stencil[indices['dir']]
        point = np.column_stack([indices[a] + block.offset[k] + .5 for k,a in enumerate(('x','y'))])
        point = point * solver.fine_level.dx + solver.walls.origin
        active = indices['q'] > 0
        wall = point[active] + indices['q'][active,None] * e[active,:2] * solver.fine_level.dx
        epsilon = 1e-6 * e[active,:2] * solver.fine_level.dx
        assert not solver.contours[0].contains(*(wall - epsilon).T).any()
        assert solver.contours[0].contains(*(wall + epsilon).T).all()
    source = solver._fine.data_handling.cpu_arrays[solver._fine.pdf_array_name]
    saved = source.copy()
    try:
        source[...] = [float(w) for w in get_weights(solver._fine.method.stencil)]
        solver._fine.boundary_handling(**solver._fine.kernel_params)
        loads = solver.walls.measure()
        np.testing.assert_allclose(loads['force_lattice'], 0, atol=2e-12, rtol=0)
        assert abs(loads['moment_z_common_lattice']) <= 2e-12
        assert abs(loads['mass_exchange_lattice']) <= 2e-12
    finally:
        source[...] = saved


@pytest.mark.integration
def test_symmetric_startup_two_substeps_and_actual_open_domain_budget(solver):
    solver.initialize_flow()
    records = []
    leakage = 0.
    for i in range(20):
        records.append(solver.step())
        budget = solver.last_budget
        assert abs(budget['mass_residual_per_volume']) <= 5e-11
        assert max(map(abs,budget['momentum_residual_per_volume'])) <= 2e-12
        leakage += abs(budget['wall_mass_exchange'])
        if i == 0:  # no body signal has reached the outer boundary yet
            assert abs(budget['external_mass_in']) < 2e-13
            assert max(map(abs,budget['external_impulse_in'])) < 2e-12
    np.testing.assert_allclose([[r['cl'],r['cm']] for r in records], 0, atol=2e-10, rtol=0)
    assert np.mean([r['cd'] for r in records]) > 0
    assert leakage > 0  # interpolation mass exchange is measured, not hidden as exact impermeability
    assert solver.coarse_boundary_calls == solver.steps == 20
    assert solver.fine_boundary_calls == solver.fine_steps == 40
    solver.diagnostics()


@pytest.mark.integration
def test_force_conversion_matches_independent_fine_lattice_normalization(solver):
    from aerokinetic.solver import coefficients
    solver.initialize_flow()
    for _ in range(3):
        record = solver.step()
    raw_force = np.mean([w['force_lattice'] for w in solver._interval_wall], axis=0)
    raw_moment = np.mean([w['moment_z_common_lattice'] for w in solver._interval_wall]) / solver.fine_level.dx
    expected = coefficients(raw_force, raw_moment, solver.flow.lattice_speed,
                             2 * solver.grid.cells_per_chord, solver.fine_shape[2])
    np.testing.assert_allclose([record[k] for k in ('cl','cd','cm')], expected, atol=2e-12, rtol=0)
    assert record['t_star'] == pytest.approx((solver.steps-.5) * solver.coarse_level.dt)


@pytest.mark.integration
def test_diagnostics_and_field_export_do_not_reapply_outflow_or_change_pdfs(solver, tmp_path):
    from aerokinetic.refined_visualization import save_frame
    snapshots = [a.copy() for a in solver.states()]
    histories = {k:a.copy() for k,a in boundary_arrays(solver._coarse).items()}
    clocks = (solver.steps,solver.fine_steps,solver.coarse_boundary_calls,solver.fine_boundary_calls)
    solver.diagnostics();solver.diagnostics()
    save_frame(solver,tmp_path/'frame.npz')
    for before,after in zip(snapshots,solver.states()):np.testing.assert_array_equal(before,after)
    for name,array in boundary_arrays(solver._coarse).items():np.testing.assert_array_equal(array,histories[name])
    assert clocks == (solver.steps,solver.fine_steps,solver.coarse_boundary_calls,solver.fine_boundary_calls)
    with np.load(tmp_path/'frame.npz',allow_pickle=False) as frame:
        assert frame['coarse_dx'] == 2*frame['fine_dx']
        assert np.isfinite(frame['fine_speed'][~frame['fine_mask']]).all()
        assert np.isnan(frame['fine_cp'][frame['fine_mask']]).all()


@pytest.mark.integration
def test_unused_virtual_coarse_core_cannot_influence_owned_fluid(solver):
    solver.initialize_flow()
    alternative = RefinedSolver(solver.project,solver.refinement)
    fields = alternative._free_fields.copy()
    fields[1:4] = [.025,-.004,.003]
    alternative._free_pdf = alternative.transfer.populations(fields)
    for _ in range(8):solver.step();alternative.step()
    a,b=solver.states(),alternative.states()
    np.testing.assert_allclose(a[0][solver.coarse_owned],b[0][solver.coarse_owned],atol=2e-13,rtol=0)
    np.testing.assert_allclose(a[1][solver.fine_fluid],b[1][solver.fine_fluid],atol=2e-13,rtol=0)


@pytest.mark.integration
def test_checkpoint_restores_both_levels_with_nontrivial_outflow_history(solver,tmp_path):
    solver.initialize_flow()
    history0=boundary_arrays(solver._coarse)[solver._outflow.name].copy()
    view=solver._physical_views()[0]
    shape=view[-4:].shape[:3]
    fields=np.broadcast_to(solver._free_fields,(*shape,10)).copy()
    fields[...,0]+=1e-4
    fields[...,1]+=1e-4/np.sqrt(3)
    view[-4:]=solver.transfer.populations(fields)
    for _ in range(4):solver.step()
    history=boundary_arrays(solver._coarse)[solver._outflow.name]
    assert np.max(abs(history['pdf']-history0['pdf']))>1e-10
    path=tmp_path/'checkpoint.npz'
    solver.save_checkpoint(path,{'test':'nontrivial outflow'})
    expected=[solver.step() for _ in range(4)]
    final=solver.states()
    restored=RefinedSolver.from_checkpoint(path)
    actual=[restored.step() for _ in range(4)]
    for before,after in zip(final,restored.states()):np.testing.assert_allclose(before,after,atol=5e-13,rtol=0)
    np.testing.assert_allclose([[r[k] for k in ('cl','cd','cm')] for r in actual],
                               [[r[k] for k in ('cl','cd','cm')] for r in expected],atol=1e-10,rtol=0)
    assert restored.coarse_boundary_calls==restored.steps==8
    assert restored.fine_boundary_calls==restored.fine_steps==16
    with np.load(path,allow_pickle=False) as data:arrays={k:data[k] for k in data.files}
    meta=json.loads(str(arrays['metadata']));key=next(iter(meta['array_sha256']))
    arrays[key]=arrays[key].copy();arrays[key].flat[0]+=1
    np.savez(tmp_path/'bad.npz',**arrays)
    with pytest.raises(ValueError,match='checksum'):RefinedSolver.from_checkpoint(tmp_path/'bad.npz')


@pytest.mark.integration
def test_arbitrary_moment_reference_translation(solver):
    a=project(alpha=5.)
    b=project(alpha=5.,reference=(.4,.03))
    base=RefinedSolver(replace(a,flow=replace(a.flow,perturbation=.001)))
    shifted=RefinedSolver(replace(b,flow=replace(b.flow,perturbation=.001)))
    for _ in range(4):a,b=base.step(),shifted.step()
    delta=np.array([.15,.03]);angle=-np.deg2rad(5.)
    rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    dx,dy=rotation@delta
    np.testing.assert_allclose([a['cl'],a['cd']],[b['cl'],b['cd']],atol=2e-12,rtol=0)
    assert b['cm']-a['cm']==pytest.approx(dx*a['cl']-dy*a['cd'],abs=2e-12)


@pytest.mark.integration
def test_periodic_donor_layers_do_not_add_to_force_or_reference_span(solver):
    solver.initialize_flow()
    wide=RefinedSolver(project(span=1.))
    for _ in range(4):a,b=solver.step(),wide.step()
    np.testing.assert_allclose([a[k] for k in ('cl','cd','cm')],[b[k] for k in ('cl','cd','cm')],atol=2e-10,rtol=0)


@pytest.mark.integration
def test_fractional_bouzidi_flat_wall_sine_decay():
    from aerokinetic.interface import _make_lattice
    from aerokinetic.refinement import AcousticLevel
    from aerokinetic.walls import StationaryWalls
    nx,ny,nz=8,16,8
    lower,upper=2.2,13.6
    height=upper-lower
    contour0=Contour([[-10,-10],[20,-10],[20,lower],[-10,lower]],'lower')
    contour1=Contour([[-10,upper],[20,upper],[20,30],[-10,30]],'upper')
    lbm=_make_lattice((nx,ny,nz),AcousticLevel(1,1,.59,.59))
    walls=StationaryWalls(lbm,(contour0,contour1),origin=(0,0),dx=1,
                           owned_slices=(slice(0,nx),slice(0,ny),slice(0,nz)))
    for block in lbm.data_handling.iterate(ghost_layers=True):
        x,y,z=block.midpoint_arrays
        velocity=block[lbm.velocity_data_name]
        velocity[...]=0
        inside=(y>lower)&(y<upper)
        velocity[...,0][inside]=1e-4*np.sin(np.pi*(y[inside]-lower)/height)
    lbm.set_pdf_fields_from_macroscopic_values()
    walls.lbm.boundary_handling.prepare()
    for _ in range(400):
        lbm.data_handling.run_kernel(lbm._lbmKernels[0],**lbm.kernel_params)
        lbm._sync_src();lbm.boundary_handling(**lbm.kernel_params)
        lbm.data_handling.run_kernel(lbm._lbmKernels[1],**lbm.kernel_params)
        lbm.data_handling.swap(lbm._pdf_arr_name,lbm._tmp_arr_name,lbm._gpu)
    lbm.post_run()
    velocity=np.asarray(lbm.data_handling.gather_array(lbm.velocity_data_name))
    y=np.arange(ny)+.5;fluid=(y>lower)&(y<upper)
    expected=1e-4*np.sin(np.pi*(y[fluid]-lower)/height)*np.exp(-.03*(np.pi/height)**2*400)
    actual=velocity[...,0].mean(axis=(0,2))[fluid]
    assert np.linalg.norm(actual-expected)/np.linalg.norm(expected)<=.02
