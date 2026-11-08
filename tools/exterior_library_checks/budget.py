"""Independent full-population flux across a physical FLUID mask.

No wall index lists or reconstruction formulas are used to count flux.
The caller observes the already reflected post-collision populations once,
immediately before streaming. This is verification code, not a steady residual.
"""
import numpy as np


class FluidBudget:
    def __init__(self, solver):
        self.solver = solver
        self.stepper = solver._stepper
        self.c = np.asarray(list(self.stepper.method.stencil), dtype=int)
        self.weights = np.asarray(list(map(float, self.stepper.method.weights)))
        shape, plan = solver._storage_shape, solver.plan
        x = (np.arange(shape[0]) + .5)*plan.dx + solver._storage_origin[0]
        y = (np.arange(shape[1]) + .5)*plan.dx + solver._storage_origin[1]
        solid_xy = np.zeros(shape[:2], bool)
        for contour in solver.section.normalized(solver.flow.alpha_deg):
            solid_xy |= contour.contains(x[:, None], y[None, :])
        solid = np.zeros(tuple(n+2 for n in shape), bool)
        solid[1:-1, 1:-1, 1:-1] = np.broadcast_to(solid_xy[:, :, None], shape)
        owned = np.zeros_like(solid)
        core_pdf = tuple(slice(1+p, 1+p+n) for p, n in zip(solver._padding, plan.shape))
        owned[core_pdf] = ~solid[core_pdf]
        for array in (solid, owned):
            array[:, :, 0] = array[:, :, -2]
            array[:, :, -1] = array[:, :, 1]
        indices = np.column_stack(np.nonzero(owned[core_pdf]))
        self.count = len(indices)
        positions = indices + np.asarray(solver._padding) + 1
        self.index = tuple(positions.T)
        self.rx = indices[:, 0] + .5 + plan.origin[0]/plan.dx
        self.ry = indices[:, 1] + .5 + plan.origin[1]/plan.dx
        self.crossings = []
        for q, direction in enumerate(self.c):
            for offset, sign in ((direction, -1), (-direction, 1)):
                neighbor = positions + offset
                crosses = ~owned[tuple(neighbor.T)]
                is_wall = solid[tuple(neighbor.T)]
                for kind, selected in (("wall", crosses & is_wall), ("outer", crosses & ~is_wall)):
                    if not selected.any():
                        continue
                    source = positions[selected] if sign < 0 else neighbor[selected]
                    arm = self.rx[selected]*direction[1] - self.ry[selected]*direction[0]
                    self.crossings.append((q, sign, kind, tuple(source.T), arm))

    def totals(self, name=None):
        s = self.stepper
        populations = s.data_handling.cpu_arrays[name or s.pdf_array_name][self.index]
        momentum = populations @ self.c
        return np.r_[self.count + populations.sum(), momentum.sum(axis=0),
                     np.sum(self.rx*momentum[:, 1] - self.ry*momentum[:, 0])]

    def flux(self):
        populations = self.stepper.data_handling.cpu_arrays[self.stepper.pdf_array_name]
        answer = {kind: np.zeros(5) for kind in ("wall", "outer")}
        for q, sign, kind, source, arm in self.crossings:
            values = populations[source + (q,)] + self.weights[q]
            answer[kind] += sign*np.r_[values.sum(), values.sum()*self.c[q], np.sum(values*arm)]
        return answer

    def observe(self, *, sample=True):
        """One normal Solver.step, with no extra boundary application."""
        s, dh = self.stepper, self.stepper.data_handling
        before = self.totals()
        original = dh.run_kernel
        capture = {}

        def observer(kernel, **parameters):
            if kernel is s._lbmKernels[1]:
                capture["flux"] = self.flux()
            result = original(kernel, **parameters)
            if kernel is s._lbmKernels[1]:
                capture["after_stream"] = self.totals(s._tmp_arr_name)
            return result

        dh.run_kernel = observer
        try:
            loads = self.solver.step(sample=sample)
        finally:
            dh.run_kernel = original
        flux = capture["flux"]
        residual = (capture["after_stream"] - before - flux["wall"] - flux["outer"])/self.count
        return loads, {"step": self.solver.steps, "fluid_budget_per_cell": residual.tolist(),
            "physical_fluid_overwrite_budget": ((self.totals() - capture["after_stream"])/self.count).tolist(),
            "wall_flux": flux["wall"].tolist(), "outer_flux": flux["outer"].tolist()}
