"""Offline, resumable NACA0012 study using an isolated installed research wheel."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile

from bootstrap import StudyAlreadyRunning, check_core, digest, missing_packages, requirements, study_lock, write


class Tee:
    def __init__(self, console, stream):
        self.console, self.stream = console, stream
    def write(self, value):
        self.console.write(value); self.stream.write(value); self.flush()
    def flush(self):
        self.console.flush(); self.stream.flush()


def find_portable(program):
    direct = [program / "_internal", program, program / "AeroKinetic/_internal",
              program / "dist/AeroKinetic/_internal"]
    for root in (program, program.parent):
        for pattern in ("*/_internal/portable/toolchain.json", "*/AeroKinetic/_internal/portable/toolchain.json"):
            direct.extend(path.parent.parent for path in sorted(root.glob(pattern)))
    return next((path for path in direct if (path / "portable/toolchain.json").is_file()), None)


def activate(args):
    sys.path.insert(0, str(args.target))
    identity = json.loads((args.kit / "CORE_IDENTITY.json").read_text(encoding="utf-8"))
    check_core(args.target, identity)
    missing = missing_packages(requirements(args.kit / "source/requirements-offline-py312.txt"))
    if missing:
        raise RuntimeError("Версии зависимостей отличаются от закреплённых: " + json.dumps(missing))
    if not getattr(sys, "_aerokinetic_network_denied", False):
        raise RuntimeError("Не установлен сетевой guard. Запускайте серию через bootstrap/BAT.")
    cache = args.workspace / "study/cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.update(XDG_CACHE_HOME=str(cache / "xdg"), OPENBLAS_NUM_THREADS="1", OMP_WAIT_POLICY="PASSIVE")
    bundled = find_portable(args.program_root)
    compiler = {"mode": "local_pystencils_configuration"}
    if sys.platform == "win32" and bundled is not None:
        spec = importlib.util.spec_from_file_location("ak_research_portable_runtime", args.kit / "ak_portable_runtime.py")
        adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
        state = adapter.configure(bundled, cache)
        compiler.update(mode="existing_portable_toolchain", compiler=state["compiler"], jit_include=state["jit_include"])
        # Keep DLL-directory handles alive throughout this process.
        args._portable_adapter = adapter
    else:
        original = next((p / "pystencils.json" for p in (args.program_root, args.program_root.parent)
                         if (p / "pystencils.json").is_file()), args.program_root / "pystencils.json")
        configuration = json.loads(original.read_text(encoding="utf-8-sig")) if original.is_file() else {}
        configuration["cache"] = {"object_cache": str(cache / "jit"), "clear_cache_on_start": False}
        write(cache / "pystencils.json", configuration)
        os.environ.update(PYSTENCILS_CONFIG=str(cache / "pystencils.json"),
            PYSTENCILS_CACHE_DIR=str(cache / "symbolic"), MPLCONFIGDIR=str(cache / "matplotlib"),
            XDG_CACHE_HOME=str(cache / "xdg"), OPENBLAS_NUM_THREADS="1", OMP_WAIT_POLICY="PASSIVE")
        compiler["configuration"] = {"compiler": {k:v for k, v in configuration.get("compiler", {}).items()
                                      if k in ("os", "command", "flags", "msvc_version", "arch", "restrict_qualifier")}}
    import aerokinetic
    if aerokinetic.__version__ != "0.5.3" or Path(aerokinetic.__file__).resolve().parent != args.target / "aerokinetic":
        raise RuntimeError("Загружена другая версия AeroKinetic.")
    return identity, compiler


def resources():
    total = available = None
    if os.name == "nt":
        import ctypes
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available",
                                                       "virtual_total", "virtual_available", "extended")]
        memory = Memory(); memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            total, available = memory.total, memory.available
    else:
        try:
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            available = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            maximum = Path("/sys/fs/cgroup/memory.max")
            if maximum.is_file() and maximum.read_text().strip() != "max":
                cap = int(maximum.read_text()); current = int(Path("/sys/fs/cgroup/memory.current").read_text())
                total = min(total, cap); available = min(available, cap - current)
        except (OSError, ValueError):
            pass
    return {"logical_cpus": os.cpu_count(), "physical_memory_bytes": total, "available_memory_bytes": available}


def make_projects(args):
    from aerokinetic import Project
    from aerokinetic.convergence import StopCriteria
    base = Project.load(args.kit / "base_project.json")
    grids = (16, 32) if args.smoke else tuple(int(x.strip()) for x in args.grids.split(","))
    if not grids or len(set(grids)) != len(grids) or any(n not in (16, 32, 64, 128) for n in grids):
        raise ValueError("Сетки: 32,64; дополнительно допускается 128. N16 используется только для smoke.")
    if not args.smoke and (16 in grids or not math.isfinite(args.duration) or args.duration < 12):
        raise ValueError("Для исследовательской серии нужны N>=32 и длительность не менее 12 tU/c.")
    if args.threads < 1 or args.threads > (os.cpu_count() or 1):
        raise ValueError("Число потоков должно быть от 1 до числа логических CPU.")
    memory = resources()["physical_memory_bytes"]
    limit = min(96., .75 * memory / 2**30) if memory else 6.
    result = {}
    for n in grids:
        grid = replace(base.grid, cells_per_chord=n, openmp_threads=args.threads,
                       farfield="exterior_regularized_fd_v1", memory_limit_gib=limit)
        run = replace(base.run, duration_convective=.12 if args.smoke else args.duration,
            average_after=.06 if args.smoke else 6., sample_every=1 if args.smoke else n // 4,
            frame_every=12 if args.smoke else 75 * n, check_every=4 if args.smoke else 4 * n,
            checkpoint_every=8 if args.smoke else 25 * n,
            termination=StopCriteria(mode="time", wall_time_limit_s=0))
        result[f"N{n}"] = replace(base, grid=grid, run=run)
    return result


def stop_path(args):
    return args.workspace / "study/STOP_REQUESTED"


def install_signal_handlers(args):
    def request(*_):
        stop_path(args).touch()
        print("Запрошена остановка. Ожидается безопасное сохранение контрольного состояния.", flush=True)
    signal.signal(signal.SIGINT, request)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request)


class FileStop:
    def __init__(self, path):
        self.path = path
    def is_set(self):
        return self.path.exists()


def command(args, worker, case=None):
    values = [sys.executable, "-u", str(args.kit / "run_suite.py"), "--kit", str(args.kit),
              "--workspace", str(args.workspace), "--program-root", str(args.program_root),
              "--target", str(args.target), "--threads", str(args.threads), "--worker", worker]
    if case:
        values += ["--case", case]
    return values


def child(args, worker, case=None):
    process = subprocess.Popen(command(args, worker, case), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    activity = [time.monotonic()]
    def pump():
        for line in process.stdout:
            activity[0] = time.monotonic(); print(line, end="", flush=True)
    reader = threading.Thread(target=pump, daemon=True); reader.start()
    while process.poll() is None:
        if time.monotonic() - activity[0] >= 20:
            print("Подготовка / выполнение продолжается. Первая компиляция может занять несколько минут.", flush=True)
            activity[0] = time.monotonic()
        time.sleep(.25)
    reader.join(); process.stdout.close()
    return process.returncode


def preflight(args):
    import numpy as np
    from aerokinetic import Project, Solver
    from pystencils import get_code_str
    from pystencils.cpu.cpujit import get_compiler_config
    directory = args.workspace / "study/preflight"
    directory.mkdir(parents=True, exist_ok=True)
    completed = directory / "CHECKS.json"
    if completed.is_file() and json.loads(completed.read_text())["passed"]:
        print("Короткая проверка ядра уже пройдена.", flush=True)
        return 0
    print("Короткая проверка: компиляция, балансы, потоки и продолжение в новом процессе.", flush=True)
    flags = str(get_compiler_config().get("flags", ""))
    if args.threads > 1 and "openmp" not in flags.lower():
        raise RuntimeError("В конфигурации компилятора не включён OpenMP. Пришлите FOR_REVIEW.zip; запуск с одним потоком не подменяет заданную серию.")
    project = Project.load(args.kit / "base_project.json")
    project = replace(project, grid=replace(project.grid, cells_per_chord=16, memory_limit_gib=2,
                                            openmp_threads=1))
    one = Solver(project.section, project.flow, project.grid)
    many = Solver(project.section, project.flow, replace(project.grid, openmp_threads=args.threads))
    spec = importlib.util.spec_from_file_location("ak_independent_budget", args.kit / "fluid_budget.py")
    audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
    observer = audit.FluidBudget(many)
    pdf_error = load_error = balance_error = mass_error = force_error = torque_error = 0.
    for step in range(32):
        if stop_path(args).exists():
            return 2
        reference = one.step(sample=True)
        if step < 8:
            measured, budget = observer.observe()
            balance_error = max(balance_error, max(abs(x) for x in budget["fluid_budget_per_cell"]))
            assert max(abs(x) for x in budget["physical_fluid_overwrite_budget"]) == 0
            wall = np.asarray(budget["wall_flux"]); body = measured["element_lattice_loads"].sum(axis=0)
            mass_error = max(mass_error, abs(measured["wall_mass_exchange_lattice"] - wall[0]))
            force_error = max(force_error, float(np.max(abs(body[:3] + wall[1:4]))))
            torque_error = max(torque_error, abs(body[3] + wall[4]))
        else:
            measured = many.step(sample=True)
        load_error = max(load_error, max(abs(reference[k] - measured[k]) for k in ("cl", "cd", "cm")))
    for a, b in ((one._stepper.pdf_array_name, many._stepper.pdf_array_name),
                 (one._stepper._tmp_arr_name, many._stepper._tmp_arr_name)):
        pdf_error = max(pdf_error, float(np.max(abs(one._stepper.data_handling.cpu_arrays[a] - many._stepper.data_handling.cpu_arrays[b]))))
    many.save_checkpoint(directory / "checkpoint.npz")
    records = [many.step(sample=True) for _ in range(9)]
    keys = ("step", "t_star", "cl", "cd", "cm", "wall_mass_exchange_lattice",
            "wall_mass_exchange_accumulated", "exterior_source_mass_accumulated")
    write(directory / "expected.json", {"loads": [{k:r[k] for k in keys} for r in records],
          "boundary": many.boundary_diagnostics()})
    np.savez(directory / "expected.npz", src=many._stepper.data_handling.cpu_arrays[many._stepper.pdf_array_name],
             dst=many._stepper.data_handling.cpu_arrays[many._stepper._tmp_arr_name], initial=many._exterior.initial)
    many.save_frame(directory / "frame.npz")
    with np.load(directory / "frame.npz", allow_pickle=False) as frame:
        assert frame["speed"].shape == many.plan.shape[:2]
    kernels = [str(get_code_str(k.ast)) for k in many._stepper._lbmKernels]
    assert len(kernels) == 2
    if args.threads > 1:
        assert all("#pragma omp parallel" in text and f"num_threads({args.threads})" in text for text in kernels)
    assert pdf_error <= 2e-12 and load_error <= 2e-10 and balance_error <= 2e-12
    assert mass_error <= 2e-10 and force_error <= 2e-10 and torque_error <= 2e-9
    if child(args, "restart"):
        raise RuntimeError("Не пройдена проверка продолжения в новом процессе.")
    write(completed, {"passed": True, "threads": args.threads, "cpu1_vs_many_pdf_max_error": pdf_error,
          "cpu1_vs_many_load_max_error": load_error, "fluid_budget_per_cell_max_error": balance_error,
          "wall_mass_max_error": mass_error, "wall_force_max_error": force_error, "wall_torque_max_error": torque_error,
          "fresh_process_restart": "exact", "openmp_kernels": args.threads > 1,
          "compiler_flags": flags,
          "scope": "Implementation checks; not airfoil accuracy"})
    print("Короткая проверка пройдена.", flush=True)
    return 0


def restart_check(args):
    import numpy as np
    from aerokinetic import Solver
    directory = args.workspace / "study/preflight"
    solver = Solver.from_checkpoint(directory / "checkpoint.npz")
    expected = json.loads((directory / "expected.json").read_text())
    for row in expected["loads"]:
        measured = solver.step(sample=True)
        assert all(measured[k] == value for k, value in row.items())
    with np.load(directory / "expected.npz", allow_pickle=False) as fields:
        for name, key in ((solver._stepper.pdf_array_name, "src"), (solver._stepper._tmp_arr_name, "dst")):
            np.testing.assert_array_equal(solver._stepper.data_handling.cpu_arrays[name], fields[key])
        np.testing.assert_array_equal(solver._exterior.initial, fields["initial"])
    assert json.loads(json.dumps(solver.boundary_diagnostics())) == expected["boundary"]
    print("Продолжение в новом процессе: PDF, нагрузки, часы и накопления совпали точно.", flush=True)
    return 0


def calculate(args):
    from aerokinetic import Project, Solver
    plan = json.loads((args.workspace / "study/PLAN.json").read_text(encoding="utf-8"))
    entry = plan["cases"][args.case]
    project_file = args.workspace / "study/projects" / (args.case + ".json")
    if digest(project_file) != entry["project_sha256"]:
        raise RuntimeError("Параметры активной серии изменены: " + args.case)
    project = Project.load(project_file)
    output = args.workspace / "study/cases" / args.case
    if (output / "metadata.json").is_file():
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        if metadata["case_fingerprint"] != project.case_fingerprint or metadata["run"] != asdict(project.run):
            raise RuntimeError("Постановка сохранённого расчёта не совпадает с планом.")
        if metadata["status"] == "completed":
            summary = json.loads((output / "summary.json").read_text())
            if summary["steps"] != entry["end_step"] or abs(summary["t_star"] - project.run.duration_convective) > 1e-12:
                raise RuntimeError("Завершённый расчёт не достиг заданного срока.")
            print(args.case + ": уже завершён, повторный расчёт не нужен.", flush=True)
            return 0
        if metadata["status"] == "failed":
            raise RuntimeError("Сохранён численно неудачный расчёт " + args.case + ". Пришлите FOR_REVIEW.zip; данные сохранены.")
        if not (output / "checkpoint.npz").is_file():
            raise RuntimeError("Прерванный расчёт не успел сохранить checkpoint: " + str(output))
        resume = True
    else:
        resume = False
    memory = resources()["available_memory_bytes"]
    required = (1.35 * entry["plan"]["estimated_memory_gib"] + .3) * 2**30
    if memory is not None and memory < required:
        raise RuntimeError("Недостаточно свободной RAM для " + args.case + "; сетка не изменялась.")
    disk = max(2., 3 * entry["plan"]["estimated_memory_gib"]) * 2**30
    if shutil.disk_usage(args.workspace).free < disk:
        raise RuntimeError("Недостаточно места на диске для атомарных checkpoint " + args.case)
    print(f"{args.case}: {'продолжение' if resume else 'подготовка'}; {project.grid.openmp_threads} потоков; "
          f"план RAM {entry['plan']['estimated_memory_gib']:.2f} ГиБ; конец tU/c={project.run.duration_convective:g}.", flush=True)
    started = time.monotonic(); start_step = [None]; last_print = [-math.inf]
    def progress(solver, record):
        if start_step[0] is None:
            start_step[0] = solver.steps
        current = solver.steps * solver.plan.dt_convective
        if current - last_print[0] >= .25 or solver.steps == entry["end_step"]:
            last_print[0] = current
            print(f"{args.case}: tU/c={current:.3f}/{project.run.duration_convective:g}; "
                  f"Cl={record['cl']:.6f} Cd={record['cd']:.6f} Cm={record['cm']:.6f}; "
                  f"прошло {(time.monotonic()-started)/60:.1f} мин.", flush=True)
        return True
    install_signal_handlers(args)
    if resume:
        result = Solver.resume_run(output, callback=progress, stop_event=FileStop(stop_path(args)))
    else:
        solver = Solver(project.section, project.flow, project.grid)
        result = solver.run(output=output, callback=progress, stop_event=FileStop(stop_path(args)), **asdict(project.run))
    if result["status"] == "stopped":
        print(args.case + ": остановлен с сохранением. Этот же BAT продолжит расчёт.", flush=True)
        return 2
    if result["status"] != "completed" or result["steps"] != entry["end_step"]:
        raise RuntimeError("Не выполнен полный заданный расчёт.")
    return 0


def analysis(args, projects):
    import csv
    import numpy as np
    from aerokinetic.temporal import diagnose_series
    report = {"experimental_validation": "inconclusive", "grid_convergence": "not_certified", "cases": {},
              "scope": "Two-grid differences and descriptive statistics; not Richardson/GCI or a validated polar"}
    tunnel_path = args.kit / "ohtake_re30000.csv"
    with tunnel_path.open(newline="", encoding="utf-8-sig") as stream:
        tunnel = list(csv.DictReader(stream))
    experiment = {}
    for key, column in (("cl", "cl"), ("cd", "cd"), ("cm", "cm_c4")):
        rows = [r for r in tunnel if r["coefficient"] == column and float(r["reynolds"]) == 30000]
        x, y = [float(r["alpha_deg"]) for r in rows], [float(r["value"]) for r in rows]
        assert all(b > a for a, b in zip(x, x[1:])) and x[0] <= 4 <= x[-1]
        experiment[key] = float(np.interp(4, x, y))
    report["wind_tunnel"] = {"doi": "10.2322/jjsass.55.439", "reynolds": 30000, "alpha_deg": 4,
                              "interpolated": experiment, "csv_sha256": digest(tunnel_path),
                              "uncertainty": "Digitization bounds are not full tunnel uncertainty"}
    curves = {}
    for name, project in projects.items():
        root = args.workspace / "study/cases" / name
        if not (root / "summary.json").is_file():
            continue
        summary = json.loads((root / "summary.json").read_text())
        case = {"run_status": summary["status"], "t_star": summary["t_star"], "steps": summary["steps"],
                "windows": {}, "wall_fallback_links": summary.get("wall_fallback_links")}
        report["cases"][name] = case
        if summary["status"] != "completed":
            continue
        with (root / "history.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        times = np.array([float(r["t_star"]) for r in rows])
        values = {k:np.array([float(r[k]) for r in rows]) for k in ("cl", "cd", "cm")}
        curves[name] = (times, values)
        windows = [(.06, .12)] if args.smoke else [(6., 12.)]
        if not args.smoke and project.run.duration_convective > 12:
            windows.append((12., project.run.duration_convective))
        indexed = {int(r["step"]):r for r in rows}
        for first, last in windows:
            keep = times <= last
            statistics = {k:diagnose_series(times[keep], v[keep], average_after=first) for k, v in values.items()}
            case["windows"][f"{first:g}..{last:g}"] = statistics
            if not args.smoke and first == 6:
                case["tunnel_difference"] = {k:statistics[k]["mean"] - experiment[k] for k in values}
            endpoints = [round(t / project.plan().dt_convective) for t in (first, last)]
            if all(step in indexed for step in endpoints):
                case.setdefault("mass_windows", {})[f"{first:g}..{last:g}"] = {
                    key:float(indexed[endpoints[1]][field]) - float(indexed[endpoints[0]][field])
                    for key, field in (("wall", "wall_mass_exchange_accumulated"),
                                       ("exterior_auxiliary_source", "exterior_source_mass_accumulated"))}
        if not args.smoke and name == "N32":
            reference = json.loads((args.kit / "study_protocol.json").read_text())["linux_n32_reference"]
            errors = {k:abs(case["windows"]["6..12"][k]["mean"] - reference["mean"][k]) for k in values}
            rms = {k:abs(case["windows"]["6..12"][k]["rms_fluctuation"] - reference["rms"][k]) for k in values}
            case["cross_platform_implementation_check"] = {"mean_absolute_error":errors, "rms_absolute_error":rms,
                "proposed_absolute_tolerance":1e-6, "passed":max(*errors.values(), *rms.values()) <= 1e-6,
                "scope":"Same-code reproduction control; not experimental validation"}
    report["complete_requested_cases"] = len(report["cases"]) == len(projects) and all(
        c["run_status"] == "completed" for c in report["cases"].values())
    destination = args.workspace / "study/analysis"
    write(destination / "RESULTS.json", report)
    with (destination / "comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream); writer.writerow(("case", "window", "coefficient", "mean", "RMS", "half_window_drift", "tunnel_interpolation"))
        for name, case in report["cases"].items():
            for window, statistics in case["windows"].items():
                for k, v in statistics.items():
                    writer.writerow((name, window, k, v["mean"], v["rms_fluctuation"], v["last_minus_first_half_mean"],
                                     "" if args.smoke else experiment[k]))
    if curves:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams.update({"svg.fonttype":"none", "svg.hashsalt":"ak-user-study-053"})
        fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
        for axis, k in zip(axes, ("cl", "cd", "cm")):
            for name, (times, values) in curves.items():
                axis.plot(times, values[k], label=name)
            if not args.smoke:
                axis.axhline(experiment[k], color="black", linestyle="--", label="Ohtake2007 interpolation")
            axis.set_ylabel(k.upper()); axis.grid(alpha=.2); axis.legend()
        axes[-1].set_xlabel("t U / c")
        fig.suptitle("Research study: grid and statistical accuracy not established")
        fig.tight_layout(); fig.savefig(destination / "histories.svg", metadata={"Date":None}); plt.close(fig)
        path = destination / "histories.svg"
        path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n", encoding="utf-8")
    return report


def pack(args):
    study = args.workspace / "study"
    files = [p for p in study.rglob("*") if p.is_file() and "cache" not in p.relative_to(study).parts
             and p.suffix in (".json", ".csv", ".log", ".txt", ".svg")]
    for case in (study / "cases").glob("*") if (study / "cases").exists() else ():
        frames = sorted((case / "frames").glob("frame_*.npz"))
        if frames:
            files.append(frames[-1])
    for name in ("PREPARATION.json", "PREPARATION_ERROR.txt", "prepare.log", "network_audit.jsonl"):
        path = args.workspace / name
        if path.is_file():
            files.append(path)
    records = [{"path":p.relative_to(args.workspace).as_posix(), "bytes":p.stat().st_size, "sha256":digest(p)} for p in sorted(set(files))]
    destination = args.workspace / "FOR_REVIEW.zip"
    temporary = destination.with_suffix(".tmp.zip")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=4) as archive:
        for p in sorted(set(files)):
            archive.write(p, p.relative_to(args.workspace).as_posix())
        archive.writestr("FILES_SHA256.json", json.dumps({"files":records}, indent=2) + "\n")
        archive.writestr("READ_ME.txt", "Research output only. Checkpoints, wheels and compiler caches remain on the computer.\n")
    os.replace(temporary, destination)
    print("\nПРИШЛИТЕ ЭТОТ АРХИВ: " + str(destination), flush=True)


def supervisor(args, identity, compiler):
    from aerokinetic.timing import duration_steps
    study = args.workspace / "study"
    study.mkdir(parents=True, exist_ok=True)
    with study_lock(study / "RUNNING.lock"):
        (study / "logs").mkdir(exist_ok=True)
        with (study / "suite.log").open("a", encoding="utf-8", buffering=1) as log:
            console = sys.stdout; sys.stdout = Tee(console, log)
            projects = {}
            try:
                projects = make_projects(args)
                plan_path = study / "PLAN.json"
                settings = {"core_id":identity["id"], "threads":args.threads, "grids":list(projects),
                            "duration":.12 if args.smoke else args.duration, "smoke":args.smoke}
                if plan_path.is_file():
                    plan = json.loads(plan_path.read_text(encoding="utf-8"))
                    if plan["settings"] != settings:
                        raise RuntimeError("Параметры отличаются от активной серии. Для новой серии переименуйте рабочую папку; прежние результаты сохранятся.")
                else:
                    cases = {}
                    for name, project in projects.items():
                        path = study / "projects" / (name + ".json")
                        path.parent.mkdir(exist_ok=True); project.save(path)
                        cases[name] = {"project_sha256":digest(path), "case_fingerprint":project.case_fingerprint,
                                       "plan":project.plan().as_dict(), "end_step":duration_steps(project.run.duration_convective, project.plan().dt_convective)}
                    write(plan_path, {"settings":settings, "cases":cases,
                          "study_protocol":json.loads((args.kit / "study_protocol.json").read_text()),
                          "created_utc":datetime.now(timezone.utc).isoformat(),
                          "initial_jit_cache_empty":not any((study / "cache/jit").glob("*"))})
                write(study / "RUNTIME.json", {"core":identity, "compiler":compiler, "hardware":resources(),
                      "python":sys.version, "platform":platform.platform(), "installed_package_root":str(args.target),
                      "existing_installation_modified":False})
                install_signal_handlers(args)
                code = child(args, "preflight")
                if code:
                    return code
                for name in projects:
                    code = child(args, "case", name)
                    if code:
                        return code
                print("Все заданные расчёты завершены. Сеточная и экспериментальная точность оцениваются отдельно.", flush=True)
                return 0
            except Exception:
                details = traceback.format_exc(); print(details, flush=True)
                write(study / "SUITE_FAILURE.json", {"error":details, "results_preserved":True})
                return 1
            finally:
                try:
                    if projects:
                        try:
                            analysis(args, projects)
                        except Exception:
                            write(study / "ANALYSIS_FAILURE.json", {"error":traceback.format_exc(), "results_preserved":True})
                    pack(args)
                finally:
                    sys.stdout = console


def main():
    parser = argparse.ArgumentParser()
    for name in ("kit", "workspace", "program-root", "target"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--grids", default="32,64")
    parser.add_argument("--duration", type=float, default=12.)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--worker", choices=("preflight", "restart", "case"))
    parser.add_argument("--case")
    args = parser.parse_args()
    for name in ("kit", "workspace", "program_root", "target"):
        setattr(args, name, getattr(args, name).resolve())
    try:
        identity, compiler = activate(args)
        if args.worker == "preflight":
            return preflight(args)
        if args.worker == "restart":
            return restart_check(args)
        if args.worker == "case":
            return calculate(args)
        return supervisor(args, identity, compiler)
    except StudyAlreadyRunning as error:
        print(str(error), flush=True)
        return 3
    except Exception:
        details = traceback.format_exc(); print(details, flush=True)
        directory = args.workspace / "study"
        directory.mkdir(parents=True, exist_ok=True)
        if args.worker:
            write(directory / ("FAILURE_" + (args.case or args.worker) + ".json"), {"error":details, "results_preserved":True})
        else:
            try:
                with study_lock(directory / "RUNNING.lock"):
                    write(directory / "FAILURE_startup.json", {"error":details, "results_preserved":True})
                    pack(args)
            except StudyAlreadyRunning:
                print("Другая серия продолжает работу; её файлы не изменены.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
