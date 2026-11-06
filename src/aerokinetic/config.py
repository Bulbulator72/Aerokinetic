from dataclasses import dataclass, asdict, field
import math
import numpy as np
from .geometry import Section


@dataclass(frozen=True)
class Atmosphere:
    """ISA sea-level, dry air. Dimensional time additionally needs an actual chord."""
    temperature_k: float = 288.15
    pressure_pa: float = 101325.0
    gas_constant: float = 287.05287
    dynamic_viscosity: float = 1.7894e-5

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("Atmospheric quantities must be finite and positive")

    @property
    def rho(self):
        return self.pressure_pa / (self.gas_constant * self.temperature_k)

    @property
    def nu(self):
        return self.dynamic_viscosity / self.rho

    @property
    def speed_of_sound(self):
        return math.sqrt(1.4 * self.gas_constant * self.temperature_k)


@dataclass(frozen=True)
class Flow:
    reynolds: float = 10000.0
    alpha_deg: float = 0.0
    lattice_speed: float = 0.04
    physical_chord_m: float | None = None
    atmosphere: Atmosphere = field(default_factory=Atmosphere)
    sgs: str = "none"
    smagorinsky_constant: float = 0.10
    perturbation: float = 1e-4
    seed: int = 31

    def __post_init__(self):
        if not 10000 <= self.reynolds <= 100000 or not math.isfinite(self.reynolds):
            raise ValueError("This module targets 10000 <= Re <= 100000")
        if not math.isfinite(self.alpha_deg) or abs(self.alpha_deg) >= 90:
            raise ValueError("Use an angle strictly between -90 and 90 degrees")
        if not 0 < self.lattice_speed <= 0.08:
            raise ValueError("Lattice speed must be in (0,0.08]; local Mach is monitored")
        if self.sgs not in ("none", "smagorinsky"):
            raise ValueError("sgs must be none or smagorinsky")
        if not 0 < self.smagorinsky_constant < 0.3:
            raise ValueError("Smagorinsky constant must be in (0,0.3)")
        if not 0 <= self.perturbation <= 0.01 or not math.isfinite(self.perturbation):
            raise ValueError("Initial perturbation must be in [0,0.01] relative to U")
        if self.physical_chord_m is not None:
            if not math.isfinite(self.physical_chord_m) or self.physical_chord_m <= 0:
                raise ValueError("Physical chord must be positive")
            if self.physical_mach > 0.15:
                raise ValueError("Physical Mach exceeds 0.15: this low-Mach model is unsuitable")

    @property
    def physical_speed(self):
        if self.physical_chord_m is None:
            return None
        return self.reynolds * self.atmosphere.nu / self.physical_chord_m

    @property
    def physical_mach(self):
        return None if self.physical_speed is None else self.physical_speed / self.atmosphere.speed_of_sound


@dataclass(frozen=True)
class Grid:
    cells_per_chord: int = 128
    upstream_chords: float = 6.0
    downstream_chords: float = 12.0
    transverse_chords: float = 6.0
    span_chords: float = 0.5
    memory_limit_gib: float = 8.0
    target: str = "cpu"
    openmp_threads: int = 1

    def __post_init__(self):
        if not isinstance(self.cells_per_chord, int) or self.cells_per_chord < 16:
            raise ValueError("cells_per_chord must be an integer >=16")
        if self.target not in ("cpu", "gpu"):
            raise ValueError("target must be cpu or gpu")
        for key in ("upstream_chords", "downstream_chords", "transverse_chords", "span_chords", "memory_limit_gib"):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be finite and positive")
        if type(self.openmp_threads) is not int or self.openmp_threads < 1:
            raise ValueError("openmp_threads must be a positive integer")


@dataclass(frozen=True)
class Plan:
    shape: tuple[int, int, int]
    origin: tuple[float, float]
    dx: float
    span: float
    viscosity: float
    relaxation_rate: float
    dt_convective: float
    dt_seconds: float | None
    estimated_memory_gib: float
    notes: tuple[str, ...]

    @classmethod
    def create(cls, section: Section, flow: Flow, grid: Grid):
        contours = section.normalized(flow.alpha_deg)
        p = np.concatenate([e.xy for e in contours])
        xmin, ymin = p.min(axis=0)
        xmax, ymax = p.max(axis=0)
        n = grid.cells_per_chord
        origin = (xmin - grid.upstream_chords, ymin - grid.transverse_chords)
        shape = (math.ceil((xmax - xmin + grid.upstream_chords + grid.downstream_chords) * n),
                 math.ceil((ymax - ymin + 2 * grid.transverse_chords) * n),
                 max(8, math.ceil(grid.span_chords * n)))
        # Center rounding slack transversely, preserving reflection symmetry.
        origin = (origin[0], (ymin + ymax) / 2 - shape[1] / (2 * n))
        # Two double-precision D3Q27 fields plus macro fields and flags; allow
        # boundary/index and code-generation overhead. This is an estimate, not an allocation.
        memory = math.prod(v + 2 for v in shape) * ((2 * 27 * 8 + 4 * 8 + 8) * 1.3 + 128) / 2**30
        nu = flow.lattice_speed * n / flow.reynolds
        tau = 0.5 + 3 * nu
        dt = flow.lattice_speed / n
        dt_s = None if flow.physical_speed is None else dt * flow.physical_chord_m / flow.physical_speed
        notes = ["Uniform isotropic grid; no mesh refinement or wall model.",
                 "Accuracy is unvalidated until grid/domain/span/Mach/statistical convergence and tunnel comparison.",
                 "The initial spanwise perturbation is numerical seeding, not a specified tunnel turbulence spectrum."]
        if n < 256:
            notes.append("Fewer than 256 cells/chord: use for development; do not assume a resolved boundary layer.")
        if tau < 0.501:
            notes.append("Relaxation time is close to 0.5; low-viscosity stability and resolution require special checks.")
        if flow.physical_speed is None:
            notes.append("Physical chord unspecified; time is t*=t U/c only, with no invented dimensional seconds.")
        else:
            # Maxwell estimate, using the viscosity-based mean free path.
            mean_free_path = flow.atmosphere.dynamic_viscosity / flow.atmosphere.pressure_pa * math.sqrt(math.pi * flow.atmosphere.gas_constant * flow.atmosphere.temperature_k / 2)
            if mean_free_path / flow.physical_chord_m > 1e-3:
                raise ValueError("Kn > 1e-3: the no-slip low-Kn model needs reconsideration")
        return cls(shape, origin, 1/n, shape[2]/n, nu, 1/tau, dt, dt_s, memory, tuple(notes))

    def as_dict(self):
        return asdict(self)
