from pathlib import Path
import os
import tempfile
import numpy as np


def animate(run_directory, output=None, field="omega_z", fps=12, max_frames=240):
    """Offline GIF of the midspan slice; requires no browser or ffmpeg."""
    if field not in ("omega_z", "speed", "cp", "speed_m_s", "pressure_pa"):
        raise ValueError("field must be omega_z, speed, cp, speed_m_s or pressure_pa")
    if not isinstance(fps, int) or fps < 1 or not isinstance(max_frames, int) or max_frames < 2:
        raise ValueError("fps>=1 and max_frames>=2 are required")
    import json
    metadata_path=Path(run_directory)/"metadata.json"
    if metadata_path.is_file() and json.loads(metadata_path.read_text(encoding="utf-8")).get("format")=="aerokinetic_refined_run":
        from .refined_visualization import animate as animate_refined
        return animate_refined(run_directory,output,field,fps,max_frames)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.animation import FuncAnimation, PillowWriter

    run_directory = Path(run_directory)
    key = {"speed_m_s": "speed", "pressure_pa": "cp"}.get(field, field)
    factor = 1.0
    if field in ("speed_m_s", "pressure_pa"):
        import json
        from .config import Atmosphere, Flow
        metadata = json.loads((run_directory / "metadata.json").read_text(encoding="utf-8"))
        values = dict(metadata["flow"])
        values["atmosphere"] = Atmosphere(**values.get("atmosphere", {}))
        flow = Flow(**values)
        if flow.physical_speed is None:
            raise ValueError("Physical chord is required for dimensional field export")
        factor = flow.physical_speed if field == "speed_m_s" else 0.5 * flow.atmosphere.rho * flow.physical_speed**2
    files = sorted((run_directory / "frames").glob("frame_*.npz"))
    if not files:
        raise FileNotFoundError("No saved flow frames")
    if len(files) > max_frames:
        files = [files[i] for i in np.linspace(0, len(files) - 1, max_frames).astype(int)]
    output = Path(output) if output else run_directory / f"{field}.gif"
    if output.suffix.lower() != ".gif":
        raise ValueError("This exporter writes .gif files")
    bounds = []
    for path in files[::max(1, len(files) // 20)]:
        with np.load(path) as data:
            values = data[key] * factor
            values = values[np.isfinite(values)]
            if len(values):
                bounds.append(np.quantile(abs(values), 0.99))
    limit = max(max(bounds, default=1.0), 1e-6)
    with np.load(files[0]) as first:
        array = first[key] * factor
        origin, dx = first["origin"], float(first["dx"])
        extent = (origin[0], origin[0] + dx * array.shape[0], origin[1], origin[1] + dx * array.shape[1])
        fig = Figure(figsize=(10, 5), constrained_layout=True)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot()
        im = ax.imshow(array.T, extent=extent, origin="lower", interpolation="nearest",
                       cmap="viridis" if key == "speed" else "RdBu_r",
                       vmin=0 if key == "speed" else -limit, vmax=limit)
    fig.colorbar(im, ax=ax, label={"omega_z": "vorticity z * c / U", "speed": "|u| / U", "cp": "Cp",
                                  "speed_m_s": "speed, m/s", "pressure_pa": "p - p_inf, Pa"}[field])
    ax.set(xlabel="x / c (moment reference at origin)", ylabel="y / c", aspect="equal", facecolor="0.35")

    def update(i):
        with np.load(files[i]) as data:
            im.set_data(data[key].T * factor)
            ax.set_title(f"3D calculation, midspan slice: t U/c = {float(data['t_star']):.4f}")
        return [im]

    animation = FuncAnimation(fig, update, frames=len(files), interval=1000 / fps, blit=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + output.stem + ".", suffix=".gif", dir=output.parent)
    os.close(fd)
    try:
        animation.save(temporary, writer=PillowWriter(fps=fps))
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
        fig.clear()
    return output
