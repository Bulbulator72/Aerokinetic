"""Reporting-point changes preserve the physical seeded initial condition."""
import numpy as np
import pytest

from aerokinetic import Flow,Grid,Section,Solver,naca0012


def make(reference,farfield='legacy'):
    return Solver(Section((naca0012(51),),1.,reference),
        Flow(reynolds=30000,alpha_deg=4,perturbation=.002,seed=31),
        Grid(cells_per_chord=16,upstream_chords=1.5,downstream_chords=3,
             transverse_chords=1.5,span_chords=.5,memory_limit_gib=1,farfield=farfield))


@pytest.mark.integration
@pytest.mark.parametrize('reference',[(.4,-.03),(.1,.05)])
@pytest.mark.parametrize('farfield',['legacy','exterior_regularized_fd_v1'])
def test_seed_and_flow_are_invariant_to_moment_reference(reference,farfield):
    base,other=make((.25,0),farfield),make(reference,farfield)
    assert base.plan.shape==other.plan.shape
    # Geometry and its physical mesh coincide after a pure coordinate shift.
    np.testing.assert_array_equal(base._solid_xy,other._solid_xy)
    for name in ('velocity_data_name','density_data_name'):
        a=base._stepper.data_handling.gather_array(getattr(base._stepper,name))
        b=other._stepper.data_handling.gather_array(getattr(other._stepper,name))
        np.testing.assert_allclose(a,b,atol=2e-14,rtol=0)
    angle=-np.deg2rad(4)
    rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    dx,dy=rotation@(np.array(reference)-(.25,0))
    for _ in range(12):
        a,b=base.step(sample=True),other.step(sample=True)
        np.testing.assert_allclose([b['cl'],b['cd']],[a['cl'],a['cd']],atol=2e-10,rtol=0)
        # Independently transfer torque through the known reporting-point shift.
        assert b['cm']==pytest.approx(a['cm']+dx*a['cl']-dy*a['cd'],abs=2e-10)
