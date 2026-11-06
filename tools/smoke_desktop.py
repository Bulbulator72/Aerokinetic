"""Exercise the real Tk interface on a display; coarse flow, no accuracy claim.

Run with an installed aerokinetic package and an empty --output directory.
On Linux this requires DISPLAY; the regular offline pytest suite is headless.
"""
import argparse
import csv
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageGrab
from aerokinetic import Project
from aerokinetic.gui import Application


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    errors = []
    messagebox.showerror = lambda *a, **k: errors.append(a)
    root = tk.Tk()
    app = Application(root)
    app.populate(Project.from_dict({"geometry": {"naca0012": {"n_side": 31}},
        "flow": {"alpha_deg": 3, "physical_chord_m": 0.15},
        "grid": {"cells_per_chord": 32, "upstream_chords": 1, "downstream_chords": 2,
                 "transverse_chords": 1, "memory_limit_gib": 1},
        "run": {"duration_convective": 0.4, "sample_every": 8, "frame_every": 10,
                "check_every": 8, "checkpoint_every": 20, "average_after": 0.02}}))
    app.preview()
    filedialog.askdirectory = lambda **kwargs: str(args.output)
    app.start()
    phase = "running"
    started = time.monotonic()
    def callback_error(kind, error, trace):
        errors.append(str(error))
        root.quit()
    root.report_callback_exception = callback_error
    def inspect():
        nonlocal phase
        if errors or time.monotonic() - started > 120:
            app.stopping.set()
            root.quit()
            return
        alive = app.worker is not None and app.worker.is_alive()
        if phase == "running" and len(app.records) >= 2 and alive:
            app.stop()
            phase = "stopping"
        elif phase == "stopping" and not alive and not app.busy:
            summary = json.loads((app.output / "summary.json").read_text())
            assert summary["status"] == "stopped" and summary["steps"] > 0
            assert (app.output / "checkpoint.npz").is_file()
            ImageGrab.grab().save(args.output / "paused.png")
            filedialog.askdirectory = lambda **kwargs: str(app.output)
            app.resume()
            phase = "resuming"
        elif phase == "resuming" and not alive and not app.busy:
            summary = json.loads((app.output / "summary.json").read_text())
            assert summary["status"] == "completed" and summary["steps"] == 320
            with (app.output / "history.csv").open() as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == len({r["step"] for r in rows}) == 40
            app.field_choice.set("Давление p−p∞, Па")
            app.tabs.select(app.tab_frames["field"])
            app.draw_frame()
            root.update_idletasks()
            ImageGrab.grab().save(args.output / "completed_pressure.png")
            filedialog.asksaveasfilename = lambda **kwargs: str(args.output / "pressure_pa.gif")
            app.export_animation()
            phase = "animation"
        elif phase == "animation" and not alive and not app.busy:
            assert str(app.variables["reynolds"].get()) == "10000.0"
            assert str(app.stop_button.cget("state")) == "disabled"
            with Image.open(args.output / "pressure_pa.gif") as gif:
                assert gif.n_frames >= 2
            print(json.dumps({"status": "passed", "stopped_and_resumed": True,
                              "output": str(app.output), "samples": 40, "pressure_gif": True}))
            phase = "done"
            root.quit()
            return
        root.after(100, inspect)
    root.after(100, inspect)
    root.after(120000, root.quit)
    root.mainloop()
    if app.worker and app.worker.is_alive():
        app.stopping.set()
        app.worker.join(timeout=60)
    root.destroy()
    assert phase == "done" and not errors, (phase, errors)


if __name__ == "__main__":
    main()
