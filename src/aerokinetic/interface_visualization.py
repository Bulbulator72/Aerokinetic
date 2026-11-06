"""Offline scientific slices of the fluid-interface verification cases.

The production uniform-grid airfoil visualization is separate. These slices
show the actual coarse/fine cell footprints, without smoothing in x/y. Density
and momentum are interpolated to one explicitly stated plane in z.
"""
from pathlib import Path
import os
import tempfile

import numpy as np

from ._io import atomic_binary


def slice_data(solver, *, z_plane, background_velocity_lattice):
    if not np.isfinite(z_plane):
        raise ValueError("Slice plane must be finite")
    background = np.asarray(background_velocity_lattice, dtype=float)
    if background.shape != (3,) or not np.isfinite(background).all():
        raise ValueError("Specify three finite background velocity components")
    saved = {"time": solver.time, "z_plane": z_plane,
             "background_velocity_lattice": background,
             "velocity_unit": solver.coarse_level.lattice_velocity_unit}
    for label, (fields, coordinates, owned, dx) in zip(("coarse", "fine"), solver.leaf_moments()):
        x, y, z = coordinates
        zz = z.ravel()
        if not zz[0] <= z_plane <= zz[-1]:
            raise ValueError("Slice plane needs bracketing cell centres on both levels; extrapolation is unsupported")
        upper = min(len(zz) - 1, max(1, np.searchsorted(zz, z_plane)))
        lower = upper - 1
        fraction = (z_plane - zz[lower]) / (zz[upper] - zz[lower])
        moment_slice = (1 - fraction) * fields[:, :, lower, :4] + fraction * fields[:, :, upper, :4]
        velocity = (moment_slice[..., 1:4] / moment_slice[..., 0, None] - background) * saved["velocity_unit"]
        saved[f"{label}_speed"] = np.linalg.norm(velocity, axis=-1)
        saved[f"{label}_velocity_perturbation"] = velocity
        saved[f"{label}_rho"] = moment_slice[..., 0]
        saved[f"{label}_mask"] = ~(owned[:, :, lower] & owned[:, :, upper])
        saved[f"{label}_dx"] = dx
        saved[f"{label}_x_edges"] = x.ravel()[0] - dx / 2 + np.arange(fields.shape[0] + 1) * dx
        saved[f"{label}_y_edges"] = y.ravel()[0] - dx / 2 + np.arange(fields.shape[1] + 1) * dx
    return saved


def save_slice(solver, path, *, z_plane, background_velocity_lattice):
    saved = slice_data(solver, z_plane=z_plane, background_velocity_lattice=background_velocity_lattice)
    with atomic_binary(Path(path)) as handle:
        np.savez_compressed(handle, **saved)
    return Path(path)


def animate_slices(directory, output, *, speed_scale, fps=6):
    if not np.isfinite(speed_scale) or speed_scale <= 0 or not isinstance(fps, int) or fps < 1:
        raise ValueError("Positive speed scale and integer fps>=1 are required")
    files = sorted(Path(directory).glob("frame_*.npz"))
    if len(files) < 2:
        raise ValueError("At least two actual slice frames are required")
    output = Path(output)
    if output.suffix.lower() != ".gif":
        raise ValueError("The offline interface exporter writes .gif")
    if output.exists():
        raise FileExistsError("Use a new animation output")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.animation import FuncAnimation, PillowWriter
    from matplotlib.patches import Rectangle

    times, limit = [], 0.
    for path in files:
        with np.load(path, allow_pickle=False) as data:
            times.append(float(data["time"]))
            for level in ("coarse", "fine"):
                values = data[f"{level}_speed"][~data[f"{level}_mask"]]
                if not np.isfinite(values).all():
                    raise ValueError("Non-finite saved slice")
                limit = max(limit, float(values.max(initial=0)) / speed_scale)
    if not np.all(np.diff(times) > 0):
        raise ValueError("Slice times must strictly increase")
    fig = Figure(figsize=(7, 6), constrained_layout=True)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    images = []
    with np.load(files[0], allow_pickle=False) as first:
        for level in ("coarse", "fine"):
            data = np.ma.array(first[f"{level}_speed"] / speed_scale, mask=first[f"{level}_mask"])
            images.append(ax.pcolormesh(first[f"{level}_x_edges"], first[f"{level}_y_edges"], data.T,
                                       shading="flat", cmap="magma", vmin=0, vmax=max(limit * 1.02, 1e-12),
                                       edgecolors=(0, 0, 0, 0.14), linewidth=0.15))
        x_edges, y_edges = first["fine_x_edges"], first["fine_y_edges"]
        ax.add_patch(Rectangle((x_edges[0], y_edges[0]), x_edges[-1] - x_edges[0], y_edges[-1] - y_edges[0],
                               fill=False, edgecolor="cyan", linewidth=1.2))
    ax.set(xlabel="x, common length units", ylabel="y, common length units", aspect="equal")
    fig.colorbar(images[0], ax=ax, label="|u - U0| / A")

    def update(index):
        with np.load(files[index], allow_pickle=False) as frame:
            for level, artist in zip(("coarse", "fine"), images):
                values = np.ma.array(frame[f"{level}_speed"] / speed_scale, mask=frame[f"{level}_mask"])
                artist.set_array(values.T)
            ax.set_title(f"3D verification vortex; z={float(frame['z_plane']):g}; t={float(frame['time']):g}")
        return images

    output.parent.mkdir(parents=True, exist_ok=True)
    animation = FuncAnimation(fig, update, frames=len(files), interval=1000 / fps, blit=False)
    fd, temporary = tempfile.mkstemp(prefix=".interface_", suffix=".gif", dir=output.parent)
    os.close(fd)
    try:
        animation.save(temporary, writer=PillowWriter(fps=fps), dpi=100)
        os.replace(temporary, output)
        update(len(files) - 1)
        png = output.with_suffix(".png")
        fig.savefig(png, dpi=140)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
        fig.clear()
    return {"gif": output.name, "png": png.name, "frame_count": len(files),
            "times": times, "colour_scale_fixed_across_frames": True,
            "xy_interpolation": "none: flat shading of actual cells",
            "z_interpolation": "density and momentum linearly interpolated to the stated plane"}
