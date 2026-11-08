"""Build/install the pinned research core in a private target, without network."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import signal
import shutil
import struct
import subprocess
import sys
import traceback
import uuid
import zipfile


class StudyAlreadyRunning(RuntimeError):
    pass


@contextmanager
def study_lock(path):
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise StudyAlreadyRunning("Эта серия уже запущена в другом окне.") from error
        yield


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def check_core(target, identity):
    for name, expected in identity["package_files"].items():
        path = target / "aerokinetic" / name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError("Изменён или отсутствует файл расчётного ядра: " + name)


def requirements(path):
    return dict(line.strip().split("==", 1) for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#"))


def missing_packages(pins):
    missing = {}
    for name, expected in pins.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            missing[name] = {"required": expected, "installed": actual}
    return missing


def pip(command, log, env):
    print("Локальная подготовка: " + " ".join(command[:3]), flush=True)
    result = subprocess.run([sys.executable, "-m", "pip", *map(str, command)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    log.flush()
    if result.returncode:
        raise RuntimeError("Ошибка локальной сборки/установки. Подробности: " + str(log.name))


def wheelhouse(program):
    candidates = ("downloads/wheels_windows", "downloads/wheels_linux", "wheels_windows", "wheels_linux")
    return next((program / name for name in candidates
                 if (program / name).is_dir() and any((program / name).glob("*.whl"))), None)


def install(kit, workspace, program):
    identity = json.loads((kit / "CORE_IDENTITY.json").read_text(encoding="utf-8"))
    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise RuntimeError("Нужен Python 3.12 x64 из исходной установки AeroKinetic.")
    check_core(kit / "source/src", identity)
    pins = requirements(kit / "source/requirements-offline-py312.txt")
    build_missing = missing_packages({name: pins[name] for name in ("setuptools", "wheel")})
    target = workspace / "runtime" / identity["id"]
    ready = target / "RESEARCH_READY.json"
    if ready.is_file():
        saved = json.loads(ready.read_text())
        if saved["python_abi"] != sys.implementation.cache_tag or saved["platform"] != sys.platform:
            raise RuntimeError("Изолированная среда создана для другой ОС/Python. Используйте новую рабочую папку.")
        check_core(target, identity)
        return target, identity
    if target.exists():
        raise RuntimeError("Незавершённая среда: " + str(target) + ". Переименуйте эту папку и повторите запуск.")
    env = dict(os.environ)
    env.update(PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_CONFIG_FILE=os.devnull,
               PIP_NO_INPUT="1", PIP_NO_CACHE_DIR="1", PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1")
    wheels = workspace / "wheels" / identity["id"]
    wheels.mkdir(parents=True, exist_ok=True)
    build_source = workspace / "build" / uuid.uuid4().hex / "source"
    shutil.copytree(kit / "source", build_source)
    with (workspace / "prepare.log").open("a", encoding="utf-8") as log:
        if build_missing:
            house = wheelhouse(program)
            if house is None:
                raise RuntimeError("Для сборки нужны локальные setuptools/wheel: " + json.dumps(build_missing)
                                   + ". Укажите исходную установку с downloads/wheels_windows; сеть отключена.")
            backend = build_source.parent / "backend"
            pip(["install", "--no-index", "--no-deps", "--no-compile", "--find-links", house, "--target", backend,
                 *(name + "==" + pins[name] for name in ("setuptools", "wheel", "packaging"))], log, env)
            env["PYTHONPATH"] = os.pathsep.join((str(backend), str(kit / "guard")))
        pip(["wheel", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", wheels, build_source], log, env)
        wheel = wheels / "aerokinetic-0.5.3-py3-none-any.whl"
        if not wheel.is_file():
            raise RuntimeError("Локальная сборка не создала wheel 0.5.3.")
        staging = target.with_name(target.name + ".installing_" + uuid.uuid4().hex)
        staging.mkdir(parents=True)
        needed = missing_packages(pins)
        if needed:
            house = wheelhouse(program)
            if house is None:
                raise RuntimeError("Нужны локальные wheels для закреплённых пакетов: " + json.dumps(needed)
                                   + ". Сеть отключена; рабочая установка не изменена.")
            pip(["install", "--no-index", "--no-compile", "--find-links", house, "--target", staging,
                 "-r", kit / "source/requirements-offline-py312.txt"], log, env)
        pip(["install", "--no-index", "--no-deps", "--no-compile", "--target", staging, wheel], log, env)
    check_core(staging, identity)
    write(staging / "RESEARCH_READY.json", {"core": identity["id"], "wheel_sha256": digest(wheel),
          "python_abi": sys.implementation.cache_tag, "platform": sys.platform,
          "dependency_source": "private pinned wheels" if needed else "existing pinned Python environment"})
    staging.rename(target)
    return target, identity


def fallback_archive(workspace):
    destination = workspace / "FOR_REVIEW.zip"
    temporary = destination.with_suffix(".preparation.tmp.zip")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("PREPARATION.json", "PREPARATION_ERROR.txt", "prepare.log"):
            path = workspace / name
            if path.is_file():
                archive.write(path, name)
    os.replace(temporary, destination)
    print("Архив диагностики: " + str(destination), flush=True)


def execute(args):
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               OPENBLAS_NUM_THREADS="1", OMP_WAIT_POLICY="PASSIVE")
    guard = args.kit / "guard"
    audit = args.workspace / "network_audit.jsonl"
    os.environ["AEROKINETIC_NETWORK_AUDIT"] = str(audit)
    env["AEROKINETIC_NETWORK_AUDIT"] = str(audit)
    if not getattr(sys, "_aerokinetic_network_denied", False):
        sys.path.insert(0, str(guard)); __import__("sitecustomize")
    os.environ["PYTHONPATH"] = str(guard)
    launched = False
    try:
        write(args.workspace / "PREPARATION.json", {"started_utc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
              "offline": True, "existing_installation_modified": False})
        print("AeroKinetic: подготовка отдельной исследовательской среды 0.5.3. Сеть не используется.", flush=True)
        target, identity = install(args.kit, args.workspace, args.program_root)
        env["PYTHONPATH"] = os.pathsep.join(map(str, (target, guard)))
        command = [sys.executable, "-u", str(args.kit / "run_suite.py"), "--kit", str(args.kit),
                   "--workspace", str(args.workspace), "--program-root", str(args.program_root),
                   "--target", str(target), "--threads", str(args.threads), "--grids", args.grids,
                   "--duration", str(args.duration)]
        if args.smoke:
            command.append("--smoke")
        launched = True
        # The suite/worker process handles Ctrl+C by requesting a safe stop.
        signal.signal(signal.SIGINT, lambda *_:None)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, lambda *_:None)
        return subprocess.call(command, env=env)
    except Exception:
        details = traceback.format_exc()
        (args.workspace / "PREPARATION_ERROR.txt").write_text(details, encoding="utf-8")
        print(details, flush=True)
        return 1
    finally:
        if not launched:
            fallback_archive(args.workspace)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--program-root", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--grids", default="32,64")
    parser.add_argument("--duration", type=float, default=12.)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    args.kit = args.kit.resolve(); args.workspace = args.workspace.resolve()
    args.program_root = args.program_root.resolve(); args.workspace.mkdir(parents=True, exist_ok=True)
    try:
        with study_lock(args.workspace / "LAUNCH.lock"):
            # Remove only a previous launch's request, before preparation starts.
            # New STOP requests during local installation must reach the solver.
            (args.workspace / "study/STOP_REQUESTED").unlink(missing_ok=True)
            return execute(args)
    except StudyAlreadyRunning as error:
        print(str(error), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
