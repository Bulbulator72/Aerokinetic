"""Run with aero2d and aerokinetic installed in the same environment.

The coarse grid below is an integration example, not an accuracy prescription.
"""
from aero2d.geometry import naca4
from aero2d.multi import highlift
from aerokinetic import Section, Flow, Grid, Plan, Solver

profile = naca4("0012", n_side=121)
elements = highlift(profile, flap={"type": "slotted", "chord": 0.3,
                                  "deflection": 20, "gap": 0.04, "overlap": 0.0})
section = Section.from_aero2d(elements)
flow = Flow(reynolds=10000, alpha_deg=4, physical_chord_m=0.15)
grid = Grid(cells_per_chord=128, memory_limit_gib=8)
print(Plan.create(section, flow, grid).as_dict())
# Select a grid that resolves the gap, wall layers and wake, and set a memory
# limit that your hardware can actually provide before creating the Solver.
# solver = Solver(section, flow, grid)
# solver.run(100, "multi_run", average_after=30)
