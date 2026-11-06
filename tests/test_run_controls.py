from dataclasses import replace,asdict
import csv
import json

import numpy as np
import pytest

from aerokinetic import Solver,RefinedSolver,StopCriteria
from test_refined_airfoil import project


def make_project(termination=None):
    p=project(alpha=0.)
    return replace(p,run=replace(p.run,duration_convective=.025,average_after=0.,check_every=3,
                                 termination=termination or StopCriteria()))


def execute(p,backend,out,callback=None):
    if backend=='refined':return RefinedSolver(p).run(out,callback=callback)
    return Solver(p.section,p.flow,p.grid).run(output=out,callback=callback,**asdict(p.run))


@pytest.mark.integration
@pytest.mark.parametrize('backend',['refined','uniform'])
def test_residual_history_and_held_state_survive_off_schedule_stop_resume(tmp_path,backend):
    p=make_project()
    direct=tmp_path/'direct';partial=tmp_path/'partial'
    execute(p,backend,direct)
    stopped=execute(p,backend,partial,callback=lambda s,r:s.steps<4)
    assert stopped['status']=='stopped'
    saved=(partial/'residuals.csv').read_bytes()
    assert len(list(csv.DictReader(saved.decode().splitlines())))==1 # last residual at 3, checkpoint at 4
    with (partial/'residuals.csv').open('a') as handle:handle.write('999,broken,uncommitted\n')
    solver_type=RefinedSolver if backend=='refined' else Solver
    result=solver_type.resume_run(partial)
    assert result['termination_reason']=='duration_reached'
    assert (partial/'residuals.csv').read_bytes()==(direct/'residuals.csv').read_bytes()
    assert (partial/'history.csv').read_bytes()==(direct/'history.csv').read_bytes()
    recovered=list((partial/'recovery').glob('*/residuals_before_resume.csv'))
    assert len(recovered)==1 and b'broken' in recovered[0].read_bytes()
    assert result['last_residuals']['step']==9


@pytest.mark.integration
def test_residual_prefix_corruption_rejected_before_recovery(tmp_path):
    p=make_project();out=tmp_path/'partial'
    execute(p,'refined',out,callback=lambda s,r:s.steps<4)
    path=out/'residuals.csv';path.write_bytes(path.read_bytes().replace(b't_star',b'T_star',1))
    with pytest.raises(ValueError,match='Residual history before checkpoint changed'):RefinedSolver.resume_run(out)


@pytest.mark.integration
def test_actual_all_thresholds_hold_and_step_limit_have_distinct_reasons(tmp_path):
    loose=StopCriteria(mode='steady',minimum_t_star=.0075,consecutive_checks=3,
                       mass_tolerance=1e5,momentum_x_tolerance=1e5,momentum_y_tolerance=1e5,momentum_z_tolerance=1e5)
    p=make_project(loose);p=replace(p,run=replace(p.run,check_every=1))
    converged=execute(p,'refined',tmp_path/'steady')
    assert converged['status']=='completed' and converged['termination_reason']=='residual_targets_met'
    assert converged['steps']==5 and converged['steady_residual_criterion_met']
    p=make_project(StopCriteria(max_steps=5))
    limited=execute(p,'refined',tmp_path/'steps')
    assert limited['steps']==5 and limited['termination_reason']=='max_steps_reached'
    assert not limited['steady_residual_criterion_met']
    empty=replace(p,run=replace(p.run,sample_every=100))
    no_forces=execute(empty,'refined',tmp_path/'no_force_samples')
    assert no_forces['samples']==0 and no_forces['mean'] is None and no_forces['last_residuals']


@pytest.mark.integration
def test_actual_two_threads_match_serial_state_loads_and_budget(tmp_path):
    from pystencils import get_code_str
    p=project(alpha=5.)
    a=RefinedSolver(p)
    b=RefinedSolver(replace(p,grid=replace(p.grid,openmp_threads=2)))
    code=get_code_str(b._coarse._lbmKernels[0].ast)
    assert '#pragma omp' in code and 'num_threads(2)' in code
    code=get_code_str(b.transfer._native[0].ast)
    assert '#pragma omp' in code and 'num_threads(2)' in code
    for _ in range(8):
        left,right=a.step(),b.step()
        np.testing.assert_allclose([left[k] for k in ('cl','cd','cm')],[right[k] for k in ('cl','cd','cm')],atol=1e-10,rtol=0)
        assert abs(b.last_budget['mass_residual_per_volume'])<=5e-11
        assert max(map(abs,b.last_budget['momentum_residual_per_volume']))<=2e-12
    for left,right in zip(a.states(),b.states()):np.testing.assert_allclose(left,right,atol=2e-12,rtol=0)
    b.save_checkpoint(tmp_path/'threads.npz')
    expected=b.step();restored=RefinedSolver.from_checkpoint(tmp_path/'threads.npz')
    actual=restored.step()
    assert restored.grid.openmp_threads==2
    np.testing.assert_allclose([actual[k] for k in ('cl','cd','cm')],[expected[k] for k in ('cl','cd','cm')],atol=1e-10,rtol=0)


@pytest.mark.integration
def test_uniform_two_threads_preserve_pdfs_and_loads():
    p=project(alpha=5.)
    a=Solver(p.section,p.flow,p.grid)
    b=Solver(p.section,p.flow,replace(p.grid,openmp_threads=2))
    for _ in range(5):
        left,right=a.step(sample=True),b.step(sample=True)
        np.testing.assert_allclose([left[k] for k in ('cl','cd','cm')],[right[k] for k in ('cl','cd','cm')],atol=1e-10,rtol=0)
    for s in (a,b):s.diagnostics()
    aa=a._stepper.data_handling.cpu_arrays[a._stepper.pdf_array_name]
    bb=b._stepper.data_handling.cpu_arrays[b._stepper.pdf_array_name]
    # Outer ghosts and solid storage are not physical state and can contain
    # untouched allocation bytes. Compare the actual owned fluid only.
    np.testing.assert_allclose(aa[1:-1,1:-1,1:-1][a._fluid],bb[1:-1,1:-1,1:-1][b._fluid],atol=2e-12,rtol=0)
