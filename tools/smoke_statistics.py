"""Exercise the actual Tk statistics controls on an existing saved run.

No flow is calculated here. The source files must remain byte-for-byte intact.
Run against an installed wheel on a display, not an editable source checkout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import zipfile

import aerokinetic
from aerokinetic.gui import Application
from aerokinetic.temporal import read_run
from PIL import ImageGrab


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert Path(aerokinetic.__file__).is_relative_to(Path(sys.prefix)), aerokinetic.__file__
    args.output.mkdir(parents=True, exist_ok=False)
    original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.directory.rglob("*")
                if p.is_file() and not any(s.startswith("analysis_") for s in p.relative_to(args.directory).parts)}
    errors, notices = [], []
    messagebox.showerror = lambda *a, **kw: errors.append(str(a))
    messagebox.showinfo = lambda *a, **kw: notices.append(str(a))
    root = tk.Tk()
    root.report_callback_exception = lambda *a: errors.append(str(a))
    app = Application(root)
    filedialog.askdirectory = lambda **kw: str(args.directory)
    app.open_output()
    assert app.temporal_report is not None and not errors, errors
    expected, _, _ = read_run(args.directory)
    assert float(app.variables["reynolds"].get()) == expected["reynolds"]
    assert float(app.variables["alpha_deg"].get()) == expected["alpha_deg"]
    assert app.history_average_after == expected["original_average_after_t_star"]
    for item, key in zip(app.statistics_table.get_children(), ("cl", "cd", "cm")):
        actual = float(app.statistics_table.item(item, "values")[1])
        assert abs(actual - expected["series"][key]["mean"]) <= max(1e-12, abs(actual)*5e-6)
    times = expected["series"]["cl"]
    new_cutoff = (times["selected_t_first"] + times["selected_t_last"]) / 2
    app.analysis_cutoff.set(str(new_cutoff).replace(".", ","))
    app.refresh_statistics()
    assert not errors, errors
    assert app.temporal_report["requested_average_after_t_star"] == new_cutoff
    app.tabs.select(app.tab_frames["statistics"])
    root.update()
    ImageGrab.grab().save(args.output / "statistics.png")
    previous = set(args.directory.glob("analysis_*"))
    # Invoke the real control, including Tk's registered command callback.
    bar = app.tab_frames["statistics"].winfo_children()[0]
    button = next(w for w in bar.winfo_children() if isinstance(w, ttk.Button) and str(w.cget("text")) == "Отчёт и архив для проверки")
    button.invoke()
    assert app.busy and app.worker
    started = time.monotonic()
    while app.busy or app.worker.is_alive():
        root.update()
        if errors or time.monotonic() - started > 45:
            raise AssertionError(errors or "Analysis GUI timeout")
        time.sleep(0.02)
    assert not errors and notices, (errors, notices)
    created = set(args.directory.glob("analysis_*")) - previous
    assert len(created) == 1
    folder = created.pop()
    with zipfile.ZipFile(folder / "FOR_REVIEW.zip") as archive:
        assert archive.testzip() is None
        assert archive.read("run/history.csv") == (args.directory / "history.csv").read_bytes()
        report = json.loads(archive.read("diagnostics/temporal_diagnostics.json"))
        assert report["requested_average_after_t_star"] == new_cutoff
        assert report["temporal_convergence"] == "not_certified"
        assert len([n for n in archive.namelist() if n.endswith(".npz")]) <= 1
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in original.items())
    # An invalid window must not leave a stale report available for export.
    app.analysis_cutoff.set("99999999")
    app.refresh_statistics(quiet=True)
    assert app.temporal_report is None
    app.refresh_statistics(reset_cutoff=True)
    assert app.temporal_report["requested_average_after_t_star"] == expected["requested_average_after_t_star"]
    root.destroy()
    result = {"status": "passed", "installed_package": aerokinetic.__file__,
              "version": aerokinetic.__version__, "callback_errors": errors,
              "report_folder": str(folder), "original_files_unchanged": True,
              "cutoff_changed_for_analysis_only": True, "real_export_button": True,
              "invalid_window_clears_stale_report": True}
    (args.output / "CHECK.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
