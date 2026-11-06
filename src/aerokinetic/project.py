"""Portable single-point projects, with embedded geometry in a common frame."""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path

from ._io import write_json
from .config import Atmosphere, Flow, Grid, Plan
from .geometry import Contour, Section, naca4, naca0012
from .convergence import StopCriteria


@dataclass(frozen=True)
class RunSettings:
    duration_convective: float = 0.2
    sample_every: int = 10
    frame_every: int = 20
    check_every: int = 10
    average_after: float = 0.1
    checkpoint_every: int = 1000
    termination: StopCriteria = field(default_factory=StopCriteria)

    def __post_init__(self):
        if isinstance(self.termination,dict):
            object.__setattr__(self,'termination',StopCriteria(**self.termination))
        if not isinstance(self.termination,StopCriteria):raise ValueError('termination must contain stop criteria')
        if not math.isfinite(self.duration_convective) or self.duration_convective <= 0:
            raise ValueError("duration_convective must be finite and positive")
        if not math.isfinite(self.average_after) or self.average_after < 0:
            raise ValueError("average_after must be finite and nonnegative")
        for name in ("sample_every", "frame_every", "check_every", "checkpoint_every"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "checkpoint_every" else 1):
                raise ValueError(f"{name} has to be an integer >= {'0' if name == 'checkpoint_every' else '1'}")


@dataclass(frozen=True)
class Project:
    section: Section
    flow: Flow = field(default_factory=Flow)
    grid: Grid = field(default_factory=Grid)
    run: RunSettings = field(default_factory=RunSettings)
    title: str = "NACA0012"

    @classmethod
    def from_dict(cls, data, base_directory=Path(".")):
        if data.get("format", "aerokinetic_project") != "aerokinetic_project" or data.get("schema_version", 1) != 1:
            raise ValueError("Unsupported project format or schema version")
        geometry = data.get("geometry", {})
        if "elements" in geometry:
            elements = tuple(Contour.from_dat(Path(base_directory) / item["file"])
                             if "file" in item else Contour(item["xy"], item.get("name", f"element_{i}"))
                             for i, item in enumerate(geometry["elements"]))
        elif "naca4" in geometry:
            elements = (naca4(**geometry["naca4"]),)
        else:
            elements = (naca0012(**geometry.get("naca0012", {})),)
        flow = dict(data.get("flow", {}))
        flow["atmosphere"] = Atmosphere(**flow.get("atmosphere", {}))
        return cls(Section(elements, geometry.get("chord"), geometry.get("moment_reference")),
                   Flow(**flow), Grid(**data.get("grid", {})), RunSettings(**data.get("run", {})),
                   data.get("title", elements[0].name))

    @classmethod
    def load(cls, path):
        path = Path(path)
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8-sig")), path.resolve().parent)

    def as_dict(self):
        return {"format": "aerokinetic_project", "schema_version": 1, "title": self.title,
                "geometry": {"elements": [{"name": e.name, "xy": e.xy.tolist()} for e in self.section.elements],
                             "chord": self.section.chord, "moment_reference": self.section.moment_reference},
                "flow": asdict(self.flow), "grid": asdict(self.grid), "run": asdict(self.run)}

    def save(self, path):
        write_json(path, self.as_dict())

    def plan(self):
        return Plan.create(self.section, self.flow, self.grid)

    @property
    def case_fingerprint(self):
        # Run duration/outputs and project title do not alter the physical/numerical state.
        data = self.as_dict()
        case = {k: data[k] for k in ("geometry", "flow", "grid")}
        return hashlib.sha256(json.dumps(case, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
