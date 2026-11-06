import numpy as np
import pytest

from aerokinetic.parallel import CPUTransfer
from aerokinetic.refinement import AcousticLevel,D3Q27Transfer


@pytest.mark.integration
@pytest.mark.parametrize('zero_centered',[True,False])
@pytest.mark.parametrize('threads',[1,2])
def test_fused_transfer_matches_reference_and_rejects_invalid_states(zero_centered,threads):
    from lbmpy import Stencil
    from lbmpy.stencils import LBStencil
    stencil=list(LBStencil(Stencil.D3Q27))
    stencil=[stencil[i] for i in np.random.default_rng(19).permutation(27)]
    level=AcousticLevel(1,1,.73,1.1)
    reference=D3Q27Transfer(stencil,level,zero_centered=zero_centered,pdf_stage='pre_collision')
    fused=CPUTransfer(stencil,level,zero_centered=zero_centered,pdf_stage='pre_collision',threads=threads)
    rng=np.random.default_rng(6)
    fields=np.zeros((5,7,3,10));fields[...,0]=rng.uniform(.99,1.01,(5,7,3))
    fields[...,1:4]=rng.uniform(-.035,.035,(5,7,3,3));fields[...,4:]=rng.normal(0,1e-5,(5,7,3,6))
    pdf=reference.populations(fields)
    np.testing.assert_allclose(fused.moments(pdf),reference.moments(pdf),atol=2e-13,rtol=0)
    np.testing.assert_allclose(fused.populations(fields),pdf,atol=2e-13,rtol=0)
    np.testing.assert_allclose(fused.rescale(pdf,to_fine=True),reference.rescale(pdf,to_fine=True),atol=2e-13,rtol=0)
    invalid=fields.copy();invalid[...,4]=-1
    with pytest.raises(ValueError,match='negative physical'):fused.populations(invalid)
    invalid=fields.copy();invalid[...,0]=0
    with pytest.raises(ValueError,match='nonpositive density'):fused.populations(invalid)
    invalid=pdf.copy();invalid.flat[0]=np.nan
    with pytest.raises(ValueError,match='finite'):fused.moments(invalid)
