import numpy as np
import pytest
from dataclasses import replace

from aerokinetic import Project,RefinementSettings
from aerokinetic.mesh import preview,_lines


def project():
    return Project.from_dict({'grid':{'cells_per_chord':16,'upstream_chords':1,'downstream_chords':2,
                                      'transverse_chords':1,'span_chords':.5,'memory_limit_gib':2},
                              'flow':{'physical_chord_m':.15}})


def test_mesh_counts_match_exact_sampled_fluid_mask_without_3d_allocation():
    p=project();r=preview(p,RefinementSettings())
    origin=r.fine_origin;dx=r.plan.fine_level.dx
    x=origin[0]+(np.arange(r.fine_shape[0])+.5)*dx
    y=origin[1]+(np.arange(r.fine_shape[1])+.5)*dx
    xx,yy=np.meshgrid(x,y,indexing='ij')
    solid=r.contours[0].contains(xx,yy)
    assert r.solid_count==solid.sum()
    assert all(count>0 for count in r.solid_counts.values())
    assert r.fluid_cells==(np.prod(r.plan.shape)-np.prod(r.plan.box.shape)+np.prod(r.fine_shape)-solid.sum()*r.fine_shape[2])
    u=preview(p)
    assert u.fine_shape is None and u.fluid_cells==np.prod(u.plan.shape)-u.solid_count*u.plan.shape[2]


def test_coarse_preview_lines_do_not_cross_covered_fine_interior():
    segments,stride=_lines((0,0),(12,12,8),1,box=(np.array([3,3]),np.array([9,9])))
    assert stride==1
    for first,last in segments:
        middle=(first+last)/2
        assert not np.all((middle>3)&(middle<9))


def test_large_preview_and_guard_are_explicit_and_bounded():
    p=project();r=preview(p,RefinementSettings(),max_body_cells=2)
    assert r.solid_count is None and r.fluid_cells is None and r.solid_centres.size==0
    assert 'не выполнена' in r.note
    with pytest.raises(ValueError,match='guard'):preview(p,RefinementSettings(.05,.05,.05))
