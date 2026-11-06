"""Offline descriptive diagnostics of unsteady loads.

This module also runs as a standalone script in an existing 0.2 environment.
It never changes the solver, the chosen averaging cutoff or the input files.
Block means are descriptive: no independence or confidence interval is assumed.
An autocorrelation estimate requires stationarity; it cannot establish it.
"""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import uuid
import zipfile

import numpy as np


def correlation_estimate(values, dt):
    """Initial positive, monotone pairs of the biased sample autocorrelation.

    tau_samples = 1 + 2 sum(rho_lag). This variance-inflation estimate is
    conservative for negative correlations and only heuristic for a transient
    or a short periodic trace. No confidence interval follows from this alone.
    """
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 16:
        return {"status": "too_few_samples", "tau_samples": None, "effective_samples_heuristic": None}
    centered = values - values.mean()
    variance = float(np.dot(centered, centered))
    if variance == 0:
        return {"status": "constant_observed_signal", "tau_samples": 1.0,
                "effective_samples_heuristic": float(n), "correlation_span_t_star": float(dt)}
    size = 1 << (2 * n - 1).bit_length()
    transformed = np.fft.rfft(centered, n=size)
    covariance = np.fft.irfft(transformed * transformed.conjugate(), n=size)[:n]
    rho = covariance / covariance[0]
    previous, total, lag = math.inf, 0.0, 0
    exhausted = True
    for k in range(1, n // 2 - 1, 2):
        pair = float(rho[k] + rho[k + 1])
        if pair <= 0:
            exhausted = False
            break
        previous = min(previous, pair)
        total += previous
        lag = k + 1
    tau = max(1.0, 1.0 + 2.0 * total)
    return {"status": "heuristic_requires_stationarity", "tau_samples": tau,
            "effective_samples_heuristic": float(n / tau),
            "correlation_span_t_star": float(dt * tau), "last_positive_pair_lag": lag,
            "positive_tail_reached_half_record": exhausted}


def diagnose_series(times, values, average_after=0.0, block_count=8):
    times, values = np.asarray(times, dtype=float), np.asarray(values, dtype=float)
    if times.ndim != 1 or values.shape != times.shape or len(times) < 2:
        raise ValueError("Need matching one-dimensional arrays with at least two samples")
    if not np.isfinite(times).all() or not np.isfinite(values).all() or not math.isfinite(average_after):
        raise ValueError("Non-finite time or load values are invalid")
    increments = np.diff(times)
    if not (increments > 0).all():
        raise ValueError("History times must increase strictly; duplicates are invalid")
    dt = float(np.median(increments))
    if not np.allclose(increments, dt, rtol=1e-6, atol=1e-12):
        raise ValueError("These diagnostics require uniformly spaced samples; history has gaps")
    if type(block_count) is not int or block_count < 2:
        raise ValueError("block_count must be an integer >=2")
    keep = times >= average_after
    selected_t, selected = times[keep], values[keep]
    if len(selected) < 2:
        raise ValueError("Need at least two samples after the chosen averaging cutoff")
    # Eight points/block is a display choice, not an independence criterion.
    count = min(block_count, max(1, len(selected) // 8))
    chunks = np.array_split(np.arange(len(selected)), count)
    blocks = [{"t_first": float(selected_t[idx[0]]), "t_last": float(selected_t[idx[-1]]),
               "samples": len(idx), "mean": float(selected[idx].mean()),
               "rms_fluctuation": float(selected[idx].std())} for idx in chunks]
    half = len(selected) // 2
    mean_first, mean_last = float(selected[:half].mean()), float(selected[half:].mean())
    return {"samples": len(selected), "sample_dt_t_star": dt,
            "selected_t_first": float(selected_t[0]), "selected_t_last": float(selected_t[-1]),
            "selected_interval_t_star": float(selected_t[-1] - selected_t[0]),
            "mean": float(selected.mean()), "rms_fluctuation": float(selected.std()),
            "minimum": float(selected.min()), "maximum": float(selected.max()),
            "first_half_mean": mean_first, "last_half_mean": mean_last,
            "first_half_rms_fluctuation": float(selected[:half].std()),
            "last_half_rms_fluctuation": float(selected[half:].std()),
            "last_minus_first_half_mean": mean_last - mean_first, "blocks": blocks,
            "block_mean_range": float(max(b["mean"] for b in blocks) - min(b["mean"] for b in blocks)),
            "correlation": correlation_estimate(selected, dt),
            "confidence_interval": None, "temporal_convergence": "not_certified"}


def read_run(directory, average_after=None):
    directory = Path(directory)
    required = ("history.csv", "metadata.json", "summary.json")
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise ValueError("Choose a result folder containing: " + ", ".join(missing))
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8-sig"))
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8-sig"))
    if summary.get("status") == "running":
        raise ValueError("Stop/save or finish the calculation before taking a result snapshot")
    with (directory / "history.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    try:
        times = np.array([float(row["t_star"]) for row in rows])
        loads = {name: np.array([float(row[name]) for row in rows]) for name in ("cl", "cd", "cm")}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("History needs complete numeric t_star, cl, cd, cm columns") from error
    cutoff = float(summary.get("average_after_t_star", 0.0) if average_after is None else average_after)
    series = {name: diagnose_series(times, values, cutoff) for name, values in loads.items()}
    diagnostics = {}
    for name in ("local_mach_max", "density_error_max"):
        numbers = [float(row[name]) for row in rows if row.get(name) not in (None, "")]
        if numbers and not np.isfinite(numbers).all():
            raise ValueError("History contains non-finite field diagnostics")
        diagnostics[name] = max(numbers) if numbers else None
    report = {
        "format": "aerokinetic_temporal_diagnostics", "schema_version": 1,
        "run_status": summary.get("status", "unknown"), "case_fingerprint": metadata.get("case_fingerprint"),
        "reynolds": metadata.get("flow", {}).get("reynolds"),
        "alpha_deg": metadata.get("flow", {}).get("alpha_deg"),
        "cells_per_chord": metadata.get("grid", {}).get("cells_per_chord"),
        "fine_cells_per_chord": metadata.get("plan", {}).get("fine_cells_per_chord"),
        "force_time_convention": metadata.get("force_time"),
        "termination_reason":summary.get('termination_reason'),
        "steady_residual_criterion_met":summary.get('steady_residual_criterion_met',False),
        "last_residuals":summary.get('last_residuals'),
        "requested_average_after_t_star": cutoff,
        "original_average_after_t_star": summary.get("average_after_t_star"),
        "last_saved_force_time_t_star": float(times[-1]),
        "calculated_final_time_t_star": summary.get("t_star"), "series": series,
        "sampled_field_diagnostics": diagnostics,
        "spatial_convergence": "not_assessed", "temporal_convergence": "not_certified",
        "experimental_validation": "not_performed",
        "limitations": [
            "Large instantaneous oscillations do not alone prove numerical divergence.",
            "The physical or numerical origin of oscillations is not classified by this tool.",
            "Block statistics are descriptive; the blocks are not assumed independent.",
            "Autocorrelation/effective sample counts assume stationarity and a sufficiently long record.",
            "No confidence interval or accepted error tolerance has been established.",
            "Sampled density/Mach diagnostics do not certify behavior between saved samples.",
            "This analysis does not validate the mesh, domain, span, timestep/Mach or wind-tunnel conditions.",
        ],
    }
    return report, times, loads


def write_analysis(directory, output=None, average_after=None, open_report=False):
    directory = Path(directory)
    report, times, loads = read_run(directory, average_after)
    output = Path(output) if output else directory / ("analysis_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new/empty output folder for this diagnostic report")
    output.mkdir(parents=True, exist_ok=True)
    text = ["AEROKINETIC: ДИАГНОСТИКА ВРЕМЕННОЙ ИСТОРИИ", "",
            f"Re={report['reynolds']}; alpha={report['alpha_deg']}°; ячеек/хорду={report['cells_per_chord']}",
            f"Состояние расчёта: {report['run_status']}",
            f"Конец расчёта tU/c={report['calculated_final_time_t_star']}; среднее после {report['requested_average_after_t_star']}",
            "Остановка по заданному условию не подтверждает аэродинамическую точность.",
            f"Причина остановки: {report.get('termination_reason')}", ""]
    for name, values in report["series"].items():
        corr = values["correlation"]
        text += [name.upper(),
                 f"Выборок: {values['samples']}; среднее: {values['mean']:.9g}; RMS пульсаций: {values['rms_fluctuation']:.9g}",
                 f"Минимум/максимум в выбранном окне: {values['minimum']:.9g} / {values['maximum']:.9g}",
                 f"Среднее первой половины: {values['first_half_mean']:.9g}",
                 f"Среднее второй половины: {values['last_half_mean']:.9g}",
                 f"Изменение среднего между половинами: {values['last_minus_first_half_mean']:+.9g}",
                 f"RMS первой/второй половин: {values['first_half_rms_fluctuation']:.9g} / {values['last_half_rms_fluctuation']:.9g}",
                 f"Размах средних по блокам: {values['block_mean_range']:.9g}",
                 f"Приближённое эффективное число выборок: {corr.get('effective_samples_heuristic')}",
                 "Последняя оценка применима только при стационарной статистике и длинной записи.",
                 "Блоки: t_начало, t_конец, среднее, RMS"]
        text += [f"  {b['t_first']:.7g}, {b['t_last']:.7g}, {b['mean']:.9g}, {b['rms_fluctuation']:.9g}" for b in values["blocks"]]
        text += [""]
    text += ["ДИАГНОСТИКА ПОЛЯ ПО ЗАПИСАННЫМ ВЫБОРКАМ",
             json.dumps(report["sampled_field_diagnostics"], ensure_ascii=False), "",
             "СТАТУС: временная сходимость не подтверждена; пространственная не оценивалась.",
             "Мгновенный Cl при нестационарном течении может продолжать колебаться.",
             "RMS пульсаций не является ошибкой среднего. Доверительный интервал не вычислен.",
             "Происхождение колебаний (физика/старт/численная ошибка) этим инструментом не установлено.",
             "Нужны достаточно длинная статистика и независимые проверки сетки, области и размаха.",
             "Исходные результаты и расчётное ядро не изменялись.", "",
             "Для разбора в чате приложите созданный FOR_REVIEW.zip."]
    text_path = output / "REPORT_RU.txt"
    if report.get("fine_cells_per_chord") is not None:
        text[2] += f" (крупная сетка); мелкая: {report['fine_cells_per_chord']} ячеек/хорду"
        text += ["Временная привязка сил: " + str(report.get("force_time_convention")), ""]
    text_path.write_text("\n".join(text) + "\n", encoding="utf-8")
    (output / "temporal_diagnostics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(10, 8), constrained_layout=True)
    FigureCanvasAgg(fig)
    cutoff = report["requested_average_after_t_star"]
    for ax, name in zip(fig.subplots(3, 1, sharex=True), ("cl", "cd", "cm")):
        ax.plot(times, loads[name], color="#23618b", linewidth=0.9, label="Выборки коэффициентов")
        selected = times >= cutoff
        chosen = loads[name][selected]
        ax.plot(times[selected], np.cumsum(chosen) / np.arange(1, len(chosen) + 1), color="#ca7220", linewidth=1.5, label="Накапливаемое среднее после отсечки")
        for block in report["series"][name]["blocks"]:
            ax.plot([block["t_first"], block["t_last"]], [block["mean"]] * 2, color="#248646", linewidth=2)
        ax.axvline(cutoff, linestyle=":", color="0.5", linewidth=0.8)
        ax.set_ylabel(name.capitalize())
        ax.grid(alpha=0.25)
    fig.axes[0].legend(fontsize=8)
    ax.set_xlabel("tU∞/c")
    fig.suptitle("История сил и средние по временным окнам\nДиагностика; точность и сходимость не подтверждены", fontsize=11)
    fig.savefig(output / "forces.png", dpi=150)
    fig.clear()
    manifest = {}
    with zipfile.ZipFile(output / "FOR_REVIEW.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        items = [(directory / name, "run/" + name) for name in ("project.json", "metadata.json", "summary.json", "history.csv", "residuals.csv")]
        frames = sorted((directory / "frames").glob("frame_*.npz"))
        if frames:
            items.append((frames[-1], "run/frames/" + frames[-1].name))
        items += [(output / name, "diagnostics/" + name) for name in ("REPORT_RU.txt", "temporal_diagnostics.json", "forces.png")]
        for source, target in items:
            if source.is_file():
                data = source.read_bytes()
                archive.writestr(target, data)
                manifest[target] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        archive.writestr("SHA256.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("Analysis:", text_path, flush=True)
    print("Attach this file for review:", output / "FOR_REVIEW.zip", flush=True)
    if open_report and os.name == "nt":
        os.startfile(text_path)
    return output, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--average-after", type=float)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    if args.directory is None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="Выберите папку результата с history.csv, metadata.json и summary.json")
        root.destroy()
        if not folder:
            return
        args.directory = Path(folder)
    write_analysis(args.directory, args.output, args.average_after, not args.no_open)


if __name__ == "__main__":
    main()
