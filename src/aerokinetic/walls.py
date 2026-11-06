"""Pinned lbmpy Bouzidi walls and actual post-reflection momentum exchange.

Geometry coordinates are in common length units, not local padded array
indices. Loads include only fluid links originating in the owned physical
interior; periodic donor layers must never add to the force or span.
"""
import numpy as np


class StationaryWalls:
    def __init__(self, lbm, contours, *, origin, dx, owned_slices, prefix="solid"):
        from lbmpy.boundaries import NoSlipLinearBouzidi
        self.lbm, self.contours = lbm, tuple(contours)
        self.origin, self.dx = np.array(origin[:2], float), float(dx)
        self.owned_slices = tuple(owned_slices)
        self.stencil = np.array(lbm.method.stencil, dtype=np.int64)
        self.inverse = np.array([lbm.method.stencil.index(tuple(-c)) for c in self.stencil])
        self.fallback_links = 0
        self.objects = []
        for i, contour in enumerate(self.contours):
            wall = NoSlipLinearBouzidi(name=f"{prefix}_{i}", init_wall_distance=self._distance(contour))
            lbm.boundary_handling.set_boundary(wall, mask_callback=self._mask(contour))
            self.objects.append(wall)

    def _mask(self, contour):
        def callback(x, y, z):
            inside = contour.contains(x[:, :, 0] * self.dx + self.origin[0],
                                      y[:, :, 0] * self.dx + self.origin[1])
            return np.broadcast_to(inside[:, :, None], x.shape)
        return callback

    def _distance(self, contour):
        def callback(data, **kwargs):
            xy = np.column_stack([data.index_array[a] + data.offset[k] + 0.5
                                  for k, a in enumerate(("x", "y"))])
            directions = data.link_offsets()[:, :2]
            keys, inverse = np.unique(np.column_stack((xy, directions)), axis=0, return_inverse=True)
            points = keys[:, :2] * self.dx + self.origin
            offsets = keys[:, 2:] * self.dx
            fraction = contour.link_fraction(points, offsets)
            other = points - offsets
            blocked = np.zeros(len(points), bool)
            for element in self.contours:
                blocked |= element.contains(other[:, 0], other[:, 1])
            fallback = (fraction < 0.5) & blocked
            fraction[fallback] = -1.0  # explicit documented upstream halfway fallback
            data["q"] = fraction[inverse]
            self.fallback_links += int(np.sum(fallback[inverse]))
        return callback

    def owned_links(self):
        handling = self.lbm.boundary_handling
        handling.prepare()
        for block in self.lbm.data_handling.iterate(ghost_layers=1):
            lists = block[handling._index_array_name].boundary_object_to_index_list
            pdf = block[self.lbm.pdf_array_name]
            for wall in self.objects:
                indices = lists.get(wall)
                if indices is None or not len(indices):
                    continue
                owned = np.ones(len(indices), bool)
                for a, axis in enumerate(("x", "y", "z")):
                    coordinate = indices[axis] + block.offset[a]
                    limits = self.owned_slices[a]
                    owned &= (coordinate >= limits.start) & (coordinate < limits.stop)
                yield block, pdf, indices[owned]

    def measure(self):
        """After reflection and before streaming; this never applies a boundary.

        force_lattice is the solid's momentum gain in lattice units. The moment
        lever arm is already in common length units about common (0,0). The
        mass_exchange is mass entering the fluid through interpolated links,
        which must be reported separately rather than silently corrected.
        """
        force = np.zeros(3)
        moment, mass = 0., 0.
        links = 0
        for block, pdf, ind in self.owned_links():
            if not len(ind):
                continue
            d = ind["dir"].astype(np.int64)
            e = self.stencil[d]
            pos = [ind[a].astype(np.int64) for a in ("x", "y", "z")]
            outgoing = pdf[(*pos, d)]
            incoming = pdf[(*(pos[k] + e[:, k] for k in range(3)), self.inverse[d])]
            link_force = (outgoing + incoming)[:, None] * e
            force += link_force.sum(axis=0)
            rx = (pos[0] + block.offset[0] + 0.5) * self.dx + self.origin[0]
            ry = (pos[1] + block.offset[1] + 0.5) * self.dx + self.origin[1]
            moment += np.sum(rx * link_force[:, 1] - ry * link_force[:, 0])
            mass += np.sum(incoming - outgoing)
            links += len(ind)
        return {"force_lattice": force, "moment_z_common_lattice": float(moment),
                "mass_exchange_lattice": float(mass), "owned_links": links}
