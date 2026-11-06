"""Experimental static 2:1 periodic fluid-box coupling with conservative reflux.

This is NOT yet the production airfoil solver. There are no bodies, open outer
boundaries, SGS, or multilevel restart here. Coarse/fine states are pre-collision;
the production Solver's post-collision arrays cannot be used as initial states.

Mass and momentum are conserved by replacing actual coarse Cartesian-face
streaming fluxes with the sum over four fine faces and two fine time steps.
Only coarse cells sharing the interface faces receive a reflux correction.
"""
from dataclasses import dataclass
from itertools import combinations, product
import math

import numpy as np

from .refinement import AcousticLevel, D3Q27Transfer


@dataclass(frozen=True)
class FineBox:
    """Half-open coarse cell bounds, aligned with physical coarse faces."""
    lower: tuple[int, int, int]
    upper: tuple[int, int, int]

    def __post_init__(self):
        for values in (self.lower, self.upper):
            if len(values) != 3 or any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer)) for v in values):
                raise ValueError("Fine-box bounds must be three integer indices")
        if any(lo < 0 or hi <= lo for lo, hi in zip(self.lower, self.upper)):
            raise ValueError("Fine-box bounds must have 0 <= lower < upper")
        object.__setattr__(self, "lower", tuple(int(v) for v in self.lower))
        object.__setattr__(self, "upper", tuple(int(v) for v in self.upper))

    @property
    def shape(self):
        return tuple(hi - lo for lo, hi in zip(self.lower, self.upper))

    @property
    def slices(self):
        return tuple(slice(lo, hi) for lo, hi in zip(self.lower, self.upper))


class StreamingFaceFlux:
    """Exact moment flux of pull streaming through a Cartesian face patch.

    The face index denotes the upper face of cell `index` on `axis`. Tangential
    intervals are half-open and follow the other axes in ascending order.
    A diagonal stream can be factored into axis shifts in six ways; the average
    gives an axis-symmetric exact discrete divergence. This avoids corner errors
    from simply assigning each diagonal particle to a destination coarse cell.

    Populations supplied to __call__ MUST be post-collision. Returned integrated
    flux is in [density,jx,jy,jz] * one lattice-cell volume units; caller supplies
    the physical cell volume. The uniform zero-centered background cancels in
    flux differences, so the stored populations can be used directly.
    """
    def __init__(self, shape, stencil, axis, index, tangential_ranges, *, periodic):
        self.shape = tuple(shape)
        c = np.asarray(tuple(stencil))
        if (len(self.shape) != 3 or any(not isinstance(n, (int, np.integer)) or n <= 0 for n in self.shape)
                or c.shape != (27, 3) or axis not in (0, 1, 2)):
            raise ValueError("Face flux requires a 3D D3Q27 array and a valid axis")
        if set(map(tuple, c)) != set(product((-1, 0, 1), repeat=3)):
            raise ValueError("Face flux requires each D3Q27 direction exactly once")
        c = c.astype(np.int64)
        others = [a for a in range(3) if a != axis]
        if len(tangential_ranges) != 2 or any(hi <= lo for lo, hi in tangential_ranges):
            raise ValueError("A face patch needs two nonempty tangential intervals")
        self.output_shape = tuple(hi - lo for lo, hi in tangential_ranges)
        self.axis = axis
        self.terms = []
        for direction, velocity in enumerate(c):
            normal = velocity[axis]
            if normal == 0:
                continue
            tangential_motion = [a for a in others if velocity[a] != 0]
            # A given subset precedes the normal shift in a uniformly random
            # permutation with probability |S|! (m-|S|)! / (m+1)!.
            m = len(tangential_motion)
            for count in range(m + 1):
                weight = math.factorial(count) * math.factorial(m - count) / math.factorial(m + 1)
                for subset in combinations(tangential_motion, count):
                    coordinates = [None] * 3
                    coordinates[axis] = np.array([index + (1 if normal < 0 else 0)])
                    for a, (lo, hi) in zip(others, tangential_ranges):
                        coordinates[a] = np.arange(lo, hi) - (velocity[a] if a in subset else 0)
                    for a in range(3):
                        if periodic:
                            coordinates[a] %= self.shape[a]
                        elif coordinates[a].min() < 0 or coordinates[a].max() >= self.shape[a]:
                            raise ValueError("Insufficient donor padding for face flux")
                    coefficient = weight * normal * np.r_[1.0, velocity]
                    self.terms.append((direction, np.ix_(*coordinates), coefficient))

    def __call__(self, post_collision_pdf):
        pdf = np.asarray(post_collision_pdf)
        if pdf.shape != (*self.shape, 27):
            raise ValueError("Post-collision face-flux array shape mismatch")
        result = np.zeros((*self.output_shape, 4))
        for direction, indices, coefficient in self.terms:
            sampled = pdf[..., direction][indices].squeeze(axis=self.axis)
            result += sampled[..., None] * coefficient
        return result


def _read_pdf(lbm):
    return np.asarray(lbm.data_handling.gather_array(lbm.pdf_array_name)).copy()


def _write_pdf(lbm, pdf):
    for block in lbm.data_handling.iterate(ghost_layers=False):
        block[lbm.pdf_array_name][...] = pdf


def _make_lattice(shape, level, *, periodicity=True, threads=1):
    import sympy as sp
    from lbmpy import LBMConfig, Method, Stencil
    from lbmpy.lbstep import LatticeBoltzmannStep
    from lbmpy.stencils import LBStencil
    from pystencils import CreateKernelConfig

    omega_s, omega_b = sp.symbols("omega_s omega_b")
    return LatticeBoltzmannStep(domain_size=shape, periodicity=periodicity,
        time_step_order="collide_stream", fixed_loop_sizes=False,
        lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27), method=Method.CUMULANT,
                             relaxation_rates=[omega_s, omega_b] + [sp.Integer(1)] * 27,
                             compressible=True, zero_centered=True),
        kernel_params={"omega_s": 1 / level.tau_shear, "omega_b": 1 / level.tau_bulk},
        config=CreateKernelConfig(data_type="float64", cpu_openmp=threads if threads > 1 else False))


class PeriodicRefinedBox:
    """Two simultaneously advanced fluid levels and one rectangular fine box.

    Coarse cells inside the fine box are virtual, supply predictions only, and
    are never counted in composite mass/momentum. Fine cells own that volume.
    A box may span an entire periodic axis; partial spans must be interior.
    The optional disabled-reflux mode exists ONLY as a labelled negative control.
    """
    pad = 2  # fine donor cells; one coarse-cell thickness

    def __init__(self, shape, box: FineBox, coarse: AcousticLevel, *,
                 memory_limit_gib=1.0, reflux=True, progress=None):
        self.shape = tuple(shape)
        if len(self.shape) != 3 or any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer)) or v < 8 for v in self.shape):
            raise ValueError("Coarse domain needs three integer sizes >=8")
        self.shape = tuple(int(n) for n in self.shape)
        if not isinstance(box, FineBox) or not isinstance(coarse, AcousticLevel):
            raise TypeError("Explicit FineBox and AcousticLevel are required")
        if not isinstance(reflux, bool):
            raise TypeError("reflux must be bool")
        self.box, self.coarse_level, self.fine_level = box, coarse, coarse.refined()
        self.full_periodic_axes = []
        for a, (lo, hi, n) in enumerate(zip(box.lower, box.upper, self.shape)):
            if lo == 0 and hi == n:
                self.full_periodic_axes.append(a)
            elif not 1 <= lo < hi <= n - 1:
                raise ValueError("Each fine-box axis must be wholly periodic or strictly inside the domain")
        self.fine_shape = tuple(2 * n for n in box.shape)
        self.padded_shape = tuple(n + 2 * self.pad for n in self.fine_shape)
        self.fine_slices = tuple(slice(self.pad, self.pad + n) for n in self.fine_shape)
        cells = math.prod(self.shape) + math.prod(self.padded_shape)
        # Conservative working estimate: lattice buffers plus snapshots,
        # decompositions/reconstructions, face registers, and JIT headroom.
        self.estimated_memory_gib = (cells * (8 * 27 * 8 + 4 * 10 * 8 + 4 * 8)
                                     + 256 * 2**20) / 2**30
        if not math.isfinite(memory_limit_gib) or memory_limit_gib <= 0:
            raise ValueError("Memory limit must be finite and positive")
        if self.estimated_memory_gib > memory_limit_gib:
            raise MemoryError(f"Estimated {self.estimated_memory_gib:.3f} GiB exceeds {memory_limit_gib:.3f} GiB")
        progress = progress or (lambda _: None)
        progress("Compiling generic pre-collision D3Q27 coarse/fine kernels")
        self._coarse = self._create_lattice(self.shape, coarse, coarse_grid=True)
        self._fine = self._create_lattice(self.padded_shape, self.fine_level, coarse_grid=False)
        self.transfer = self._create_transfer(self._coarse.method.stencil)
        self.coarse_owned = np.ones(self.shape, dtype=bool)
        self.coarse_owned[box.slices] = False
        self.reflux_support = np.zeros(self.shape, dtype=bool)
        self.faces = []
        for axis in range(3):
            if axis in self.full_periodic_axes:
                continue
            others = [a for a in range(3) if a != axis]
            coarse_ranges = [(box.lower[a], box.upper[a]) for a in others]
            fine_ranges = [(self.pad, self.pad + self.fine_shape[a]) for a in others]
            for side in (-1, 1):
                coarse_face_index = box.lower[axis] - 1 if side < 0 else box.upper[axis] - 1
                fine_face_index = self.pad - 1 if side < 0 else self.pad + self.fine_shape[axis] - 1
                outside = list(box.slices)
                outside[axis] = box.lower[axis] - 1 if side < 0 else box.upper[axis]
                outside = tuple(outside)
                self.reflux_support[outside] = True
                coarse_sampler = StreamingFaceFlux(self.shape, self.transfer.stencil, axis, coarse_face_index,
                                                     coarse_ranges, periodic=True)
                fine_sampler = StreamingFaceFlux(self.padded_shape, self.transfer.stencil, axis, fine_face_index,
                                                   fine_ranges, periodic=False)
                self.faces.append((axis, side, outside, coarse_sampler, fine_sampler))
        self.coarse_owned.flags.writeable = False
        self.reflux_support.flags.writeable = False
        self.reflux_enabled = reflux
        self.steps = 0
        self.fine_steps = 0
        self.initialized = False
        self.last_reflux_moments = np.zeros((*self.shape, 4))
        self.last_flux_register = []
        self.last_donor_time_fractions = ()

    @property
    def time(self):
        return self.steps * self.coarse_level.dt

    def _create_lattice(self, shape, level, *, coarse_grid):
        return _make_lattice(shape, level)

    def _create_transfer(self, stencil):
        return D3Q27Transfer(stencil, self.coarse_level, zero_centered=True, pdf_stage="pre_collision")

    def _restrict_fine(self, fine_pdf):
        return self.transfer.restrict(fine_pdf)

    def _fine_donors(self, before, after, fraction):
        return self.transfer.prolong_in_time(before, after, fraction)

    @staticmethod
    def coordinates(shape, dx, origin=(0, 0, 0)):
        return np.meshgrid(*[(np.arange(n) + 0.5) * dx + o for n, o in zip(shape, origin)],
                           indexing="ij", sparse=True)

    def _initial_pdf(self, initializer, shape, dx, origin):
        xyz = self.coordinates(shape, dx, origin)
        rho, momentum = initializer(*xyz, dx)
        fields = np.zeros((*shape, 10))
        fields[..., 0] = np.broadcast_to(rho, shape)
        fields[..., 1:4] = np.broadcast_to(momentum, (*shape, 3))
        return self.transfer.populations(fields)

    def initialize(self, initializer):
        """initializer(x,y,z,dx) returns cell-average rho and lattice momentum.

        Coordinates are sparse meshgrid arrays; outputs must broadcast. Uniform
        or analytic cell-average states are appropriate for verification. No
        physical seconds, airfoil chord or atmospheric condition is invented.
        """
        coarse_pdf = self._initial_pdf(initializer, self.shape, self.coarse_level.dx, (0, 0, 0))
        origin = tuple(lo * self.coarse_level.dx - self.pad * self.fine_level.dx for lo in self.box.lower)
        fine_pdf = self._initial_pdf(initializer, self.padded_shape, self.fine_level.dx, origin)
        coarse_pdf[self.box.slices] = self._restrict_fine(fine_pdf[self.fine_slices])
        _write_pdf(self._coarse, coarse_pdf)
        _write_pdf(self._fine, fine_pdf)
        self.steps = self.fine_steps = 0
        self.initialized = True
        self.last_reflux_moments.fill(0)
        self.last_flux_register = []
        self.last_donor_time_fractions = ()

    def _donor_block(self, coarse_pdf):
        # Predict the physical fine box plus two fine donor cells on each side.
        # Each parent prediction needs two more coarse donors beyond that.
        result = coarse_pdf
        for axis, (lo, hi) in enumerate(zip(self.box.lower, self.box.upper)):
            result = np.take(result, np.arange(lo - 3, hi + 3) % self.shape[axis], axis=axis)
        return result

    def _wrap_fine_periodic_padding(self, pdf):
        for axis in self.full_periodic_axes:
            n = self.fine_shape[axis]
            for at, source in ((slice(0, self.pad), slice(n, n + self.pad)),
                               (slice(n + self.pad, n + 2 * self.pad), slice(self.pad, 2 * self.pad))):
                target_slice, source_slice = [slice(None)] * 4, [slice(None)] * 4
                target_slice[axis], source_slice[axis] = at, source
                pdf[tuple(target_slice)] = pdf[tuple(source_slice)]

    @staticmethod
    def _collide(lbm):
        lbm.data_handling.run_kernel(lbm._lbmKernels[0], **lbm.kernel_params)

    @staticmethod
    def _stream(lbm):
        lbm._sync_src()
        # No wall or open boundary in this fluid-only periodic prototype.
        lbm.data_handling.run_kernel(lbm._lbmKernels[1], **lbm.kernel_params)
        lbm.data_handling.swap(lbm._pdf_arr_name, lbm._tmp_arr_name, lbm._gpu)

    def _apply_reflux(self, coarse_pdf, correction):
        support = self.reflux_support
        if not support.any():
            return
        old = self.transfer.moments(coarse_pdf[support])
        old[..., 4:] = 0  # equilibrium only; the actual non-equilibrium PDF is retained below
        new = old.copy()
        new[..., :4] += correction[support]
        coarse_pdf[support] += self.transfer.populations(new) - self.transfer.populations(old)
        # Reject nonphysical states, never clip or spread a correction globally.
        self.transfer.moments(coarse_pdf[support])

    def step(self):
        """Advance one synchronized coarse step; a failed state must be initialized anew."""
        if not self.initialized:
            raise RuntimeError("Initialize the composite state before stepping")
        if self.fine_steps != 2 * self.steps:
            raise RuntimeError("Coarse and fine clocks are not synchronized")
        try:
            self._step_once()
        except BaseException:
            # A partially completed level step cannot be resumed as a valid
            # synchronized state. Production checkpoint/recovery is a later stage.
            self.initialized = False
            raise

    def _step_once(self):
        old_coarse = _read_pdf(self._coarse)
        fine_state = _read_pdf(self._fine)[self.fine_slices].copy()
        self._collide(self._coarse)
        post = _read_pdf(self._coarse)
        coarse_flux = [sampler(post) * self.coarse_level.dx**3
                       for _, _, _, sampler, _ in self.faces]
        self._stream(self._coarse)
        prediction = _read_pdf(self._coarse)
        before, after = self._donor_block(old_coarse), self._donor_block(prediction)
        fine_flux = [np.zeros_like(flux) for flux in coarse_flux]
        donor_times = []
        for substep in range(2):
            fraction = substep / 2
            donor_times.append(fraction)
            padded = self._fine_donors(before, after, fraction)
            padded[self.fine_slices] = fine_state
            self._wrap_fine_periodic_padding(padded)
            _write_pdf(self._fine, padded)
            self._collide(self._fine)
            post = _read_pdf(self._fine)
            for i, (_, _, _, _, sampler) in enumerate(self.faces):
                flux = sampler(post)
                a, b = flux.shape[:2]
                fine_flux[i] += flux.reshape(a // 2, 2, b // 2, 2, 4).sum(axis=(1, 3)) * self.fine_level.dx**3
            self._stream(self._fine)
            fine_state = _read_pdf(self._fine)[self.fine_slices].copy()
        correction = np.zeros((*self.shape, 4))
        self.last_flux_register = []
        for (axis, side, outside, _, _), fc, ff in zip(self.faces, coarse_flux, fine_flux):
            difference = ff - fc
            correction[outside] += side * difference / self.coarse_level.dx**3
            self.last_flux_register.append({"axis": axis, "side": side,
                                            "fine_minus_coarse_integrated_moments": difference.copy()})
        if self.reflux_enabled:
            self._apply_reflux(prediction, correction)
        prediction[self.box.slices] = self._restrict_fine(fine_state)
        _write_pdf(self._coarse, prediction)
        self.last_reflux_moments = correction if self.reflux_enabled else np.zeros_like(correction)
        self.last_donor_time_fractions = tuple(donor_times)
        self.steps += 1
        self.fine_steps += 2

    def states(self):
        """Copies of coarse full state (covered cells virtual) and owned fine state."""
        if not self.initialized:
            raise RuntimeError("Initialize the composite state first")
        return _read_pdf(self._coarse), _read_pdf(self._fine)[self.fine_slices].copy()

    def leaf_moments(self):
        """rho/j/Pi fields, coordinate grids, ownership and dx for each level."""
        coarse, fine = self.states()
        origin = tuple(lo * self.coarse_level.dx for lo in self.box.lower)
        return ((self.transfer.moments(coarse), self.coordinates(self.shape, self.coarse_level.dx),
                 self.coarse_owned.copy(), self.coarse_level.dx),
                (self.transfer.moments(fine), self.coordinates(self.fine_shape, self.fine_level.dx, origin),
                 np.ones(self.fine_shape, dtype=bool), self.fine_level.dx))

    def conserved_integrals(self):
        mass = 0.0
        momentum = np.zeros(3)
        for fields, _, owned, dx in self.leaf_moments():
            mass += fields[..., 0][owned].sum() * dx**3
            momentum += fields[..., 1:4][owned].sum(axis=0) * dx**3 * self.coarse_level.lattice_velocity_unit
        return {"mass": float(mass), "momentum": momentum.tolist()}
