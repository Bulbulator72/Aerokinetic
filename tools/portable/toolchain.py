"""Stage a private, relocatable x64 toolchain from an installed Windows SDK.

Standard library only. No installers, registry writes or downloads. Call the
installed vcvarsall once for discovery; never copy the user's entire environment.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig


DEFAULT_FLAGS = "/Ox /fp:fast /OpenMP /arch:avx"


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def discover_windows(tools_root, explicit=None):
    spec = importlib.util.spec_from_file_location("ak_compiler_discovery", Path(tools_root) / "configure_windows_compiler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidates = [module.toolchain_at(explicit)] if explicit else module.find_toolchains()[0]
    for build in candidates:
        if not build:
            continue
        batch = str(build / "vcvarsall.bat")
        if any(c in batch for c in ('"', '\r', '\n')):
            raise ValueError("Invalid compiler path")
        # Raw Windows command line: avoid list2cmdline re-escaping cmd's quotes.
        cmd = os.environ.get("COMSPEC", str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/cmd.exe"))
        command = f'"{cmd}" /d /u /s /c ""{batch}" x64 >nul && set"'
        result = subprocess.run(command, capture_output=True, timeout=90, check=False)
        if result.returncode:
            continue
        lines = result.stdout.decode("utf-16le", errors="replace").splitlines()
        allowed = {"VCTOOLSINSTALLDIR", "VCTOOLSREDISTDIR", "WINDOWSSDKDIR", "WINDOWSSDKVERSION", "UCRTVERSION"}
        env = {key.upper(): value for line in lines for key, sep, value in [line.partition("=")]
               if sep and key.upper() in allowed}
        if all(env.get(key) for key in ("VCTOOLSINSTALLDIR", "VCTOOLSREDISTDIR", "WINDOWSSDKDIR", "WINDOWSSDKVERSION")):
            env["AK_VCVARS_DIRECTORY"] = str(build)
            return env
    raise RuntimeError("Installed x64 MSVC + Windows SDK not found. Build on the home PC where AeroKinetic calculations work. A downloads/vs_buildtools layout is not an installed compiler.")


def _copy_tree(source, target):
    source, target = Path(source), Path(target)
    if not source.is_dir():
        raise FileNotFoundError("Required toolchain directory is missing: " + str(source))
    shutil.copytree(source, target)


def _version(value):
    value = str(value).strip("/\\ ")
    if not re.fullmatch(r"\d+(?:\.\d+){1,4}", value):
        raise ValueError("Unexpected SDK version: " + value)
    return value


def stage_windows(environment, target, python_base=None, flags=DEFAULT_FLAGS):
    target = Path(target)
    if target.exists():
        raise FileExistsError("Staging must use a new directory: " + str(target))
    target.mkdir(parents=True)
    env = {key.upper(): value for key, value in environment.items()}
    vc = Path(env["VCTOOLSINSTALLDIR"])
    sdk = Path(env["WINDOWSSDKDIR"])
    version = _version(env["WINDOWSSDKVERSION"])
    ucrt_version = _version(env.get("UCRTVERSION", version))
    redist = Path(env["VCTOOLSREDISTDIR"]) / "x64"
    tc = target / "toolchain"
    _copy_tree(vc / "bin/Hostx64/x64", tc / "msvc/bin")
    _copy_tree(vc / "include", tc / "msvc/include")
    _copy_tree(vc / "lib/x64", tc / "msvc/lib")
    for group, ver in (("shared", version), ("um", version), ("ucrt", ucrt_version)):
        _copy_tree(sdk / "Include" / ver / group, tc / "sdk/include" / group)
    for group, ver in (("um", version), ("ucrt", ucrt_version)):
        _copy_tree(sdk / "Lib" / ver / group / "x64", tc / "sdk/lib" / group)
    runtime = tc / "runtime"
    runtime.mkdir()
    for pattern in ("Microsoft.VC*.CRT", "Microsoft.VC*.OpenMP"):
        found = sorted(redist.glob(pattern))
        if not found:
            raise FileNotFoundError("Missing x64 redistributable directory: " + str(redist / pattern))
        for path in found[-1].glob("*.dll"):
            shutil.copy2(path, runtime / path.name)
            # Native compiler children search their own EXE directory first.
            # Keep their CRT next to cl/link, independently of the freezer's
            # DLL search directory inherited from the GUI process.
            shutil.copy2(path, tc / "msvc/bin" / path.name)
    for name in ("cl.exe", "link.exe"):
        if not (tc / "msvc/bin" / name).is_file():
            raise FileNotFoundError("Missing copied compiler: " + name)
    if not (runtime / "vcomp140.dll").is_file():
        raise FileNotFoundError("OpenMP runtime vcomp140.dll was not found")
    # Keep locally available license documents with a private copy of the tools.
    licenses = target / "licenses"
    licenses.mkdir()
    copied = []
    for index, base in enumerate((vc, *list(vc.parents)[:5], sdk, redist.parent)):
        for pattern in ("*icense*.txt", "*icense*.htm", "*icense*.html", "*ULA*.rtf"):
            for path in base.glob(pattern):
                if path.is_file():
                    dest = licenses / f"{index}_{path.name}"
                    shutil.copy2(path, dest)
                    copied.append(dest.name)
    stage_python_sdk(target / "python-sdk", python_base)
    metadata = {"format": "aerokinetic_private_toolchain", "schema": 1, "platform": "win32",
                "python": "3.12", "arch": "x64", "compiler_flags": flags,
                "msvc_version": vc.name, "windows_sdk_version": version, "ucrt_version": ucrt_version,
                "compiler_bin": "toolchain/msvc/bin", "runtime_bin": "toolchain/runtime",
                "include_dirs": ["toolchain/msvc/include", "toolchain/sdk/include/ucrt", "toolchain/sdk/include/shared", "toolchain/sdk/include/um"],
                "lib_dirs": ["toolchain/msvc/lib", "toolchain/sdk/lib/ucrt", "toolchain/sdk/lib/um"],
                "python_sdk": "python-sdk", "copied_license_files": copied,
                "usage": "private copy of user's installed tools for user's PCs; not a public MSVC redistribution"}
    (target / "toolchain.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def stage_python_sdk(target, python_base=None):
    target = Path(target)
    base = Path(python_base or sys.base_prefix)
    include = base / "Include" if sys.platform == "win32" or python_base else Path(sysconfig.get_paths()["include"])
    _copy_tree(include, target / "include")
    if not (target / "include/Python.h").is_file():
        raise FileNotFoundError("Full Python headers are required for JIT")
    if sys.platform == "win32" or python_base:
        library = base / "libs/python312.lib"
        if not library.is_file():
            raise FileNotFoundError("Full CPython 3.12 installation needs libs/python312.lib")
        (target / "libs").mkdir()
        shutil.copy2(library, target / "libs/python312.lib")
    for name in ("LICENSE.txt", "LICENSE"):
        if (base / name).is_file():
            shutil.copy2(base / name, target / name)


def write_manifest(root, filename="FILES_SHA256.json"):
    root = Path(root)
    entries = {p.relative_to(root).as_posix(): {"bytes": p.stat().st_size, "sha256": file_hash(p)}
               for p in sorted(root.rglob("*")) if p.is_file() and p.name != filename and "__pycache__" not in p.parts}
    (root / filename).write_text(json.dumps({"schema": 1, "files": entries}, indent=2) + "\n")
    return entries
