"""Independent split/uninterrupted evolution and immutable parent provenance."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from aerokinetic import Flow,Grid,Project,RunSettings,Section,Solver,naca0012,continue_run


def digest_files(directory):
    return {p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*')if p.is_file()}


def make_project():
    return Project(Section((naca0012(31),)),Flow(reynolds=30000,alpha_deg=3,perturbation=.001),
        Grid(cells_per_chord=16,upstream_chords=1,downstream_chords=2,transverse_chords=1,
             span_chords=.5,memory_limit_gib=2),
        RunSettings(duration_convective=.02,sample_every=3,frame_every=4,check_every=4,
                    average_after=0,checkpoint_every=4))


@pytest.mark.integration
@pytest.mark.parametrize('refined',[False,True])
def test_completed_continuation_matches_direct_evolution_and_preserves_parent(tmp_path,refined):
    from aerokinetic.refined_solver import RefinedSolver
    project=make_project()
    dt=project.plan().dt_convective
    first=replace(project,run=replace(project.run,duration_convective=7*dt))
    direct_project=replace(project,run=replace(project.run,duration_convective=12*dt))
    parent=tmp_path/'parent';child=tmp_path/'child'
    if refined:
        s=RefinedSolver(first);s.run(parent)
        direct=RefinedSolver(direct_project);direct.run(tmp_path/'direct')
    else:
        from dataclasses import asdict
        s=Solver(first.section,first.flow,first.grid);s.run(output=parent,**asdict(first.run))
        direct=Solver(project.section,project.flow,project.grid)
        direct.run(output=tmp_path/'direct',**asdict(direct_project.run))
    assert s.steps==7
    original=digest_files(parent)
    result=continue_run(parent,child,5*dt)
    assert result['status']=='completed'and result['steps']==12
    assert digest_files(parent)==original
    lineage=json.loads((child/'continuation.json').read_text())
    assert lineage['parent_files_unchanged']and lineage['history_scope']=='new_segment_only'
    assert lineage['start_step']==7 and lineage['average_after_t_star']==pytest.approx(7*dt)
    cls=RefinedSolver if refined else Solver
    resumed=cls.from_checkpoint(child/'checkpoint.npz')
    if refined:
        for a,b in ((resumed._coarse,direct._coarse),(resumed._fine,direct._fine)):
            np.testing.assert_array_equal(a.data_handling.cpu_arrays[a.pdf_array_name],b.data_handling.cpu_arrays[b.pdf_array_name])
    else:
        a,b=resumed._stepper,direct._stepper
        np.testing.assert_array_equal(a.data_handling.cpu_arrays[a.pdf_array_name],b.data_handling.cpu_arrays[b.pdf_array_name])
    import csv
    with (child/'history.csv').open()as handle:rows=list(csv.DictReader(handle))
    assert [int(r['step'])for r in rows]==[9,12]
    # A child interrupted after its first sample must preserve its original
    # start clock and resume to the new end, not to the parent's old target.
    interrupted=tmp_path/'interrupted_child'
    stopped=continue_run(parent,interrupted,9*dt,callback=lambda solver,row:solver.steps<9)
    assert stopped['status']=='stopped'and stopped['steps']==9
    finished=cls.resume_run(interrupted)
    assert finished['status']=='completed'and finished['steps']==16
    assert json.loads((interrupted/'metadata.json').read_text())['start_step']==7
    child16=cls.from_checkpoint(interrupted/'checkpoint.npz')
    for _ in range(4):
        direct.step()if refined else direct.step(sample=True)
    if refined:
        pairs=((child16._coarse,direct._coarse),(child16._fine,direct._fine))
    else:
        pairs=((child16._stepper,direct._stepper),)
    for a,b in pairs:
        np.testing.assert_array_equal(a.data_handling.cpu_arrays[a.pdf_array_name],b.data_handling.cpu_arrays[b.pdf_array_name])
    assert digest_files(parent)==original
    # Resuming an already completed segment must still be rejected.
    with pytest.raises(ValueError,match='completed'):
        cls.resume_run(child)
    for invalid in (parent,parent/'nested'):
        with pytest.raises(ValueError,match='outside'):
            continue_run(parent,invalid,dt)
    with pytest.raises(FileExistsError):continue_run(parent,child,dt)
    history=parent/'history.csv';saved=history.read_bytes()
    history.write_bytes(saved.replace(b'cl',b'xx',1))
    with pytest.raises(ValueError,match='history'):
        continue_run(parent,tmp_path/'tampered',dt)
    assert not(tmp_path/'tampered').exists()
    history.write_bytes(saved)


def test_invalid_continuation_time_rejected_before_io(tmp_path):
    for value in (0,-1,float('nan'),float('inf')):
        with pytest.raises(ValueError,match='positive'):
            continue_run(tmp_path/'absent',tmp_path/'new',value)
    assert not(tmp_path/'new').exists()
