"""Research two-level airfoil solver: fine stationary walls, open coarse domain.

This retains the low-Mach/Re contract of Flow. No SGS or GPU support is implied.
The fine wall exchange, outer PDF flux, and possible Bouzidi mass leakage are
accounted for separately. A small budget residual does not validate wall leakage.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .config import Plan
from .project import Project
from .interface import FineBox, PeriodicRefinedBox, StreamingFaceFlux, _make_lattice
from .refinement import AcousticLevel, prolong_cell_averages
from .walls import StationaryWalls
from ._io import write_json


@dataclass(frozen=True)
class RefinementSettings:
    upstream_chords: float = 0.375
    downstream_chords: float = 0.75
    transverse_chords: float = 0.375
    guard_coarse_cells: int = 4
    coarse_bulk_relaxation_time: float = 1.0

    def __post_init__(self):
        for name in ("upstream_chords", "downstream_chords", "transverse_chords"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Refinement {name} must be finite and positive")
        if type(self.guard_coarse_cells) is not int or self.guard_coarse_cells < 4:
            raise ValueError("At least four coarse cells must separate every wall from the fine interface")
        if not math.isfinite(self.coarse_bulk_relaxation_time) or self.coarse_bulk_relaxation_time <= .5:
            raise ValueError("Coarse bulk relaxation time must be finite and above 0.5")


@dataclass(frozen=True)
class RefinedPlan:
    shape: tuple
    origin: tuple
    box: FineBox
    coarse_level: AcousticLevel
    fine_level: AcousticLevel
    span: float
    dt_seconds: float | None
    estimated_memory_gib: float
    minimum_wall_interface_distance_coarse_cells: float
    notes: tuple

    @classmethod
    def create(cls, project, refinement):
        if project.grid.target != "cpu":
            raise ValueError("The refined solver currently requires CPU")
        if project.flow.sgs != "none":
            raise ValueError("Refined SGS is not implemented; select sgs=none explicitly")
        base = project.plan()
        points = np.concatenate([e.xy for e in project.section.normalized(project.flow.alpha_deg)])
        lower, upper = points.min(axis=0), points.max(axis=0)
        margins_low = np.array([refinement.upstream_chords, refinement.transverse_chords])
        margins_high = np.array([refinement.downstream_chords, refinement.transverse_chords])
        lo = np.floor((lower - margins_low - base.origin) / base.dx + 1e-12).astype(int)
        hi = np.ceil((upper + margins_high - base.origin) / base.dx - 1e-12).astype(int)
        if np.any(lo < 1) or np.any(hi > np.array(base.shape[:2]) - 1):
            raise ValueError("The refined box must fit strictly inside the external x/y domain")
        distances = np.r_[(lower - (base.origin + lo * base.dx)) / base.dx,
                          ((base.origin + hi * base.dx) - upper) / base.dx]
        if distances.min() < refinement.guard_coarse_cells - 1e-10:
            raise ValueError("Wall/interface guard too small; enlarge the refinement margins")
        box = FineBox((*map(int, lo), 0), (*map(int, hi), base.shape[2]))
        coarse = AcousticLevel(base.dx, base.dt_convective, 1 / base.relaxation_rate,
                               refinement.coarse_bulk_relaxation_time)
        fine = coarse.refined()
        padded = tuple(2 * n + 4 for n in box.shape)
        # Includes the library's extra source ghost layer, snapshots, moment
        # temporaries, flags/indices and headroom for symbolic/JIT work.
        cells = math.prod(n + 2 for n in base.shape) + math.prod(n + 2 for n in padded)
        memory = (cells * (8 * 27 * 8 + 4 * 10 * 8 + 4 * 8 + 128) + 256 * 2**20) / 2**30
        return cls(base.shape, base.origin, box, coarse, fine, base.span, base.dt_seconds, memory,
                   float(distances.min()),
                   ("Experimental static two-level CPU grid; fine cells/chord = 2 * coarse cells/chord.",
                    "All walls on the fine level; CPU OpenMP, no SGS, GPU, wall model or compressible-energy model.",
                    "Completed physical duration is not numerical/statistical or tunnel convergence.",
                    "Bulk relaxation is an explicit numerical parameter; preserve common bulk viscosity when comparing meshes/time steps.",
                    "Bouzidi wall mass exchange is reported separately; impermeability accuracy needs grid studies."))

    def as_dict(self):
        data = asdict(self)
        data.update(dx=self.coarse_level.dx, dt_convective=self.coarse_level.dt,
                    coarse_cells_per_chord=round(1/self.coarse_level.dx),
                    fine_cells_per_chord=round(1/self.fine_level.dx),
                    fine_shape=tuple(2 * n for n in self.box.shape))
        return data


def refined_project_dict(project, refinement):
    return {"format": "aerokinetic_refined_project", "schema_version": 1,
            "project": project.as_dict(), "refinement": asdict(refinement)}


def load_refined_project(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("format") != "aerokinetic_refined_project" or data.get("schema_version") != 1:
        raise ValueError("This command requires an aerokinetic_refined_project, not a uniform project")
    return Project.from_dict(data["project"], path.resolve().parent), RefinementSettings(**data["refinement"])


class RefinedSolver(PeriodicRefinedBox):
    def __init__(self, project: Project, refinement=RefinementSettings(), progress=None):
        self.project, self.refinement = project, refinement
        self.section, self.flow, self.grid = project.section, project.flow, project.grid
        self.plan = RefinedPlan.create(project, refinement)
        if self.plan.estimated_memory_gib > self.grid.memory_limit_gib:
            raise MemoryError(f"Estimated {self.plan.estimated_memory_gib:.2f} GiB exceeds the explicit limit {self.grid.memory_limit_gib:.2f} GiB")
        self.contours = self.section.normalized(self.flow.alpha_deg)
        self.coarse_boundary_calls = self.fine_boundary_calls = 0
        self.cumulative_wall_mass_exchange = 0.
        self.cumulative_wall_impulse = np.zeros(3)
        self.cumulative_external_in = np.zeros(4)
        self.last_loads = None
        self.last_budget = {}
        super().__init__(self.plan.shape, self.plan.box, self.plan.coarse_level,
                         memory_limit_gib=self.grid.memory_limit_gib, progress=progress)
        self._free_fields = np.zeros(10)
        self._free_fields[0] = 1
        self._free_fields[1] = self.flow.lattice_speed
        self._free_pdf = self.transfer.populations(self._free_fields)
        # Only the three fluid rows needed by interface prediction are copied
        # from the fine level. The remaining covered coarse interior is virtual.
        core = np.ones(self.box.shape, bool)
        for axis in (0, 1):
            band = np.zeros(self.box.shape[axis], bool)
            band[3:-3] = True
            reshape = [1, 1, 1]
            reshape[axis] = len(band)
            core &= band.reshape(reshape)
        self._virtual_core = core
        self._fine_virtual_core = np.repeat(np.repeat(np.repeat(core, 2, axis=0), 2, axis=1), 2, axis=2)
        self._install_outer()
        origin = self._fine_origin(padded=True)
        self.walls = StationaryWalls(self._fine, self.contours, origin=origin, dx=self.fine_level.dx,
                                     owned_slices=self.fine_slices, prefix="fine_solid")
        xyz = self.coordinates(self.fine_shape, self.fine_level.dx, self._fine_origin())
        self._fine_solid_xy = np.zeros(self.fine_shape[:2], bool)
        for contour in self.contours:
            mask = contour.contains(xyz[0][:, :, 0], xyz[1][:, :, 0])
            if not mask.any():
                raise ValueError(f"Element {contour.name} has no fine solid cells; increase resolution")
            self._fine_solid_xy |= mask
        self.fine_fluid = np.broadcast_to(~self._fine_solid_xy[:, :, None], self.fine_shape)
        if np.any(~self.fine_fluid & ~self._fine_virtual_core):
            raise ValueError("A solid intersects the fluid donor band")
        self._boundaries_initialized = False
        self._make_outer_flux_samplers()
        self.initialize_flow()

    @property
    def case_fingerprint(self):
        data = {"project_case": self.project.case_fingerprint, "refinement": asdict(self.refinement)}
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _create_lattice(self, shape, level, *, coarse_grid):
        return _make_lattice(shape, level, periodicity=(False, False, True) if coarse_grid else True,
                             threads=self.grid.openmp_threads)

    def _create_transfer(self, stencil):
        from .parallel import CPUTransfer
        return CPUTransfer(stencil,self.coarse_level,zero_centered=True,pdf_stage="pre_collision",
                           threads=self.grid.openmp_threads)

    def _fine_origin(self, padded=False):
        return tuple(self.plan.origin[a] + self.box.lower[a] * self.coarse_level.dx
                     - (self.pad * self.fine_level.dx if padded else 0) for a in (0, 1)) + (
                         -self.pad * self.fine_level.dx if padded else 0.,)

    def _restrict_fine(self, fine_pdf):
        safe = fine_pdf.copy()
        safe[self._fine_virtual_core] = self._free_pdf
        result = self.transfer.restrict(safe)
        result[self._virtual_core] = self._free_pdf
        return result

    def _donor_block(self, coarse_pdf):
        safe = coarse_pdf.copy()
        covered = safe[self.box.slices]
        covered[self._virtual_core] = self._free_pdf
        return super()._donor_block(safe)

    def _fine_donors(self, before, after, fraction):
        a, b = self.transfer.moments(before), self.transfer.moments(after)
        fields = prolong_cell_averages((1 - fraction) * a + fraction * b)
        fields = self.transfer._rescale_stress(fields, self.coarse_level, self.fine_level)
        # These are not donors: actual physical fine PDFs replace them before
        # collision. Do not interpret or reconstruct a solid's fictitious fluid.
        fields[self.fine_slices] = self._free_fields
        return self.transfer.populations(fields)

    def _install_outer(self):
        from lbmpy.boundaries import UBB, ExtrapolationOutflow
        nx, ny, nz = self.shape
        velocity = (self.flow.lattice_speed, 0., 0.)
        self._coarse.boundary_handling.set_boundary(UBB(velocity, name="coarse_freestream"),
            mask_callback=lambda x, y, z: (x < 0) | (y < 0) | (y > ny))
        self._outflow = ExtrapolationOutflow((1, 0, 0), self._coarse.method,
                                             initial_density=1., initial_velocity=velocity)
        self._coarse.boundary_handling.set_boundary(self._outflow, mask_callback=lambda x, y, z: x > nx)

    def _make_outer_flux_samplers(self):
        shape = tuple(n + 2 for n in self.shape)
        self._outer_samplers = []
        for axis in range(3):
            ranges = [(1, self.shape[a] + 1) for a in range(3) if a != axis]
            for side, index in ((-1, 0), (1, self.shape[axis])):
                sampler = StreamingFaceFlux(shape, self.transfer.stencil, axis, index, ranges, periodic=False)
                self._outer_samplers.append((axis, side, sampler))

    def initialize_flow(self):
        self._run_control=None
        # Outflow history reads the initial fluid PDFs. Populate both arrays,
        # including ghosts, BEFORE preparing/reinitializing boundary data.
        for lbm in (self._coarse, self._fine):
            for name in (lbm._pdf_arr_name, lbm._tmp_arr_name):
                lbm.data_handling.cpu_arrays[name][...] = self._free_pdf
        rng = np.random.default_rng(self.flow.seed)
        phase_y, phase_z = rng.uniform(0, 2 * np.pi, 2)
        ky = 2 * np.pi / (self.shape[1] * self.coarse_level.dx)
        kz = 2 * np.pi / self.plan.span
        original = np.concatenate([e.xy for e in self.section.elements])
        leading = original[np.argmin(original[:,0])]
        angle = -np.deg2rad(self.flow.alpha_deg)
        rotation = np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
        leading = rotation @ ((leading-np.array(self.section.moment_reference))/self.section.chord)
        def initial(x, y, z, dx):
            xx, yy = x + self.plan.origin[0], y + self.plan.origin[1]
            relative_x, relative_y = xx-leading[0], yy-leading[1]
            shape = np.broadcast_shapes(x.shape, y.shape, z.shape)
            j = np.zeros((*shape, 3))
            j[..., 0] = self.flow.lattice_speed
            amplitude = self.flow.perturbation * self.flow.lattice_speed
            envelope = np.exp(-((relative_x - 0.5) / 0.75)**2)
            j[..., 1] = amplitude * np.sin(ky * relative_y + phase_y) * np.cos(kz * z + phase_z) * envelope
            j[..., 2] = -amplitude * ky / kz * np.cos(ky * relative_y + phase_y) * np.sin(kz * z + phase_z) * envelope
            solid = np.zeros(shape[:2], bool)
            for contour in self.contours:
                solid |= contour.contains(xx[:, :, 0], yy[:, :, 0])
            j[np.broadcast_to(solid[:, :, None], shape)] = 0.
            return 1., j
        super().initialize(initial)
        if self._boundaries_initialized:
            self.walls.fallback_links = 0
            self._coarse.boundary_handling.trigger_reinitialization_of_boundary_data()
            self._fine.boundary_handling.trigger_reinitialization_of_boundary_data()
        else:
            self._coarse.boundary_handling.prepare()
            self._fine.boundary_handling.prepare()
            self._boundaries_initialized = True
        self.coarse_boundary_calls = self.fine_boundary_calls = 0
        self.cumulative_wall_mass_exchange = 0.
        self.cumulative_wall_impulse = np.zeros(3)
        self.cumulative_external_in = np.zeros(4)
        self.last_loads, self.last_budget = None, {}
        self._initial_integrals = self.conserved_integrals()

    def _stream(self, lbm):
        lbm._sync_src()
        lbm.boundary_handling(**lbm.kernel_params)
        if lbm is self._coarse:
            self.coarse_boundary_calls += 1
            post = lbm.data_handling.cpu_arrays[lbm.pdf_array_name]
            self._interval_external_in = np.zeros(4)
            for axis, side, sampler in self._outer_samplers:
                self._interval_external_in -= side * sampler(post).sum(axis=(0, 1)) * self.coarse_level.dx**3
        else:
            self.fine_boundary_calls += 1
            self._interval_wall.append(self.walls.measure())
        lbm.data_handling.run_kernel(lbm._lbmKernels[1], **lbm.kernel_params)
        lbm.data_handling.swap(lbm._pdf_arr_name, lbm._tmp_arr_name, lbm._gpu)

    def _physical_views(self):
        coarse = self._coarse.data_handling.cpu_arrays[self._coarse.pdf_array_name][1:-1, 1:-1, 1:-1]
        fine_all = self._fine.data_handling.cpu_arrays[self._fine.pdf_array_name][1:-1, 1:-1, 1:-1]
        return coarse, fine_all[self.fine_slices]

    def conserved_integrals(self):
        mass, impulse = 0., np.zeros(3)
        for pdf, owned, level in zip(self._physical_views(), (self.coarse_owned, self.fine_fluid),
                                      (self.coarse_level, self.fine_level)):
            rho = pdf.sum(axis=-1) + 1
            j = pdf @ self.transfer.stencil
            mass += rho[owned].sum() * level.dx**3
            impulse += j[owned].sum(axis=0) * level.dx**3 * level.lattice_velocity_unit
        return {"mass": float(mass), "momentum": impulse.tolist()}

    def leaf_moments(self):
        coarse, fine = self.states()
        safe_fine = fine.copy()
        safe_fine[~self.fine_fluid] = self._free_pdf
        coarse_origin = (*self.plan.origin, 0.)
        return ((self.transfer.moments(coarse), self.coordinates(self.shape, self.coarse_level.dx, coarse_origin),
                 self.coarse_owned, self.coarse_level.dx),
                (self.transfer.moments(safe_fine), self.coordinates(self.fine_shape, self.fine_level.dx, self._fine_origin()),
                 self.fine_fluid, self.fine_level.dx))

    def step(self):
        before = self.conserved_integrals()
        self._interval_wall = []
        super().step()
        if len(self._interval_wall) != 2:
            self.initialized = False
            raise RuntimeError("Each coarse step requires exactly two fine wall transfers")
        volume = self.fine_level.dx**3
        velocity_unit = self.fine_level.lattice_velocity_unit
        impulse = sum((w["force_lattice"] for w in self._interval_wall), np.zeros(3)) * volume * velocity_unit
        moment_impulse = sum(w["moment_z_common_lattice"] for w in self._interval_wall) * volume * velocity_unit
        wall_mass = sum(w["mass_exchange_lattice"] for w in self._interval_wall) * volume
        q_area = 0.5 * self.plan.span  # common c=1 and freestream common speed=1
        force = impulse / self.coarse_level.dt
        moment = moment_impulse / self.coarse_level.dt
        record = {"step": self.steps, "t_star": (self.steps - 0.5) * self.coarse_level.dt,
                  "cl": float(force[1] / q_area), "cd": float(force[0] / q_area), "cm": float(-moment / q_area)}
        if self.plan.dt_seconds is not None:
            record["time_s"] = (self.steps - 0.5) * self.plan.dt_seconds
        after = self.conserved_integrals()
        external = self._interval_external_in.copy()
        external[1:] *= velocity_unit
        mass_residual = after["mass"] - before["mass"] - external[0] - wall_mass
        momentum_residual = np.array(after["momentum"]) - before["momentum"] - external[1:] + impulse
        reference_volume = (np.count_nonzero(self.coarse_owned) * self.coarse_level.dx**3
                            + np.count_nonzero(self.fine_fluid) * self.fine_level.dx**3)
        self.last_budget = {"mass_residual_per_volume": float(mass_residual / reference_volume),
                            "momentum_residual_per_volume": (momentum_residual / reference_volume).tolist(),
                            "wall_mass_exchange": float(wall_mass), "external_mass_in": float(external[0]),
                            "wall_impulse": impulse.tolist(), "external_impulse_in": external[1:].tolist()}
        self.cumulative_wall_mass_exchange += wall_mass
        self.cumulative_wall_impulse += impulse
        self.cumulative_external_in += external
        self.last_loads = record
        return record

    def diagnostics(self):
        mach, density_error = 0., 0.
        for fields, _, owned, dx in self.leaf_moments():
            rho = fields[..., 0][owned]
            velocity = fields[..., 1:4][owned] / rho[:, None]
            mach = max(mach, float(np.linalg.norm(velocity, axis=-1).max() * math.sqrt(3)))
            density_error = max(density_error, float(np.max(abs(rho - 1))))
        if mach > 0.3 or density_error > 0.1:
            raise RuntimeError(f"Refined low-Mach state invalid: max Ma={mach:.5g}, density deviation={density_error:.5g}")
        return {"local_mach_max": mach, "density_error_max": density_error,
                "wall_fallback_links_including_donors": self.walls.fallback_links,
                "coarse_boundary_calls": self.coarse_boundary_calls, "fine_boundary_calls": self.fine_boundary_calls,
                "cumulative_wall_mass_exchange": self.cumulative_wall_mass_exchange,
                "cumulative_wall_mass_exchange_over_initial_mass": self.cumulative_wall_mass_exchange / self._initial_integrals["mass"],
                "last_budget": self.last_budget}

    def save_checkpoint(self, path, context=None):
        from .refined_checkpoint import save_checkpoint
        return save_checkpoint(self, path, context)

    @classmethod
    def from_checkpoint(cls, path, progress=None):
        from .refined_checkpoint import restore_checkpoint
        return restore_checkpoint(cls, path, progress)

    def run(self, output, **kwargs):
        from .refined_run import run
        return run(self, output, **kwargs)

    @classmethod
    def resume_run(cls, output, **kwargs):
        from .refined_run import resume
        return resume(cls, output, **kwargs)
