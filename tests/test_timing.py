import math
import pytest

from aerokinetic.timing import duration_steps


def test_integer_step_duration_round_trip_does_not_add_a_step():
    for cells in (16,32,64,96,128,256,1024):
        for speed in (.02,.04,.08):
            dt=speed/cells
            for count in (1,3,5,7,11,17,31,100,999,4800,9600,19200):
                assert duration_steps(count*dt,dt)==count


def test_fractional_requests_are_covered_without_general_rounding_down():
    for count in (1,7,100,1000000):
        dt=.04/32
        assert duration_steps((count+.01)*dt,dt)==count+1
        assert duration_steps((count-.01)*dt,dt)==count
    assert duration_steps(1e-300,1)==1
    # A representable, meaningful excess remains an extra step.
    assert duration_steps((7+1e-6)*.0025,.0025)==8


@pytest.mark.parametrize('duration,dt',[(0,1),(-1,1),(1,0),(1,-1),(math.nan,1),(1,math.inf)])
def test_invalid_step_conversion_rejected(duration,dt):
    with pytest.raises(ValueError,match='positive'):duration_steps(duration,dt)
