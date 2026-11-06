import numpy as np
import pytest
from aerokinetic import Contour, Section, naca0012, Flow, Grid, Solver


@pytest.mark.integration
def test_three_dimensional_multi_element_run_with_sgs(tmp_path):
    main = naca0012(31)
    flap = Contour(main.xy * 0.35 + [1.05, -0.20], "flap")
    section = Section((main, flap))
    solver = Solver(section, Flow(reynolds=10000, alpha_deg=3, sgs="smagorinsky"),
                    Grid(cells_per_chord=32, upstream_chords=1, downstream_chords=2,
                         transverse_chords=1, span_chords=0.5, memory_limit_gib=1))
    result = solver.run(0.0125, tmp_path / "multi", sample_every=1, frame_every=5, check_every=1)
    assert result["status"] == "completed"
    assert solver.last_loads["element_lattice_loads"].shape == (2, 4)
    assert np.isfinite(solver.last_loads["element_lattice_loads"]).all()
    assert np.linalg.norm(solver.last_loads["element_lattice_loads"][1]) > 0
    assert solver.diagnostics()["local_mach_max"] < 0.3
