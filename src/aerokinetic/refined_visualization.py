"""Actual coarse/fine airfoil sections and offline plotting, without x/y smoothing."""
import json
import os
from pathlib import Path
import tempfile

import numpy as np

from ._io import atomic_binary
from .interface_visualization import slice_data


def save_frame(solver, path):
    data = slice_data(solver, z_plane=solver.plan.span / 2, background_velocity_lattice=(0., 0., 0.))
    data.update(format="aerokinetic_refined_slice", t_star=solver.time,
                contours_json=json.dumps([e.xy.tolist() for e in solver.contours]),
                fine_cells_per_chord=2 * solver.grid.cells_per_chord)
    if solver.flow.physical_speed is not None:
        data["physical_speed"] = solver.flow.physical_speed
        data["physical_dynamic_pressure"] = 0.5 * solver.flow.atmosphere.rho * solver.flow.physical_speed**2
    for level in ("coarse", "fine"):
        mask = data[f"{level}_mask"]
        velocity = data[f"{level}_velocity_perturbation"].copy()  # background is zero: this is u/U_inf
        if level == "fine":
            velocity[mask] = np.nan
        cp = (data[f"{level}_rho"] - 1) / (1.5 * solver.flow.lattice_speed**2)
        cp[mask] = np.nan
        dx = float(data[f"{level}_dx"])
        omega = np.gradient(velocity[..., 1], dx, axis=0) - np.gradient(velocity[..., 0], dx, axis=1)
        omega[mask] = np.nan
        data[f"{level}_cp"] = cp
        data[f"{level}_omega_z"] = omega
        data[f"{level}_speed"][mask] = np.nan
    with atomic_binary(Path(path)) as handle:
        np.savez_compressed(handle, **data)
    return Path(path)


def field_values(frame, level, field):
    key = {"speed_m_s": "speed", "pressure_pa": "cp"}.get(field, field)
    if key not in ("speed", "cp", "omega_z"):
        raise ValueError("Unknown refined field")
    values = frame[f"{level}_{key}"].copy()
    if field == "speed_m_s":
        if "physical_speed" not in frame:
            raise ValueError("Physical chord is required for dimensional speed")
        values *= float(frame["physical_speed"])
    elif field == "pressure_pa":
        if "physical_dynamic_pressure" not in frame:
            raise ValueError("Physical chord is required for dimensional pressure")
        values *= float(frame["physical_dynamic_pressure"])
    return np.ma.masked_invalid(values)


def draw_frame(figure, path, field="speed", limit=None):
    from matplotlib.patches import Polygon, Rectangle
    figure.clear()
    ax = figure.add_subplot()
    with np.load(path, allow_pickle=False) as frame:
        fields = [field_values(frame, level, field) for level in ("coarse", "fine")]
        if limit is None:
            limit = max((float(np.max(abs(v.compressed()), initial=0)) for v in fields), default=1.)
        limit = max(limit, 1e-12)
        positive = field in ("speed", "speed_m_s")
        for level, values in zip(("coarse", "fine"), fields):
            image = ax.pcolormesh(frame[f"{level}_x_edges"], frame[f"{level}_y_edges"], values.T,
                                  shading="flat", cmap="viridis" if positive else "RdBu_r",
                                  vmin=0 if positive else -limit, vmax=limit)
        contours = json.loads(str(frame["contours_json"]))
        for xy in contours:
            ax.add_patch(Polygon(xy, closed=True, facecolor="0.45", edgecolor="0.15", linewidth=.8))
        xx, yy = frame["fine_x_edges"], frame["fine_y_edges"]
        ax.add_patch(Rectangle((xx[0], yy[0]), xx[-1] - xx[0], yy[-1] - yy[0],
                               fill=False, edgecolor="0.25", linewidth=.6, linestyle="--"))
        ax.set_title(f"Два уровня; среднее сечение размаха; tU/c={float(frame['time']):.4f}")
    label = {"speed": "|u| / U∞", "cp": "Cp", "omega_z": "ωz c/U∞",
             "speed_m_s": "Скорость, м/с", "pressure_pa": "p−p∞, Па"}[field]
    figure.colorbar(image, ax=ax, label=label)
    ax.set(xlabel="x/c от точки момента", ylabel="y/c", aspect="equal", facecolor="0.7")
    return ax


def animate(directory, output=None, field="speed", fps=10, max_frames=240):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.animation import FuncAnimation, PillowWriter
    files = sorted((Path(directory) / "frames").glob("frame_*.npz"))
    if not files:
        raise FileNotFoundError("No refined field frames")
    if type(fps) is not int or fps<1 or type(max_frames) is not int or max_frames<2:
        raise ValueError("fps>=1 and max_frames>=2 are required")
    if len(files)>max_frames:
        files=[files[i] for i in np.linspace(0,len(files)-1,max_frames).astype(int)]
    output = Path(output) if output else Path(directory) / f"{field}.gif"
    if output.suffix.lower() != ".gif":
        raise ValueError("Offline animation writes GIF")
    limit = 0.
    for path in files:
        with np.load(path, allow_pickle=False) as frame:
            for level in ("coarse", "fine"):
                limit = max(limit, float(np.max(abs(field_values(frame, level, field).compressed()), initial=0)))
    fig = Figure(figsize=(9, 5), constrained_layout=True)
    FigureCanvasAgg(fig)
    movie = FuncAnimation(fig, lambda i: draw_frame(fig, files[i], field, limit),
                           frames=len(files), interval=1000 / fps, blit=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".refined_", suffix=".gif", dir=output.parent)
    os.close(fd)
    try:
        movie.save(temporary, writer=PillowWriter(fps=fps))
        os.replace(temporary, output)
        draw_frame(fig, files[-1], field, limit)
        fig.savefig(output.with_suffix(".png"), dpi=140)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
        fig.clear()
    return output
