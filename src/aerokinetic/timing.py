"""Convert physical-model durations into whole updates without roundoff overshoot."""
import math


def duration_steps(duration,dt):
    """Ceil to cover duration, snapping only roundoff near a positive integer.

    Multiplication by dt and the reverse division can produce k+one_ulp.
    Four quotient ULPs cover this roundoff; an absolute cap of 1e-8 step
    keeps intentionally fractional durations distinct at very large counts.
    A positive substep request always needs at least one update.
    """
    if not math.isfinite(duration)or not math.isfinite(dt)or duration<=0 or dt<=0:
        raise ValueError('Duration and time step must be finite and positive')
    q=duration/dt
    if not math.isfinite(q):raise ValueError('Requested step count overflows')
    nearest=round(q)
    tolerance=min(4*math.ulp(q),1e-8)
    if nearest>=1 and abs(q-nearest)<=tolerance:
        return int(nearest)
    return max(1,math.ceil(q))
