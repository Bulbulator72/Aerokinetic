"""Research farfield configuration and exact history-dependent restart checks."""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from aerokinetic import Flow, Grid, Project, RunSettings, Section, Solver, continue_run, naca0012


def small_project():
    return Project(Section((naca0012(31),)),
        Flow(reynolds=30000,alpha_deg=3,perturbation=.001),
        Grid(cells_per_chord=16,upstream_chords=1,downstream_chords=2,
             transverse_chords=1,span_chords=.5,memory_limit_gib=2,farfield='characteristic_transport_v1'),
        RunSettings(duration_convective=.0175,sample_every=1,frame_every=4,check_every=4,
                    average_after=0,checkpoint_every=4))


def hashes(directory):
    return {p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*')if p.is_file()}


def assert_state_equal(a,b):
    from aerokinetic.checkpoint import _boundaries
    assert a.steps==b.steps
    names=[a._stepper.pdf_array_name,a._stepper._tmp_arr_name]
    names.extend(name for name in a._stepper.data_handling.cpu_arrays if name.startswith('ff_initial_'))
    assert len(names)>2
    for name in names:
        np.testing.assert_array_equal(a._stepper.data_handling.cpu_arrays[name],
                                      b._stepper.data_handling.cpu_arrays[name])
    aa,bb=_boundaries(a),_boundaries(b)
    assert aa.keys()==bb.keys()
    for name in aa:
        np.testing.assert_array_equal(aa[name],bb[name])
        if 'clock'in aa[name].dtype.names:np.testing.assert_array_equal(aa[name]['clock'],a.steps)
    assert any('incoming'in array.dtype.names for array in aa.values())


def test_configuration_roundtrip_and_released_fingerprint(tmp_path,capsys):
    old=Project.load(Path(__file__).parent/'fixtures'/'legacy051_project.json')
    assert old.grid.farfield=='legacy'
    assert 'farfield'not in old.as_dict()['grid']
    assert old.case_fingerprint=='9ae371cecf492f67494d9f6ac6eb70f2a33cc4e6ca680a7088b6ce146206f48f'
    candidate=replace(old,grid=replace(old.grid,farfield='characteristic_transport_v1'))
    path=tmp_path/'case.json';candidate.save(path)
    assert Project.load(path).case_fingerprint==candidate.case_fingerprint!=old.case_fingerprint
    assert candidate.as_dict()['grid']['farfield']=='characteristic_transport_v1'
    from aerokinetic.cli import main
    main(['plan',str(path)])
    assert 'characteristic_transport_v1'in capsys.readouterr().out
    main(['plan',str(path),'--farfield','legacy'])
    assert 'characteristic_transport_v1'not in capsys.readouterr().out
    with pytest.raises(ValueError,match='farfield'):Grid(farfield='misspelled')
    with pytest.raises(ValueError,match='CPU'):Grid(farfield='characteristic_transport_v1',target='gpu')
    from aerokinetic.refined_solver import RefinedPlan
    with pytest.raises(ValueError,match='farfield'):RefinedPlan.create(candidate,None)


@pytest.mark.integration
def test_history_dependent_farfield_restart_and_continuation(tmp_path):
    project=small_project();dt=project.plan().dt_convective
    parent=tmp_path/'parent'
    first=Solver(project.section,project.flow,project.grid)
    assert first.run(output=parent,**asdict(project.run))['steps']==7
    saved=hashes(parent)
    direct=Solver(project.section,project.flow,project.grid)
    for _ in range(12):direct.step(sample=True)
    child=tmp_path/'child'
    result=continue_run(parent,child,5*dt)
    assert result['status']=='completed'and result['steps']==12
    assert_state_equal(Solver.from_checkpoint(child/'checkpoint.npz'),direct)
    assert hashes(parent)==saved
    interrupted=tmp_path/'interrupted'
    stopped=continue_run(parent,interrupted,9*dt,callback=lambda solver,row:solver.steps<9)
    assert stopped['status']=='stopped'and stopped['steps']==9
    finished=Solver.resume_run(interrupted)
    assert finished['status']=='completed'and finished['steps']==16
    for _ in range(4):direct.step(sample=True)
    assert_state_equal(Solver.from_checkpoint(interrupted/'checkpoint.npz'),direct)
    assert json.loads((interrupted/'metadata.json').read_text())['start_step']==7
    assert hashes(parent)==saved


@pytest.mark.integration
def test_symmetric_profile_has_zero_lift_and_pitching_moment():
    project=small_project()
    solver=Solver(project.section,replace(project.flow,alpha_deg=0,perturbation=0),project.grid)
    for _ in range(24):
        loads=solver.step(sample=True)
        np.testing.assert_allclose([loads['cl'],loads['cm']],0,atol=2e-10,rtol=0)
