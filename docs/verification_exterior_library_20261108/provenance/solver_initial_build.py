"""lbmpy 1.4 adapter. The numerical collision and wall rules are upstream code.

The pinned boundary index API is also used for link-resolved force and torque.
In particular, loads never reapply the history-dependent outflow boundary.
"""
from dataclasses import asdict
from pathlib import Path
import csv
import json
import math
import os
import shutil
import time
import uuid
import numpy as np

from .config import EXTERIOR_FARFIELD, Flow, Grid, Plan
from .geometry import Section
from ._io import atomic_binary, write_json, file_hash
from .project import Project, RunSettings


class NumericalFailure(RuntimeError):
    pass


def coefficients(force, moment_z, speed, chord_cells, span_cells):
    """Force on solid; +Cl upward, +Cd downstream, +Cm nose up."""
    q_area = 0.5 * speed**2 * chord_cells * span_cells
    return float(force[1] / q_area), float(force[0] / q_area), float(-moment_z / (q_area * chord_cells))


class Solver:
    def __init__(self, section: Section, flow: Flow = Flow(), grid: Grid = Grid(), progress=None):
        self.section, self.flow, self.grid = section, flow, grid
        self.plan = Plan.create(section, flow, grid)
        self._exterior = None
        self._padding = (1, 1, 0) if grid.farfield == EXTERIOR_FARFIELD else (0, 0, 0)
        self._storage_shape = tuple(n + 2*p for n, p in zip(self.plan.shape, self._padding))
        self._storage_origin = tuple(x - p*self.plan.dx for x, p in zip(self.plan.origin, self._padding[:2]))
        self._core_slice = tuple(slice(p, p+n) for p, n in zip(self._padding, self.plan.shape))
        self._wall_mass_exchange = 0.0
        self._wall_mass_total = 0.0
        self.contours = section.normalized(flow.alpha_deg)
        if self.plan.estimated_memory_gib > grid.memory_limit_gib:
            raise MemoryError(f"Estimated {self.plan.estimated_memory_gib:.2f} GiB exceeds the explicit "
                              f"limit {grid.memory_limit_gib:.2f} GiB. Inspect Plan before choosing a larger limit.")
        self._progress = progress or (lambda message: None)
        self._progress("Generating and compiling D3Q27 cumulant kernels")
        self._build()
        self.steps = 0
        self.wall_fallback_links = 0
        self._install_boundaries()
        self._initialize()
        self._stepper.pre_run()
        self.last_loads = None

    def _build(self):
        import sympy as sp
        from lbmpy import LBMConfig, Method, Stencil, SubgridScaleModel
        from lbmpy.stencils import LBStencil
        from lbmpy.lbstep import LatticeBoltzmannStep
        from pystencils import CreateKernelConfig, Target

        if self.grid.target == "gpu":
            try:
                import cupy
                if cupy.cuda.runtime.getDeviceCount() < 1:
                    raise RuntimeError("No CUDA device")
            except (ImportError, RuntimeError) as error:
                raise RuntimeError("GPU target requires CuPy, a CUDA device and its runtime; use target=cpu otherwise") from error
        # Symbolic omega lets LES adapt the local viscosity without changing molecular Re.
        omega = sp.Symbol("omega")
        sgs = None if self.flow.sgs == "none" else (SubgridScaleModel.SMAGORINSKY, self.flow.smagorinsky_constant)
        lb_config = LBMConfig(stencil=LBStencil(Stencil.D3Q27), method=Method.CUMULANT,
                              relaxation_rate=omega, compressible=True, zero_centered=True,
                              subgrid_scale_model=sgs)
        config = CreateKernelConfig(target=Target.CPU if self.grid.target == "cpu" else Target.GPU,
                                    data_type="float64",
                                    cpu_openmp=self.grid.openmp_threads if self.grid.openmp_threads > 1 else False)
        order = "collide_stream" if self.grid.farfield == EXTERIOR_FARFIELD else "stream_collide"
        self._stepper = LatticeBoltzmannStep(domain_size=self._storage_shape, periodicity=(False, False, True),
                                             time_step_order=order,
                                             lbm_config=lb_config, config=config,
                                             kernel_params={"omega": self.plan.relaxation_rate})
        if self.grid.farfield == EXTERIOR_FARFIELD:
            from .exterior import ExteriorBoundary
            self._exterior = ExteriorBoundary(self._stepper, self.plan.shape, (False, False, True),
                                              (self.flow.lattice_speed, 0.0, 0.0))
        self._stencil = np.array(self._stepper.method.stencil, dtype=np.int64)
        self._inverse = np.array([self._stepper.method.stencil.index(tuple(-e)) for e in self._stencil])

    def _mask(self, contour):
        def mask(x, y, z):
            inside = contour.contains(x[:, :, 0] * self.plan.dx + self._storage_origin[0],
                                      y[:, :, 0] * self.plan.dx + self._storage_origin[1])
            return np.broadcast_to(inside[:, :, None], x.shape)
        return mask

    def _distance_callback(self, contour):
        def callback(data, **_):
            # Deduplicate extruded links: geometry is independent of z and e_z.
            # SerialBlock.offset already includes the ghost-layer shift in 1.4.
            # Use the same coordinates as Block.midpoint_arrays; subtracting the
            # ghost count a second time would move curved walls by one full cell.
            xy = np.column_stack([data.index_array[axis] + data.offset[i] + 0.5
                                  for i, axis in enumerate(("x", "y"))])
            directions = data.link_offsets()[:, :2]
            keys, inv = np.unique(np.column_stack((xy, directions)), axis=0, return_inverse=True)
            points = keys[:, :2] * self.plan.dx + self._storage_origin
            offsets = keys[:, 2:] * self.plan.dx
            q = contour.link_fraction(points, offsets)
            # The q<1/2 branch requires another fluid cell away from the wall.
            other = points - offsets
            blocked = np.zeros(len(points), dtype=bool)
            for element in self.contours:
                blocked |= element.contains(other[:, 0], other[:, 1])
            fallback = (q < 0.5) & blocked
            q[fallback] = -1.0  # Upstream's documented halfway fallback.
            data["q"] = q[inv]
            self.wall_fallback_links += int(np.sum(fallback[inv]))
        return callback

    def _install_boundaries(self):
        from lbmpy.boundaries import NoSlipLinearBouzidi, UBB, ExtrapolationOutflow
        step = self._stepper
        bh = step.boundary_handling
        nx, ny, nz = self._storage_shape
        freestream = (self.flow.lattice_speed, 0.0, 0.0)
        if self.grid.farfield == "legacy":
            outer = UBB(freestream, name="freestream")
            bh.set_boundary(outer, mask_callback=lambda x, y, z: (x < 0) | (y < 0) | (y > ny))
            outflow = ExtrapolationOutflow((1, 0, 0), step.method,
                                          initial_density=1.0, initial_velocity=freestream)
            bh.set_boundary(outflow, mask_callback=lambda x, y, z: x > nx)
        elif self._exterior is None:
            from .farfield import install_characteristic_farfield
            install_characteristic_farfield(step,self.plan.shape,(False,False,True),freestream)
        self._wall_objects = []
        for i, contour in enumerate(self.contours):
            wall = NoSlipLinearBouzidi(name=f"solid_{i}", init_wall_distance=self._distance_callback(contour))
            bh.set_boundary(wall, mask_callback=self._mask(contour))
            self._wall_objects.append(wall)

        x = (np.arange(nx) + 0.5) * self.plan.dx + self._storage_origin[0]
        y = (np.arange(ny) + 0.5) * self.plan.dx + self._storage_origin[1]
        self._solid_xy = np.zeros((nx, ny), dtype=bool)
        for e in self.contours:
            mask = e.contains(x[:, None], y[None, :])
            if not mask.any():
                raise ValueError(f"Element {e.name} has no solid cells; increase resolution")
            self._solid_xy |= mask
        self._storage_fluid = np.broadcast_to(~self._solid_xy[:, :, None], self._storage_shape)
        self._solid_xy = self._solid_xy[self._core_slice[:2]]
        self._fluid = self._storage_fluid[self._core_slice]

    def _initialize(self):
        self._run_control=None
        step, plan = self._stepper, self.plan
        dh = step.data_handling
        if self.grid.farfield != "legacy":
            # The upstream macro setter writes owned cells only. Unused ghost
            # populations in its empty-allocated buffers otherwise contain
            # allocator history and spoil reproducible full-state checkpoints.
            for name in (step.pdf_array_name,step._tmp_arr_name):
                dh.cpu_arrays[name].fill(0)
        rng = np.random.default_rng(self.flow.seed)
        phase_y, phase_z = rng.uniform(0, 2 * np.pi, 2)
        # A reporting-point change must not move the physical initial seed.
        # The computational coordinates are centred on moment_reference, so
        # transform the leading edge into that frame before evaluating it.
        original = np.concatenate([e.xy for e in self.section.elements])
        leading = original[np.argmin(original[:, 0])]
        angle = -np.deg2rad(self.flow.alpha_deg)
        rotation = np.array([[np.cos(angle), -np.sin(angle)],
                             [np.sin(angle), np.cos(angle)]])
        leading = rotation @ ((leading - np.asarray(self.section.moment_reference)) / self.section.chord)
        for block in dh.iterate(ghost_layers=True):
            x, y, z = block.midpoint_arrays
            u = block[step.velocity_data_name]
            if self._exterior is not None:
                block[step.density_data_name][...] = 1.0
            u[..., 0] = self.flow.lattice_speed
            yy = y * plan.dx + self._storage_origin[1]
            xx = x * plan.dx + self._storage_origin[0]
            relative_x, relative_y = xx - leading[0], yy - leading[1]
            zz = z * plan.dx
            ky = 2 * np.pi / (plan.shape[1] * plan.dx)
            kz = 2 * np.pi / plan.span
            amp = self.flow.perturbation * self.flow.lattice_speed
            envelope = np.exp(-((relative_x - 0.5) / 0.75) ** 2)
            u[..., 1] = amp * np.sin(ky * relative_y + phase_y) * np.cos(kz * zz + phase_z) * envelope
            u[..., 2] = -amp * ky / kz * np.cos(ky * relative_y + phase_y) * np.sin(kz * zz + phase_z) * envelope
            solid = np.zeros(x.shape[:2], dtype=bool)
            for e in self.contours:
                solid |= e.contains(xx[:, :, 0], yy[:, :, 0])
            u[np.broadcast_to(solid[:, :, None], x.shape)] = 0.0
        if self._exterior is not None:
            owned = np.zeros(tuple(n+2 for n in self._storage_shape), dtype=bool)
            owned[tuple(slice(1+p, 1+p+n) for p, n in zip(self._padding, plan.shape))] = True
            dh.cpu_arrays[step.velocity_data_name][~owned] = (self.flow.lattice_speed, 0.0, 0.0)
        step.set_pdf_fields_from_macroscopic_values()

    def _loads(self):
        """Vectorized momentum exchange, after boundary reflection and before streaming.

        Pull streaming: outgoing is f(x,d), incoming is f(x+e_d,opposite(d)).
        The intersection lies along e_d, so its lever arm gives exactly the
        same torque as the fluid-node lever arm. Background weights cancel
        over each closed extruded solid in the zero-centered representation.
        """
        step = self._stepper
        dh = step.data_handling
        bh = step.boundary_handling
        per_element = np.zeros((len(self.contours), 4))
        wall_mass = 0.0
        for block in dh.iterate(ghost_layers=1):
            index_lists = block[bh._index_array_name].boundary_object_to_index_list
            pdf = dh.gpu_arrays[step.pdf_array_name] if self.grid.target == "gpu" else block[step.pdf_array_name]
            for i, wall in enumerate(self._wall_objects):
                ind = index_lists.get(wall)
                if ind is None or len(ind) == 0:
                    continue
                d = ind["dir"].astype(np.int64)
                e = self._stencil[d]
                pos = [ind[axis].astype(np.int64) for axis in ("x", "y", "z")]
                outgoing = pdf[(*pos, d)]
                incoming = pdf[(*(pos[k] + e[:, k] for k in range(3)), self._inverse[d])]
                values = outgoing + incoming
                if self._exterior is not None:
                    # Opposite-direction background weights cancel in this
                    # difference; verify against complete-PDF mask fluxes.
                    wall_mass += float(np.sum(incoming - outgoing))
                if self.grid.target == "gpu":
                    values = values.get()
                link_force = e * np.asarray(values)[:, None]
                force = link_force.sum(axis=0)
                # Serial data handling; block offset is retained explicitly.
                rx = pos[0] + block.offset[0] + 0.5 + self._storage_origin[0] / self.plan.dx
                ry = pos[1] + block.offset[1] + 0.5 + self._storage_origin[1] / self.plan.dx
                mz = np.sum(rx * link_force[:, 1] - ry * link_force[:, 0])
                per_element[i, :3] += force
                per_element[i, 3] += mz
        total = per_element.sum(axis=0)
        cl, cd, cm = coefficients(total[:3], total[3], self.flow.lattice_speed,
                                  self.grid.cells_per_chord, self.plan.shape[2])
        result = {"cl": cl, "cd": cd, "cm": cm, "element_lattice_loads": per_element}
        if self._exterior is not None:
            result["wall_mass_exchange_lattice"] = wall_mass
        return result

    def step(self, sample=False):
        step = self._stepper
        # Reusing the upstream ordering avoids an extra application of the outflow
        # history when obtaining loads. time_step() alone cannot expose this stage.
        if self._exterior is None:
            step._sync_src()
            step.boundary_handling(**step.kernel_params)
            loads = self._loads() if sample else None
            step.data_handling.run_kernel(step._lbmKernels[0], **step.kernel_params)
            step.data_handling.swap(step._pdf_arr_name, step._tmp_arr_name, step._gpu)
        else:
            self._exterior.step(before_stream=self._loads)
            measured = self._exterior.last_loads
            self._wall_mass_exchange = measured["wall_mass_exchange_lattice"]
            self._wall_mass_total += self._wall_mass_exchange
            loads = measured if sample else None
            if loads is not None:
                loads.update(wall_mass_exchange_accumulated=self._wall_mass_total,
                    exterior_source_mass_lattice=self._exterior.last_report["exterior_source_mass_momentum"][0],
                    exterior_source_mass_accumulated=float(self._exterior.source_total[0]))
        self.steps += 1
        step.time_steps_run = self.steps
        if loads is not None:
            loads.update(step=self.steps, t_star=(self.steps - 0.5) * self.plan.dt_convective)
            if self.plan.dt_seconds is not None:
                loads["time_s"] = (self.steps - 0.5) * self.plan.dt_seconds
            self.last_loads = loads
        return loads

    def diagnostics(self):
        step = self._stepper
        step.post_run()
        dh = step.data_handling
        rho = dh.gather_array(step.density_data_name)
        velocity = dh.gather_array(step.velocity_data_name)
        r = rho[self._storage_fluid]
        v = velocity[self._storage_fluid]
        if not np.isfinite(r).all() or not np.isfinite(v).all() or not np.isfinite(r).any():
            raise NumericalFailure("Non-finite fluid state; the run is invalid")
        local_mach = float(np.sqrt(3) * np.sqrt(np.sum(v * v, axis=-1)).max())
        density_error = float(np.max(abs(r - 1.0)))
        if local_mach > 0.3 or density_error > 0.1 or r.min() <= 0:
            raise NumericalFailure(f"Low-Mach validity check failed: local Ma={local_mach:.3g}, |rho-1|max={density_error:.3g}")
        physical_density = rho[self._core_slice][self._fluid]
        return {"local_mach_max": local_mach, "density_mean": float(physical_density.mean()),
                "density_error_max": density_error, "wall_fallback_links": self.wall_fallback_links}

    def boundary_diagnostics(self):
        """Numerical exchanges, in lattice mass per step, not physical errors."""
        if self._exterior is None:
            return None
        return {"model": EXTERIOR_FARFIELD, "pdf_stage": "pre_collision",
                "physical_shape": self.plan.shape, "storage_shape": self._storage_shape,
                "storage_origin": self._storage_origin,
                "wall_mass_exchange_lattice": self._wall_mass_exchange,
                "wall_mass_exchange_accumulated": self._wall_mass_total,
                "exterior_source_mass_momentum_accumulated": self._exterior.source_total.tolist(),
                "last_exterior_step": self._exterior.last_report,
                "definition": "Wall incoming-minus-outgoing mass and explicit auxiliary PDF source; a closed bookkeeping budget does not prove zero wall mass flux or accuracy."}

    def save_frame(self, path, max_pixels=800):
        step = self._stepper
        step.post_run()
        nx, ny, nz = self.plan.shape
        stride = max(1, math.ceil(max(nx, ny) / max_pixels))
        if self._exterior is None:
            velocity = step.data_handling.gather_array(step.velocity_data_name, (slice(None, None, stride), slice(None, None, stride), nz // 2))
            rho = step.data_handling.gather_array(step.density_data_name, (slice(None, None, stride), slice(None, None, stride), nz // 2))
        else:
            velocity = step.data_handling.gather_array(step.velocity_data_name)[self._core_slice][::stride, ::stride, nz // 2]
            rho = step.data_handling.gather_array(step.density_data_name)[self._core_slice][::stride, ::stride, nz // 2]
        velocity, rho = np.asarray(velocity).squeeze(), np.asarray(rho).squeeze()
        speed = np.linalg.norm(velocity, axis=-1) / self.flow.lattice_speed
        omega_z = (np.gradient(velocity[..., 1], stride * self.plan.dx, axis=0)
                   - np.gradient(velocity[..., 0], stride * self.plan.dx, axis=1)) / self.flow.lattice_speed
        cp = (rho - 1) / (1.5 * self.flow.lattice_speed**2)
        mask = self._solid_xy[::stride, ::stride]
        for array in (speed, omega_z, cp):
            array[mask] = np.nan
        with atomic_binary(path) as handle:
            np.savez_compressed(handle, speed=speed, omega_z=omega_z, cp=cp, mask=mask,
                                origin=self.plan.origin, dx=self.plan.dx * stride,
                                t_star=self.steps * self.plan.dt_convective)

    def save_checkpoint(self, path, context=None):
        from .checkpoint import save_checkpoint
        return save_checkpoint(self, path, context)

    @classmethod
    def from_checkpoint(cls, path, progress=None):
        from .checkpoint import restore_checkpoint
        return restore_checkpoint(cls, path, progress)

    @classmethod
    def resume_run(cls, output, callback=None, stop_event=None, progress=None, residual_callback=None):
        output = Path(output)
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        solver = cls.from_checkpoint(output / "checkpoint.npz", progress)
        return solver.run(output=output, callback=callback, stop_event=stop_event,residual_callback=residual_callback,
                          resume=True, **metadata["run"])

    def run(self, duration_convective, output, sample_every=20, frame_every=200,
            average_after=0.0, check_every=100, callback=None, stop_event=None,
            checkpoint_every=1000, resume=False, termination=None, residual_callback=None):
        """Run an additional t*=t U/c interval; output must be a new/empty directory.

        callback(solver, record) may return False to stop, after saving progress.
        Mean coefficients are simple equally-spaced time-sample means. Uncertainty
        estimates must account for time correlation; RMS is not a confidence bound.
        """
        from .convergence import RunControl,StopCriteria,validate_history
        settings = RunSettings(duration_convective, sample_every, frame_every, check_every,
                               average_after, checkpoint_every,termination or StopCriteria())
        project = Project(self.section, self.flow, self.grid, settings,
                          "+".join(e.name for e in self.section.elements))
        output = Path(output)
        if not resume and output.exists() and any(output.iterdir()):
            raise FileExistsError("Use a new/empty output directory to avoid mixing histories")
        output.mkdir(parents=True, exist_ok=True)
        (output / "frames").mkdir(exist_ok=resume)
        from . import __version__
        metadata = {"version": __version__, "flow": asdict(self.flow), "grid": asdict(self.grid),
                    "plan": self.plan.as_dict(), "reference_chord_input_units": self.section.chord,
                    "moment_reference_input_units": self.section.moment_reference,
                    "contours": [{"name": e.name, "xy": e.xy.tolist()} for e in self.section.elements],
                    "coefficient_convention": "Cl up; Cd downstream; Cm nose-up about moment_reference; area=c*periodic_span",
                    "force_time": "midpoint of boundary momentum-transfer interval",
                    "status": "running", "experimental_validation": "not_performed",
                    "case_fingerprint": project.case_fingerprint, "run": asdict(settings),
                    "start_step": self.steps, "start_t_star": self.steps * self.plan.dt_convective,
                    "resume_supported": self.grid.target == "cpu"}
        from .timing import duration_steps
        requested_steps = duration_steps(duration_convective, self.plan.dt_convective)
        end_step = self.steps + requested_steps
        rows = []
        history = output / "history.csv"
        if resume:
            old = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
            metadata['start_step']=old.get('start_step',0)
            metadata['start_t_star']=metadata['start_step']*self.plan.dt_convective
            metadata['resumed_from_version']=old.get('version')
            context = getattr(self, "_checkpoint_context", {})
            saved_project = Project.load(output / "project.json")
            if (saved_project.case_fingerprint != project.case_fingerprint
                    or asdict(saved_project.run) != asdict(settings)):
                raise ValueError("Saved project or run settings changed; resume requires the original project")
            if (old.get("case_fingerprint") != project.case_fingerprint or RunSettings(**old.get("run",{})) != settings
                    or context.get("case_fingerprint") != project.case_fingerprint):
                raise ValueError("Resume requires the original project, settings and checkpoint")
            if old["status"] == "completed":
                raise ValueError("This run has already completed")
            end_step = context["end_step"]
            prefix = context["history_bytes"]
            if file_hash(history, prefix) != context["history_sha256"]:
                raise ValueError("History before the checkpoint has changed; refusing to mix results")
            validate_history(output,context)
            # A crash can leave samples/frames after the last durable checkpoint.
            # Preserve them before rolling back, then continue the same timeline.
            future = [p for p in (output / "frames").glob("frame_*.npz")
                      if int(p.stem.split("_")[-1]) > self.steps]
            if history.stat().st_size > prefix or future:
                recovery = output / "recovery" / uuid.uuid4().hex
                recovery.mkdir(parents=True)
                shutil.copy2(history, recovery / "history_before_resume.csv")
                with history.open("r+b") as handle:
                    handle.truncate(prefix)
                for path in future:
                    shutil.move(str(path), str(recovery / path.name))
            with history.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                row["step"] = int(row["step"])
                for name in ("t_star", "cl", "cd", "cm"):
                    row[name] = float(row[name])
            if rows and rows[-1]["step"] > self.steps:
                raise ValueError("History extends beyond the checkpoint")
        else:
            project.save(output / "project.json")
        if self.steps > end_step:
            raise ValueError("Checkpoint is past the requested end time")
        control=RunControl(self,settings,output,resume=resume,callback=residual_callback)
        reason='duration_reached'
        metadata["end_step"] = end_step
        metadata['residual_definition']='Volume-weighted L2/Linf of d(rho,j_common)/dt*, owned fluid only; stationary-flow residual, not a turbulence-accuracy measure'
        metadata["status"] = "running"
        if self._exterior is not None:
            metadata["boundary_model"] = self.boundary_diagnostics()
        write_json(output / "metadata.json", metadata)
        state = "completed"
        start_time = time.monotonic()
        names = ["step", "t_star", "time_s", "cl", "cd", "cm", "local_mach_max", "density_error_max"]
        if self._exterior is not None:
            names.extend(("wall_mass_exchange_lattice", "wall_mass_exchange_accumulated",
                          "exterior_source_mass_lattice", "exterior_source_mass_accumulated"))
        try:
            self.diagnostics()
            self.save_frame(output / "frames" / f"frame_{self.steps:010d}.npz")
            with history.open("a" if resume else "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, names)
                if not resume:
                    writer.writeheader()

                def checkpoint():
                    if self.grid.target != "cpu":
                        return
                    handle.flush()
                    os.fsync(handle.fileno())
                    context = {"case_fingerprint": project.case_fingerprint, "end_step": end_step,
                               "history_bytes": history.stat().st_size, "history_sha256": file_hash(history)}
                    context.update(control.checkpoint_context())
                    self.save_checkpoint(output / "checkpoint.npz", context)

                diagnostic = {}
                for _ in range(end_step - self.steps):
                    if stop_event is not None and stop_event.is_set():
                        state = "stopped"
                        reason='user_stopped'
                        break
                    sample = (self.steps + 1) % sample_every == 0
                    record = self.step(sample=sample)
                    if self.steps % check_every == 0 or self.steps == end_step or sample:
                        diagnostic = self.diagnostics()
                    if self.steps % check_every == 0:
                        control.observe()
                    if self.steps % frame_every == 0 or self.steps == end_step:
                        self.save_frame(output / "frames" / f"frame_{self.steps:010d}.npz")
                    if record is not None:
                        if not all(math.isfinite(record[k]) for k in ("cl", "cd", "cm")):
                            raise NumericalFailure("Non-finite loads; the run is invalid")
                        row = {k: record.get(k, "") for k in names}
                        row.update({k: diagnostic[k] for k in ("local_mach_max", "density_error_max")})
                        writer.writerow(row)
                        handle.flush()
                        rows.append(row)
                        if callback is not None and callback(self, record) is False:
                            state = "stopped"
                            reason='user_stopped'
                            break
                    selected_reason=control.stop_reason()
                    if selected_reason:
                        reason=selected_reason
                        if reason=='wall_time_limit':state='stopped'
                        break
                    if checkpoint_every and self.steps % checkpoint_every == 0:
                        checkpoint()
                self.diagnostics()
                self.save_frame(output / "frames" / f"frame_{self.steps:010d}.npz")
                checkpoint()
        except BaseException as error:
            state = "failed"
            reason='failed'
            metadata["failure"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            selected = np.array([[r["cl"], r["cd"], r["cm"]] for r in rows if r["t_star"] >= average_after])
            summary = {"status": state, "steps": self.steps, "t_star": self.steps * self.plan.dt_convective,
                       "wall_clock_seconds": time.monotonic() - start_time,
                       "samples": len(rows), "averaging_samples": len(selected),
                       "average_after_t_star": average_after,
                       "mean": dict(zip(("cl", "cd", "cm"), selected.mean(axis=0))) if len(selected) and state != "failed" else None,
                       "rms_fluctuation": dict(zip(("cl", "cd", "cm"), selected.std(axis=0))) if len(selected) and state != "failed" else None,
                       "wall_fallback_links": self.wall_fallback_links,
                       "experimental_validation": "not_performed"}
            summary.update(control.summary(reason))
            control.close()
            metadata['termination_reason']=reason
            metadata["status"] = state
            if self._exterior is not None:
                summary["boundary_diagnostics"] = self.boundary_diagnostics()
                metadata["boundary_model"] = self.boundary_diagnostics()
            write_json(output / "metadata.json", metadata)
            write_json(output / "summary.json", summary)
        return summary
