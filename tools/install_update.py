"""Install the bundled application wheel into an existing local .venv, offline.

The pinned dependency set is preserved. A pre-install snapshot supports
rollback, including source files that this update replaces. No data directory
or user results are traversed or changed.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


TARGET_VERSION = "0.4.0"


def check_bundle(bundle):
    manifest = json.loads((bundle / "SHA256.json").read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid update file path")
        path = bundle / relative
        if not path.is_file():
            raise ValueError(f"Missing update file: {name}")
        data = path.read_bytes()
        if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError(f"Damaged update file: {name}")
    wheel = bundle / f"aerokinetic-{TARGET_VERSION}-py3-none-any.whl"
    if wheel.name not in manifest:
        raise ValueError("Application wheel is missing from the manifest")
    return wheel


def check_target(root):
    root = Path(root).resolve()
    prefix = Path(sys.prefix).resolve()
    if sys.prefix == sys.base_prefix or prefix != root / ".venv":
        raise ValueError("Use the program's own .venv Python and its containing folder")
    purelib = Path(sysconfig.get_path("purelib")).resolve()
    if not purelib.is_relative_to(prefix):
        raise ValueError("The installation target is outside the program environment")
    return root, purelib


def restore_backup(root, backup):
    root, purelib = check_target(root)
    backup = Path(backup).resolve()
    snapshot = json.loads((backup / "snapshot.json").read_text(encoding="utf-8"))
    if snapshot["root"] != str(root) or snapshot["purelib"] != str(purelib):
        raise ValueError("This backup belongs to another installation")
    # Check all required backup material before changing the target.
    for name in snapshot["runtime"]:
        if Path(name).name != name or not (backup / "runtime" / name).is_dir():
            raise ValueError("Incomplete runtime backup")
    for name, existed in snapshot["source"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid source backup path")
        if existed and not (backup / "source" / name).is_file():
            raise ValueError("Incomplete source backup")
    targets = [purelib / "aerokinetic", *purelib.glob("aerokinetic-*.dist-info")]
    for path in targets:
        if path.exists():
            shutil.rmtree(path)
    for name in snapshot["runtime"]:
        shutil.copytree(backup / "runtime" / name, purelib / name)
    for name, existed in snapshot["source"].items():
        target = root / name
        if existed:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / "source" / name, target)
        elif target.is_file():
            target.unlink()
    print(f"Restored application {snapshot['version']}; user results preserved.", flush=True)


def install(root, bundle):
    root, purelib = check_target(root)
    wheel = check_bundle(bundle)
    previous = version("aerokinetic")
    if previous not in ("0.2.0", "0.2.1", "0.3.0", TARGET_VERSION):
        raise ValueError(f"This update supports AeroKinetic 0.2.0/0.2.1/0.3.0/0.4.0, found {previous}")
    for name, expected in (("lbmpy", "1.4"), ("pystencils", "1.4"), ("sympy", "1.12.1")):
        if version(name) != expected:
            raise ValueError(f"Use the existing pinned dependency set: {name}=={expected}")
    source = bundle / "source"
    changes = sorted(p for p in source.rglob("*") if p.is_file())
    # Keep the existing offline re-installation path usable as well. The
    # universal app wheel is added only to already prepared wheelhouses;
    # third-party dependencies and their manifest entries are preserved.
    extra = {}
    for name in ("wheels_windows", "wheels_linux"):
        relative = Path("downloads") / name
        manifest_path = root / relative / "SHA256.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest.get("files"), list):
            raise ValueError(f"Invalid offline manifest: {manifest_path}")
        data = wheel.read_bytes()
        manifest["files"] = [entry for entry in manifest["files"] if entry.get("file") != wheel.name]
        manifest["files"].append({"file": wheel.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        extra[relative / wheel.name] = data
        extra[relative / "SHA256.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    backup = bundle / "backup"
    backup.mkdir(exist_ok=False)
    runtime = [purelib / "aerokinetic", *purelib.glob("aerokinetic-*.dist-info")]
    if len(runtime) != 2 or not all(p.is_dir() for p in runtime):
        raise ValueError("Expected a single installed AeroKinetic wheel")
    snapshot = {"root": str(root), "purelib": str(purelib), "version": previous,
                "runtime": [p.name for p in runtime], "source": {}}
    for path in runtime:
        shutil.copytree(path, backup / "runtime" / path.name)
    for relative in [p.relative_to(source) for p in changes] + list(extra):
        target = root / relative
        if target.exists() and not target.is_file():
            raise ValueError(f"Update target is not a regular file: {relative}")
        snapshot["source"][relative.as_posix()] = target.is_file()
        if target.is_file():
            saved = backup / "source" / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
    (backup / "snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    environment = dict(os.environ, PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    print(f"Updating {previous} -> {TARGET_VERSION}; no dependency downloads.", flush=True)
    print(f"Backup: {backup}", flush=True)
    try:
        with (bundle / "install.log").open("w", encoding="utf-8") as log:
            subprocess.run([sys.executable, "-I", "-m", "pip", "--disable-pip-version-check", "install",
                            "--no-index", "--no-deps", "--force-reinstall", str(wheel)],
                           check=True, env=environment, stdout=log, stderr=subprocess.STDOUT)
            subprocess.run([sys.executable, "-I", "-m", "pip", "--disable-pip-version-check", "check"],
                           check=True, env=environment, stdout=log, stderr=subprocess.STDOUT)
            code = ("import aerokinetic; from aerokinetic.gui import Application; "
                    "from aerokinetic.temporal import read_run; "
                    "from aerokinetic import RefinedPlan, RefinedSolver, RefinementSettings; "
                    "from aerokinetic import StopCriteria; from aerokinetic.gui_mesh import MeshWindow; "
                    "from aerokinetic.gui_convergence import ConvergenceWindow; "
                    f"assert aerokinetic.__version__ == '{TARGET_VERSION}'; "
                    "print('Application imports passed')")
            subprocess.run([sys.executable, "-I", "-c", code], check=True,
                           env=environment, stdout=log, stderr=subprocess.STDOUT)
        for path in changes:
            target = root / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        for relative, data in extra.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except Exception:
        restore_backup(root, backup)
        raise
    status = {"status": "passed", "previous_version": previous, "installed_version": TARGET_VERSION,
              "backup": str(backup), "dependency_downloads": False, "user_results_changed": False,
              "updated_wheelhouses": [str(p.parent) for p in extra if p.name == "SHA256.json"]}
    (bundle / "UPDATE_STATUS.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    print("Update complete. Start 6_run_AEROKINETIC.bat and open the saved result.", flush=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args()
    if args.restore:
        restore_backup(args.root, args.restore)
    else:
        install(args.root, Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
