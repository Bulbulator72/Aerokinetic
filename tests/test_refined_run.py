"""Real two-level output/recovery, including an interrupted history suffix."""
from dataclasses import replace
import json
import shutil

import numpy as np
import pytest

from aerokinetic.refined_solver import RefinedSolver
from aerokinetic.temporal import read_run
from test_refined_airfoil import project


@pytest.mark.integration
def test_actual_refined_run_stop_resume_and_crash_suffix_recovery(tmp_path):
    p=project()
    p=replace(p,run=replace(p.run,average_after=0.))
    direct=RefinedSolver(p)
    reference=tmp_path/'continuous'
    expected=direct.run(reference)
    partial=RefinedSolver(p)
    output=tmp_path/'interrupted'
    stopped=partial.run(output,callback=lambda solver,record: solver.steps<8)
    assert stopped['status']=='stopped'
    assert stopped['steps']==8 and stopped['fine_steps']==16
    original_history=(output/'history.csv').read_bytes()
    # Emulate writes after the last committed checkpoint followed by a crash.
    with (output/'history.csv').open('a') as handle:
        handle.write('10,0.02375,999,999,999,999\n')
    shutil.copy2(output/'frames/frame_000000008.npz',output/'frames/frame_000000010.npz')
    metadata=json.loads((output/'metadata.json').read_text())
    metadata['status']='running'
    (output/'metadata.json').write_text(json.dumps(metadata))
    actual=RefinedSolver.resume_run(output)
    assert actual['status']==expected['status']=='completed'
    assert actual['steps']==expected['steps']==20
    assert actual['fine_steps']==40
    assert (output/'history.csv').read_bytes()==(reference/'history.csv').read_bytes()
    assert (output/'history.csv').read_bytes().startswith(original_history)
    recovered=list((output/'recovery').iterdir())
    assert len(recovered)==1
    assert b'999' in (recovered[0]/'history_before_resume.csv').read_bytes()
    assert (recovered[0]/'frame_000000010.npz').is_file()
    a=RefinedSolver.from_checkpoint(reference/'checkpoint.npz')
    b=RefinedSolver.from_checkpoint(output/'checkpoint.npz')
    for first,second in zip(a.states(),b.states()):
        np.testing.assert_allclose(first,second,atol=5e-13,rtol=0)
    report,_,_=read_run(output)
    assert report['fine_cells_per_chord']==32
    assert report['series']['cd']['mean']==pytest.approx(actual['mean']['cd'])
    assert report['force_time_convention'].startswith('average of both fine')
    with pytest.raises(ValueError,match='completed'):
        RefinedSolver.resume_run(output)
    with pytest.raises(FileExistsError):
        RefinedSolver(p).run(output)


@pytest.mark.integration
def test_refined_resume_rejects_modified_history_and_project(tmp_path):
    p=project()
    output=tmp_path/'stopped'
    RefinedSolver(p).run(output,callback=lambda solver,record: solver.steps<4)
    saved=(output/'history.csv').read_bytes()
    (output/'history.csv').write_bytes(saved.replace(b'step',b'Step',1))
    with pytest.raises(ValueError,match='history before checkpoint changed'):
        RefinedSolver.resume_run(output)
    (output/'history.csv').write_bytes(saved)
    path=output/'project.json'
    data=json.loads(path.read_text())
    data['project']['flow']['alpha_deg']=1.
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='settings changed'):
        RefinedSolver.resume_run(output)
