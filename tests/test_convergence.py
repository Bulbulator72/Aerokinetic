from dataclasses import replace
import json
from types import SimpleNamespace

import numpy as np
import pytest

from aerokinetic.convergence import StopCriteria,RunControl,residual_norms,save_control,restore_control
from aerokinetic.project import Project,RunSettings


def test_four_components_volume_weights_and_elapsed_time():
    previous=(np.zeros((1,4)),np.zeros((8,4)))
    current=(np.array([[1,2,3,4.]]),np.broadcast_to([3.,4,5,6],(8,4)))
    l2,linf=residual_norms(previous,current,(1.,.125),.5)
    expected=np.sqrt((np.array([1,2,3,4.])**2+np.array([3,4,5,6.])**2)/2)/.5
    np.testing.assert_allclose(l2,expected,atol=2e-12,rtol=0)
    np.testing.assert_allclose(linf,[6,8,10,12],atol=2e-12,rtol=0)
    doubled,_=residual_norms(previous,current,(1.,.125),1.)
    np.testing.assert_allclose(doubled,l2/2,atol=2e-12,rtol=0)
    zero,_=residual_norms(current,current,(1.,.125),1.)
    assert (zero==0).all()


@pytest.mark.parametrize('value',[-1,0,np.nan,np.inf])
def test_invalid_residual_interval_and_nan_state(value):
    q=(np.ones((3,4)),)
    with pytest.raises(ValueError):residual_norms(q,q,(1.,),value)
    bad=q[0].copy();bad[0,2]=np.nan
    with pytest.raises(ValueError,match='finite'):residual_norms(q,(bad,),(1.,),1.)


def test_stop_settings_roundtrip_and_legacy_defaults():
    p=Project.from_dict({'run':{'duration_convective':3.,'termination':{'mode':'steady','max_steps':300,'momentum_y_tolerance':2e-4}}})
    assert Project.from_dict(p.as_dict()).run==p.run
    assert RunSettings().termination==StopCriteria()
    for values in ({'mode':'unknown'},{'max_steps':True},{'consecutive_checks':0},{'mass_tolerance':0},{'wall_time_limit_s':-1}):
        with pytest.raises(ValueError):StopCriteria(**values)


def fake_solver():
    return SimpleNamespace(steps=0,plan=SimpleNamespace(dt_convective=.25),state=(np.ones((2,4)),),
        last_budget={'mass_residual_per_volume':0.,'momentum_residual_per_volume':[0.,0.,0.]})


def test_all_components_minimum_time_and_streak_with_restart(monkeypatch,tmp_path):
    import aerokinetic.convergence as module
    monkeypatch.setattr(module,'conservative_samples',lambda s:(tuple(a.copy() for a in s.state),(1.,)))
    criteria=StopCriteria(mode='steady',minimum_t_star=.5,consecutive_checks=3)
    settings=RunSettings(termination=criteria)
    solver=fake_solver();control=RunControl(solver,settings,tmp_path)
    solver.steps=1;control.observe();assert control.streak==0
    solver.steps=2;control.observe();assert control.streak==1
    solver.steps=3;solver.state[0][0,3]+=.1
    control.observe();assert control.streak==0  # z alone must prevent a false stop
    solver.steps=4;control.observe();assert control.streak==1
    arrays={};meta={};save_control(solver,arrays,meta)
    context=control.checkpoint_context();control.close()
    restored=fake_solver();restored.steps=4;restored.state=tuple(a.copy() for a in solver.state)
    restore_control(restored,arrays,meta);restored._checkpoint_context=context
    resumed=RunControl(restored,settings,tmp_path,resume=True)
    restored.steps=5;resumed.observe();assert resumed.stop_reason() is None
    restored.steps=6;resumed.observe();assert resumed.stop_reason()=='residual_targets_met'
    resumed.close()


def test_zero_balance_never_stops_time_dependent_fields(monkeypatch,tmp_path):
    import aerokinetic.convergence as module
    monkeypatch.setattr(module,'conservative_samples',lambda s:(tuple(a.copy() for a in s.state),(1.,)))
    solver=fake_solver();control=RunControl(solver,RunSettings(termination=StopCriteria(mode='steady',minimum_t_star=0,consecutive_checks=1)),tmp_path)
    for step in range(1,6):
        solver.steps=step;solver.state[0][:]+=1
        record=control.observe()
        assert all(record['balance_'+c]==0 for c in ('rho','jx','jy','jz'))
        assert control.stop_reason() is None
    control.close()


def test_step_limit_is_relative_to_run_start_and_wall_limit_is_per_launch(monkeypatch,tmp_path):
    import aerokinetic.convergence as module
    monkeypatch.setattr(module,'conservative_samples',lambda s:(tuple(a.copy() for a in s.state),(1.,)))
    clock=[100.];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    solver=fake_solver();solver.steps=20
    control=RunControl(solver,RunSettings(termination=StopCriteria(max_steps=3,wall_time_limit_s=2)),tmp_path)
    solver.steps=22;assert control.stop_reason() is None
    clock[0]+=3;assert control.stop_reason()=='wall_time_limit'
    solver.steps=23;assert control.stop_reason()=='max_steps_reached'
    control.close()
