from dataclasses import dataclass
from pathlib import Path
import numpy as np


def cross2(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


@dataclass(frozen=True)
class Contour:
    """Closed polygon in a common coordinate frame; coordinates may have any scale."""
    xy: np.ndarray
    name: str = "element"

    def __post_init__(self):
        p = np.array(self.xy, dtype=float, copy=True)
        if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all():
            raise ValueError("A contour requires at least three finite (x,y) points")
        # Remove consecutive duplicate vertices, including a duplicate TE/closure.
        p = p[np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-14]]
        if len(p) > 1 and np.allclose(p[0], p[-1], rtol=0, atol=1e-14):
            p = p[:-1]
        if len(p) < 3:
            raise ValueError("Degenerate contour")
        area = abs(np.sum(cross2(p, np.roll(p, -1, axis=0)))) / 2
        if area <= 1e-12 * np.ptp(p, axis=0).max() ** 2:
            raise ValueError("Contour has zero enclosed area")
        a, b = p, np.roll(p, -1, axis=0)
        # Reject self intersections. Adjacent segments sharing a vertex are allowed.
        for i in range(len(p)):
            for j in range(i + 2, len(p)):
                if i == 0 and j == len(p) - 1:
                    continue
                if segments_intersect(a[i], b[i], a[j], b[j]):
                    raise ValueError(f"Self-intersection in contour {self.name}")
        p.setflags(write=False)
        object.__setattr__(self, "xy", p)

    @classmethod
    def from_aero2d(cls, airfoil):
        """Adapter for aero2d.geometry.Airfoil; preserves the element's frame."""
        return cls(np.column_stack((airfoil.x, airfoil.y)), getattr(airfoil, "name", "element"))

    @classmethod
    def from_dat(cls, path):
        """Read a single Selig-style polygon. Multi-element inputs use separate contours."""
        p = []
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            parts = line.replace(",", " ").split()
            if len(parts) == 2:
                try:
                    p.append(tuple(map(float, parts)))
                except ValueError:
                    pass
        return cls(np.asarray(p), Path(path).stem)

    def contains(self, x, y):
        """Vectorized ray test without allocating a point-by-segment matrix."""
        x, y = np.broadcast_arrays(x, y)
        inside = np.zeros(x.shape, dtype=bool)
        p = self.xy
        for a, b in zip(p, np.roll(p, -1, axis=0)):
            if b[1] == a[1]:
                continue
            hit = ((a[1] > y) != (b[1] > y)) & (x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0])
            inside ^= hit
        return inside

    def link_fraction(self, points, offsets):
        """First polygon intersection p + q e, 0<=q<=1, for fluid-to-solid links."""
        points = np.asarray(points)
        offsets = np.asarray(offsets)
        q = np.full(len(points), np.inf)
        for a, b in zip(self.xy, np.roll(self.xy, -1, axis=0)):
            edge = b - a
            den = cross2(offsets, edge)
            valid = abs(den) > 1e-14
            t = np.divide(cross2(a - points, edge), den, out=np.full_like(q, np.inf), where=valid)
            v = np.divide(cross2(a - points, offsets), den, out=np.full_like(q, np.inf), where=valid)
            hit = valid & (t >= -1e-10) & (t <= 1 + 1e-10) & (v >= -1e-10) & (v <= 1 + 1e-10)
            q = np.minimum(q, np.where(hit, t, np.inf))
        if not np.isfinite(q).all():
            raise ValueError("A fluid-to-solid link has no geometric intersection")
        return np.clip(q, 1e-10, 1.0)


def segments_intersect(a, b, c, d):
    ab, cd = b - a, d - c
    den = cross2(ab, cd)
    tol = 1e-12 * max(np.linalg.norm(ab) * np.linalg.norm(cd), 1e-30)
    if abs(den) <= tol:
        if abs(cross2(c - a, ab)) > tol:
            return False
        axis = int(np.argmax(abs(ab)))
        return max(min(a[axis], b[axis]), min(c[axis], d[axis])) <= min(max(a[axis], b[axis]), max(c[axis], d[axis])) + 1e-12
    t, u = cross2(c - a, cd) / den, cross2(c - a, ab) / den
    return -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12


@dataclass(frozen=True)
class Section:
    elements: tuple[Contour, ...]
    chord: float | None = None
    moment_reference: tuple[float, float] | None = None

    def __post_init__(self):
        elements = tuple(self.elements)
        if not elements:
            raise ValueError("At least one contour is required")
        if not all(isinstance(e, Contour) for e in elements):
            raise TypeError("Use Contour.from_aero2d for existing Airfoil objects")
        object.__setattr__(self, "elements", elements)
        allp = np.concatenate([e.xy for e in elements])
        # Full assembly chord: distance from foremost to aftmost point in the supplied frame.
        le = allp[np.argmin(allp[:, 0])]
        te_candidates = allp[np.isclose(allp[:, 0], allp[:, 0].max(), rtol=0, atol=1e-12)]
        te = te_candidates.mean(axis=0)
        chord = float(np.linalg.norm(te - le)) if self.chord is None else float(self.chord)
        if not np.isfinite(chord) or chord <= 0:
            raise ValueError("Reference chord must be positive")
        ref = le + 0.25 * (te - le) if self.moment_reference is None else np.asarray(self.moment_reference, dtype=float)
        if np.shape(ref) != (2,) or not np.isfinite(ref).all():
            raise ValueError("Moment reference requires two finite coordinates")
        object.__setattr__(self, "chord", chord)
        object.__setattr__(self, "moment_reference", tuple(ref))
        for i, e in enumerate(elements):
            for f in elements[i + 1:]:
                overlap = e.contains(*f.xy[0]) or f.contains(*e.xy[0])
                if not overlap:
                    overlap = any(segments_intersect(a, b, c, d)
                                  for a, b in zip(e.xy, np.roll(e.xy, -1, axis=0))
                                  for c, d in zip(f.xy, np.roll(f.xy, -1, axis=0)))
                if overlap:
                    raise ValueError(f"Elements touch or overlap: {e.name}, {f.name}")

    @classmethod
    def from_aero2d(cls, airfoils, **kwargs):
        if hasattr(airfoils, "x"):
            airfoils = [airfoils]
        return cls(tuple(Contour.from_aero2d(a) for a in airfoils), **kwargs)

    def normalized(self, alpha_deg):
        """Moment reference becomes origin; positive alpha raises the leading edge."""
        angle = -np.deg2rad(alpha_deg)
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        return tuple(Contour(((e.xy - self.moment_reference) / self.chord) @ rotation.T, e.name)
                     for e in self.elements)


def naca0012(n_side=161, closed_te=False):
    return naca4("0012", n_side, closed_te)


def naca4(code="0012", n_side=161, closed_te=False):
    """Four-digit NACA section, unit chord, cosine point distribution.

    This analytic shape does not certify identity to a wind-tunnel model.
    The input coordinates can be exported unchanged in a project.
    """
    code = str(code).strip()
    if len(code) != 4 or not code.isascii() or not code.isdigit():
        raise ValueError("NACA code must contain four digits, e.g. 0012 or 2412")
    m, p, thickness = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100
    if thickness <= 0 or (m > 0 and p == 0):
        raise ValueError("NACA thickness must be positive; cambered profiles require a nonzero second digit")
    if not isinstance(n_side, int):
        raise ValueError("n_side must be an integer")
    if n_side < 12:
        raise ValueError("Use at least 12 points per side")
    x = (1 - np.cos(np.linspace(0, np.pi, n_side))) / 2
    last = -0.1036 if closed_te else -0.1015
    y = 5 * thickness * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 + last * x**4)
    camber, slope = np.zeros_like(x), np.zeros_like(x)
    if m > 0:
        forward = x < p
        camber[forward] = m / p**2 * (2 * p * x[forward] - x[forward]**2)
        slope[forward] = 2 * m / p**2 * (p - x[forward])
        aft = ~forward
        camber[aft] = m / (1 - p)**2 * (1 - 2 * p + 2 * p * x[aft] - x[aft]**2)
        slope[aft] = 2 * m / (1 - p)**2 * (p - x[aft])
    angle = np.arctan(slope)
    upper = np.column_stack((x - y * np.sin(angle), camber + y * np.cos(angle)))
    lower = np.column_stack((x + y * np.sin(angle), camber - y * np.cos(angle)))
    return Contour(np.concatenate((upper[::-1], lower[1:])), f"NACA{code}")
