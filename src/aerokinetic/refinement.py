"""Experimental fluid-only, cell-average D3Q27 remapping, NOT an AMR solver.

All populations accepted here are UNFORCED PRE-COLLISION populations. The
production Solver currently stores post-collision populations and must NOT pass
them here. This module does not supply interface streaming, refluxing, walls,
SGS, load ownership or multilevel checkpoints. See docs/REFINEMENT_RU.md.

The remap retains density, momentum and six non-equilibrium second moments.
Higher non-hydrodynamic modes are intentionally regularized away. The five-point
prediction conserves every parent's cell average; it is not point interpolation.
"""
from dataclasses import dataclass
from itertools import product
import math

import numpy as np


CS2 = 1.0 / 3.0
SECOND_MOMENTS = ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))


@dataclass(frozen=True)
class AcousticLevel:
    """dx and dt in one common system; tau values are in lattice time units.

    The bulk viscosity convention here is zeta = 2/3 cs² (tau_bulk-.5) dx²/dt
    in three dimensions. It is distinct from the shear kinematic viscosity.
    """
    dx: float
    dt: float
    tau_shear: float
    tau_bulk: float

    def __post_init__(self):
        for name in ("dx", "dt", "tau_shear", "tau_bulk"):
            value = getattr(self, name)
            if isinstance(value, (bool, np.bool_)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.tau_shear <= 0.5 or self.tau_bulk <= 0.5:
            raise ValueError("Relaxation times must be strictly above 0.5")
        try:
            physical_values = self.lattice_velocity_unit, self.shear_viscosity, self.bulk_viscosity
        except (OverflowError, ZeroDivisionError) as error:
            raise ValueError("Level units or physical viscosities overflow or underflow") from error
        if not all(math.isfinite(v) and v > 0 for v in physical_values):
            raise ValueError("Level units or physical viscosities overflow or underflow")

    @property
    def lattice_velocity_unit(self):
        return self.dx / self.dt

    @property
    def shear_viscosity(self):
        return CS2 * (self.tau_shear - 0.5) * self.dx**2 / self.dt

    @property
    def bulk_viscosity(self):
        return (2.0 / 3.0) * CS2 * (self.tau_bulk - 0.5) * self.dx**2 / self.dt

    def refined(self):
        """Two fine steps per coarse step; the physical viscosities stay fixed."""
        return AcousticLevel(self.dx / 2, self.dt / 2,
                             0.5 + 2 * (self.tau_shear - 0.5),
                             0.5 + 2 * (self.tau_bulk - 0.5))


def _real_array(values, name):
    original = np.asarray(values)
    if original.dtype.kind not in "fiu":
        raise ValueError(f"{name} must be real numeric data")
    result = np.asarray(original, dtype=np.float64)
    if not np.isfinite(result).all():
        raise ValueError(f"{name} must be finite")
    return result


def prolong_cell_averages(values_with_halo):
    """Predict eight child averages, with two explicit donor halo cells per side.

    First three dimensions are x,y,z; any trailing component dimensions are
    independent. No periodicity or wall data is invented here. For each axis,
    children = parent +/- [11/64 (a[i+1]-a[i-1])
                           -3/128 (a[i+2]-a[i-2])].
    This reproduces degree-four cell averages and preserves every parent's
    average. It is an unlimited smooth-fluid operator: it may overshoot. Do
    not put a wall, a shock, or an unresolved gap in its donor stencil.
    """
    values = _real_array(values_with_halo, "Donor cell averages")
    if values.ndim < 3 or any(n < 5 for n in values.shape[:3]):
        raise ValueError("A 3D donor block needs two halo cells on each side")
    for axis in range(3):
        indices = np.arange(2, values.shape[axis] - 2)
        center = np.take(values, indices, axis=axis)
        offset = (11 / 64 * (np.take(values, indices + 1, axis=axis)
                             - np.take(values, indices - 1, axis=axis))
                  - 3 / 128 * (np.take(values, indices + 2, axis=axis)
                               - np.take(values, indices - 2, axis=axis)))
        # Stack at the parent/child dimensions before joining them. This keeps
        # the sibling average exactly equal to its parent, without boundary fill.
        values = np.stack((center - offset, center + offset), axis=axis + 1)
        shape = list(center.shape)
        shape[axis] *= 2
        values = values.reshape(shape)
    return values


def restrict_cell_averages(fine_values):
    """Volume-average each 2x2x2 child group; no halos are accepted."""
    values = _real_array(fine_values, "Fine cell averages")
    if values.ndim < 3 or any(n == 0 or n % 2 for n in values.shape[:3]):
        raise ValueError("Fine block must have three nonempty even spatial dimensions")
    nx, ny, nz = values.shape[:3]
    return values.reshape(nx // 2, 2, ny // 2, 2, nz // 2, 2, *values.shape[3:]).mean(axis=(1, 3, 5))


class D3Q27Transfer:
    """Regularized 2:1 remapping between synchronized fluid levels.

    The stencil order and storage convention must be supplied explicitly.
    pdf_stage is deliberately mandatory: production post-collision PDFs are
    incompatible. Input arrays and the pinned production solver are never edited.
    """
    def __init__(self, stencil, coarse: AcousticLevel, *, zero_centered: bool,
                 pdf_stage: str):
        if pdf_stage != "pre_collision":
            raise ValueError("Only unforced pre_collision PDFs are supported; post-collision transfer is different")
        if not isinstance(coarse, AcousticLevel):
            raise TypeError("coarse must be an AcousticLevel")
        if not isinstance(zero_centered, bool):
            raise TypeError("zero_centered must be an explicit bool")
        c = _real_array(tuple(stencil), "Stencil")
        expected = set(product((-1, 0, 1), repeat=3))
        if c.shape != (27, 3) or set(map(tuple, c)) != expected:
            raise ValueError("Stencil must contain every D3Q27 direction exactly once")
        self.coarse, self.fine = coarse, coarse.refined()
        self.zero_centered = zero_centered
        self.stencil = c.astype(np.int64)
        self.weights = np.prod(np.where(self.stencil == 0, 2 / 3, 1 / 6), axis=1)
        self._second = np.stack([c[:, a] * c[:, b] for a, b in SECOND_MOMENTS], axis=1)
        hermite = self._second.copy()
        hermite[:, :3] -= CS2
        hermite[:, 3:] *= 2  # symmetric off-diagonal tensor contraction
        self._reconstruction = self.weights[:, None] * hermite / (2 * CS2**2)
        for array in (self.stencil, self.weights, self._second, self._reconstruction):
            array.flags.writeable = False

    def moments(self, pdf):
        """Return [rho, jx,jy,jz, Pi_neq_xx,yy,zz,xy,xz,yz].

        Pi_neq is relative to rho*cs²*I + j*j/rho. Density and momentum,
        rather than velocity, are the variables conserved under averaging.
        """
        pdf = _real_array(pdf, "Populations")
        if pdf.ndim < 1 or pdf.shape[-1] != 27:
            raise ValueError("Last population dimension must be 27")
        delta_rho = pdf.sum(axis=-1)
        rho = delta_rho + 1 if self.zero_centered else delta_rho
        if np.any(rho <= 0):
            raise ValueError("Transfer encountered nonpositive density")
        physical_pdf = pdf + self.weights if self.zero_centered else pdf
        if np.any(physical_pdf < 0):
            raise ValueError("Donor has negative physical populations")
        j = pdf @ self.stencil
        self._check_mach(rho, j)
        pi = pdf @ self._second
        pi[..., :3] -= (delta_rho if self.zero_centered else rho)[..., None] * CS2
        for k, (a, b) in enumerate(SECOND_MOMENTS):
            pi[..., k] -= j[..., a] * j[..., b] / rho
        return np.concatenate((rho[..., None], j, pi), axis=-1)

    @staticmethod
    def _check_mach(rho, j):
        if np.any(np.linalg.norm(j, axis=-1) / rho / math.sqrt(CS2) > 0.3):
            raise ValueError("Transfer supports local lattice Mach <= 0.3 only")

    def populations(self, moments):
        """Reconstruct tensor-product equilibrium plus second-order Hermite stress.

        This deliberately discards all independent higher-order non-equilibrium
        modes. Positivity failures raise an error; no clipping changes the state.
        """
        fields = _real_array(moments, "Hydrodynamic moments")
        if fields.ndim < 1 or fields.shape[-1] != 10:
            raise ValueError("Last hydrodynamic moment dimension must be 10")
        rho, j = fields[..., 0], fields[..., 1:4]
        if np.any(rho <= 0):
            raise ValueError("Prediction produced nonpositive density; do not clip a conservative transfer")
        self._check_mach(rho, j)
        u = j / rho[..., None]
        equilibrium = np.ones((*rho.shape, 27))
        for axis in range(3):
            velocity = u[..., axis, None]
            direction = self.stencil[:, axis]
            marginal = np.where(direction == 0, 2 / 3 - velocity**2,
                                (CS2 + velocity**2 + direction * velocity) / 2)
            equilibrium *= marginal
        equilibrium *= rho[..., None]
        pdf = equilibrium + fields[..., 4:] @ self._reconstruction.T
        # Test physical populations before subtracting the background.
        if not np.isfinite(pdf).all() or np.any(pdf < 0):
            raise ValueError("Prediction produced invalid or negative physical populations")
        return pdf - self.weights if self.zero_centered else pdf

    def _rescale_stress(self, fields, source, target):
        result = fields.copy()
        pi = result[..., 4:]
        isotropic = pi[..., :3].mean(axis=-1)
        shear_scale = target.tau_shear * target.dt / (source.tau_shear * source.dt)
        bulk_scale = target.tau_bulk * target.dt / (source.tau_bulk * source.dt)
        pi *= shear_scale
        pi[..., :3] += (bulk_scale - shear_scale) * isotropic[..., None]
        return result

    def rescale(self, pdf, *, to_fine):
        """Change level units at the same physical samples, without interpolation."""
        if not isinstance(to_fine, bool):
            raise TypeError("to_fine must be an explicit bool")
        source, target = (self.coarse, self.fine) if to_fine else (self.fine, self.coarse)
        return self.populations(self._rescale_stress(self.moments(pdf), source, target))

    def prolong(self, coarse_pdf_with_halo):
        """Coarse -> fine fluid block; requires two donor halos in x,y,z."""
        fields = self.moments(coarse_pdf_with_halo)
        if fields.ndim != 4:
            raise ValueError("Block population arrays must have shape (nx,ny,nz,27)")
        predicted = prolong_cell_averages(fields)
        return self.populations(self._rescale_stress(predicted, self.coarse, self.fine))

    def prolong_in_time(self, before, after, fraction):
        """Interpolate conserved moments at a coarse-time fraction, then prolong.

        before and after must be actual pre-collision donor states separated
        by one coarse step. Calling code owns the clock and halo exchange.
        """
        if isinstance(fraction, (bool, np.bool_)) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError("Time fraction must be finite and in [0,1]")
        a, b = self.moments(before), self.moments(after)
        if a.shape != b.shape or a.ndim != 4:
            raise ValueError("Time donor blocks must have identical 3D shapes")
        predicted = prolong_cell_averages((1 - fraction) * a + fraction * b)
        return self.populations(self._rescale_stress(predicted, self.coarse, self.fine))

    def restrict(self, fine_pdf):
        """Fine -> coarse, retaining volume means of rho and momentum.

        Regularized stress is averaged in its fine-level units before scaling.
        This is a remap, NOT a coarse/fine streaming flux correction.
        """
        fields = self.moments(fine_pdf)
        if fields.ndim != 4:
            raise ValueError("Block population arrays must have shape (nx,ny,nz,27)")
        averaged = restrict_cell_averages(fields)
        return self.populations(self._rescale_stress(averaged, self.fine, self.coarse))
