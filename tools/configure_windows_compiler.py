"""Find an installed MSVC toolchain and verify pystencils JIT, without downloads.

Standalone so that it works with existing AeroKinetic 0.2.0 and 0.2.1 installs.
No pystencils import occurs before a candidate compiler is configured in a
fresh child process. This avoids retaining partially imported dependencies.
"""
import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


PROBE_CODE = r'''
import numpy as np
import pystencils as ps
from pystencils.cpu.cpujit import get_compiler_config
source, destination = ps.fields("source, destination: double[1D]")
kernel = ps.create_kernel(ps.Assignment(destination.center, 2 * source.center)).compile()
input_array = np.arange(32, dtype=np.float64)
output_array = np.full(32, np.nan)
kernel(source=input_array, destination=output_array)
np.testing.assert_array_equal(output_array, 2 * input_array)
print("PASS: pystencils imported; C++ kernel compiled, linked and executed.")
print("Compiler OS:", get_compiler_config()["os"])
'''


def toolchain_at(path):
    """Accept only installed toolchains, not a downloaded installer/layout."""
    path = Path(path)
    options = (path, path / "VC/Auxiliary/Build", path / "Auxiliary/Build")
    for build in options:
        if not (build / "vcvarsall.bat").is_file():
            continue
        if build.name.casefold() == "build" and build.parent.name.casefold() == "auxiliary":
            vc = build.parent.parent
            compilers = list((vc / "Tools/MSVC").glob("*/bin/Hostx64/x64/cl.exe"))
            if compilers:
                return build.resolve()
    return None


def find_toolchains(environment=None, run=subprocess.run):
    environment = os.environ if environment is None else environment
    candidates, notes = [], []
    roots = []
    for name in ("ProgramFiles(x86)", "ProgramW6432", "ProgramFiles"):
        if environment.get(name):
            root = Path(environment[name]) / "Microsoft Visual Studio"
            roots.append(root)
            vswhere = root / "Installer/vswhere.exe"
            if vswhere.is_file():
                try:
                    result = run([str(vswhere), "-all", "-products", "*", "-requires",
                                  "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                                  "-format", "json", "-utf8"], capture_output=True,
                                 check=True, timeout=20)
                    for item in json.loads(result.stdout.decode("utf-8-sig")):
                        if item.get("isComplete", True) and item.get("installationPath"):
                            candidates.append(Path(item["installationPath"]))
                except (OSError, subprocess.SubprocessError, ValueError, TypeError) as error:
                    notes.append("vswhere discovery failed: " + str(error))
    for name in ("VSINSTALLDIR", "VCINSTALLDIR", "VCToolsInstallDir"):
        if environment.get(name):
            path = Path(environment[name])
            candidates.extend((path, *path.parents))
    for root in roots:
        # Fixed-depth lookup under the standard installation roots only.
        candidates.extend(sorted(root.glob("*/BuildTools"), reverse=True))
        candidates.extend(sorted(root.glob("*/*"), reverse=True))
    found = []
    for candidate in candidates:
        build = toolchain_at(candidate)
        if build and str(build).casefold() not in {str(p).casefold() for p in found}:
            found.append(build)
    return found, notes


def make_config(existing, build, cache):
    config = copy.deepcopy(existing)
    if not isinstance(config, dict):
        raise ValueError("pystencils.json must contain a JSON object")
    compiler = config.get("compiler", {})
    if not isinstance(compiler, dict):
        raise ValueError("Invalid compiler section in pystencils.json")
    # Preserve explicit Windows numerical flags; otherwise use the unchanged
    # pystencils 1.4 Windows defaults. Do not use its removed setuptools API.
    flags = compiler.get("flags", "/Ox /fp:fast /OpenMP /arch:avx") if compiler.get("os", "windows") == "windows" else "/Ox /fp:fast /OpenMP /arch:avx"
    config["compiler"] = {"os": "windows", "msvc_version": str(build), "arch": "x64",
                          "flags": flags, "restrict_qualifier": "__restrict"}
    config["cache"] = {"object_cache": str(cache), "clear_cache_on_start": False}
    return config


def launcher_with_local_config(data):
    marker = b'if exist "%~dp0pystencils.json" set "PYSTENCILS_CONFIG=%~dp0pystencils.json"'
    cache_marker = b'if exist "%~dp0pystencils.json" set "PYSTENCILS_CACHE_DIR=%~dp0.cache\\pystencils_symbolic"'
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    anchor = b'cd /d "%~dp0"' + newline
    if anchor not in data:
        raise ValueError("Cannot locate the program-folder line in this launcher")
    if marker not in data:
        data = data.replace(anchor, anchor + marker + newline, 1)
    if cache_marker not in data:
        data = data.replace(marker + newline, marker + newline + cache_marker + newline, 1)
    return data


def run_probe(config, root, timeout=150):
    environment = dict(os.environ, PYSTENCILS_CONFIG=str(config), PYTHONIOENCODING="utf-8",
                       PYSTENCILS_CACHE_DIR=str(Path(root) / ".cache/pystencils_symbolic"))
    return subprocess.run([sys.executable, "-I", "-c", PROBE_CODE], cwd=root,
                          env=environment, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def configure(root, explicit=None, no_dialog=False):
    root = Path(root).resolve()
    if Path(sys.prefix).resolve() != root / ".venv" or sys.prefix == sys.base_prefix:
        raise ValueError("Run with the existing program's .venv Python")
    if sys.platform != "win32":
        raise ValueError("MSVC configuration is intended for Windows x64")
    if sys.maxsize <= 2**32:
        raise ValueError("64-bit Python is required")
    log_path = root / "COMPILER_CHECK.log"
    lines = ["AeroKinetic: offline compiler check", f"Python: {sys.version.split()[0]}",
             "No packages or compiler components are downloaded."]
    def note(message):
        lines.append(str(message))
        print(message, flush=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    note("[1/3] Looking for installed Visual Studio C++ Tools...")
    if explicit:
        build = toolchain_at(explicit)
        if not build:
            raise ValueError("The selected folder has no vcvarsall.bat and x64 cl.exe")
        candidates, notes = [build], []
    else:
        candidates, notes = find_toolchains()
    for message in notes:
        note(message)
    if not candidates and not no_dialog:
        import tkinter as tk
        from tkinter import filedialog
        note("No standard installation found. Select the INSTALLED Visual Studio folder if it is elsewhere; Cancel if absent.")
        window = tk.Tk()
        window.withdraw()
        chosen = filedialog.askdirectory(title="Папка УСТАНОВЛЕННОГО Visual Studio / Build Tools; отмена, если не установлен")
        window.destroy()
        build = toolchain_at(chosen) if chosen else None
        if build:
            candidates = [build]
    if not candidates:
        note("NOT FOUND: an installed x64 C++ toolchain could not be located.")
        note("A downloaded downloads/vs_buildtools folder is an installer layout, not an installed compiler.")
        note("Run 2_install_COMPILER_OFFLINE.bat AS ADMINISTRATOR on THIS PC, finish setup, reboot if requested, then rerun this repair.")
        note("If the offline layout is missing: prepare it online using 1b_download_COMPILER_ONLINE.bat, and transfer the whole downloads folder.")
        (root / "compiler_check.json").write_text(json.dumps({"status": "not_found", "configuration_changed": False}, indent=2) + "\n")
        return 2
    target = root / "pystencils.json"
    original = target.read_bytes() if target.is_file() else None
    existing = json.loads(original.decode("utf-8-sig")) if original else {}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    staged = root / ("pystencils_probe_" + stamp + ".json")
    accepted = None
    try:
        for index, build in enumerate(candidates):
            cache = root / ".cache" / ("pystencils_msvc_" + stamp + "_" + str(index))
            config = make_config(existing, build, cache)
            staged.write_text(json.dumps(config, indent=2, ensure_ascii=True) + "\n", encoding="ascii")
            note(f"[2/3] Testing C++ compilation with: {build}")
            try:
                result = run_probe(staged, root)
            except subprocess.TimeoutExpired:
                note("Compiler check timed out. Check the Visual Studio installation and antivirus messages.")
                continue
            note(result.stdout)
            if result.stderr:
                note(result.stderr)
            if result.returncode == 0:
                accepted = config
                break
        if accepted is None:
            note("FAILED: no candidate compiled and executed the test kernel. Existing configuration preserved.")
            return 1
        patches = {}
        for name in ("3_install_WINDOWS_OFFLINE.bat", "4_run_PREVIEW.bat", "6_run_AEROKINETIC.bat"):
            path = root / name
            if path.is_file():
                patches[name] = (path.read_bytes(), launcher_with_local_config(path.read_bytes()))
        backup = root / "compiler_fix_backups" / stamp
        backup.mkdir(parents=True)
        if original is not None:
            (backup / "pystencils.json").write_bytes(original)
        for name, (before, after) in patches.items():
            (backup / name).write_bytes(before)
        try:
            staged.write_text(json.dumps(accepted, indent=2, ensure_ascii=True) + "\n", encoding="ascii")
            os.replace(staged, target)
            for name, (before, after) in patches.items():
                (root / name).write_bytes(after)
        except Exception:
            if original is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(original)
            for name, (before, after) in patches.items():
                (root / name).write_bytes(before)
            raise
        report = {"status": "passed", "vcvars_directory": accepted["compiler"]["msvc_version"],
                  "jit_compile_and_execute": "passed", "config": str(target),
                  "backup": str(backup), "patched_launchers": list(patches),
                  "solver_or_dependencies_changed": False}
        (root / "compiler_check.json").write_text(json.dumps(report, indent=2) + "\n")
        note("[3/3] PASS. Compiler path saved locally; launchers configured; backup created.")
        note("Close the old program and command window. Rerun 3_install_WINDOWS_OFFLINE.bat, then 6_run_AEROKINETIC.bat.")
        return 0
    finally:
        staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vs-path", type=Path)
    parser.add_argument("--no-dialog", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    try:
        return configure(args.root, args.vs_path, args.no_dialog)
    except Exception as error:
        print("Compiler setup failed:", error, flush=True)
        log = args.root / "COMPILER_CHECK.log"
        with log.open("a", encoding="utf-8") as handle:
            handle.write("\nERROR: " + str(error) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
