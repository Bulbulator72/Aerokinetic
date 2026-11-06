"""Three-dimensional kinetic flow around a fixed extruded section.

No network operations occur on import or during a calculation.
"""
from .geometry import Contour, Section, naca0012, naca4
from .config import Atmosphere, Flow, Grid, Plan
from .project import Project, RunSettings
from .convergence import StopCriteria

__version__ = "0.5.0"
__all__ = ["Contour", "Section", "naca0012", "naca4", "Atmosphere", "Flow", "Grid", "Plan", "Project", "RunSettings", "StopCriteria", "Solver", "RefinedSolver", "RefinedPlan", "RefinementSettings"]


def __getattr__(name):
    if name == "Solver":
        from .solver import Solver
        return Solver
    if name in ("RefinedSolver", "RefinedPlan", "RefinementSettings"):
        from . import refined_solver
        return getattr(refined_solver, name)
    raise AttributeError(name)
