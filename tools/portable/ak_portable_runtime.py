"""Portable launch adapter; no changes to scientific source or numerical flags."""
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys
import sysconfig
import uuid

_dll_handles = []


def verify_bundle(root):
    import hashlib
    root = Path(root).resolve()
    entries = json.loads((root / 'BUNDLE_SHA256.json').read_text())['files']
    for relative, item in entries.items():
        path = safe_member(root, relative)
        if not path.is_file() or path.stat().st_size != item['bytes']:
            raise ValueError('Missing or changed bundle file: ' + relative)
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != item['sha256']:
            raise ValueError('Bundle SHA-256 differs: ' + relative)
    return len(entries)


def safe_member(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Portable path escapes bundle: " + relative)
    return path


def compiler_environment(base, metadata, system_root=None):
    base = Path(base)
    system = Path(system_root or os.environ.get("SystemRoot", r"C:\Windows"))
    bins = [safe_member(base, metadata[name]) for name in ("compiler_bin", "runtime_bin")]
    include = [safe_member(base, p) for p in metadata["include_dirs"]]
    libraries = [safe_member(base, p) for p in metadata["lib_dirs"]]
    for path in bins + include + libraries:
        if not path.is_dir():
            raise FileNotFoundError("Incomplete portable toolchain: " + str(path))
    return {"PATH": ";".join(map(str, [*bins, system / "System32", system])),
            "INCLUDE": ";".join(map(str, include)), "LIB": ";".join(map(str, libraries)),
            "LIBPATH": ";".join(map(str, libraries)),
            "CL": "", "_CL_": "", "LINK": "", "_LINK_": ""}


class PythonSDK:
    """Narrow sysconfig view used by pystencils.cpu.cpujit only."""
    def __init__(self, path):
        self.path = Path(path).resolve()

    def get_paths(self, *args, **kwargs):
        result = dict(sysconfig.get_paths(*args, **kwargs))
        result.update(include=str(self.path / "include"), platinclude=str(self.path / "include"))
        return result

    def get_config_vars(self, *args):
        result = dict(sysconfig.get_config_vars())
        result.update(installed_base=str(self.path), py_version_nodot="312")
        return [result.get(name) for name in args] if args else result


def preload_msvc_adapter(module_path, expected_root, environment):
    """Load the pinned upstream detection module, overriding one launch hook.

    Preloading before importing pystencils avoids its eager registry/vcvars
    discovery. Reject any other compiler root; never fall back to system VS.
    """
    name = "pystencils.cpu.msvc_detection"
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected_root = Path(expected_root).resolve()
    def bundled_environment(version, arch="x64"):
        if arch != "x64" or Path(str(version)).resolve() != expected_root:
            raise ValueError("Portable compiler root/architecture differs; system MSVC fallback is disabled")
        return dict(environment)
    module.get_environment = bundled_environment
    sys.modules[name] = module
    return module


def configure(bundle, workspace):
    bundle, workspace = Path(bundle).resolve(), Path(workspace).resolve()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Portable runtime requires CPython 3.12")
    base = bundle / "portable"
    metadata = json.loads((base / "toolchain.json").read_text())
    if metadata["platform"] != sys.platform:
        raise RuntimeError("Bundle target OS differs from running OS")
    workspace.mkdir(parents=True, exist_ok=True)
    sdk = safe_member(base, metadata["python_sdk"])
    if not (sdk / "include/Python.h").is_file():
        raise FileNotFoundError("Bundled Python.h is missing")
    cache = workspace / "jit"
    cache.mkdir(exist_ok=True)
    os.environ["PYSTENCILS_CACHE_DIR"] = str(workspace / "symbolic")
    os.environ["MPLCONFIGDIR"] = str(workspace / "matplotlib")
    os.environ.setdefault('XDG_CACHE_HOME', str(workspace / 'cache'))
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    if sys.platform == "win32":
        if importlib.metadata.version("pystencils") != "1.4":
            raise RuntimeError("Portable MSVC adapter is pinned to pystencils 1.4")
        env = compiler_environment(base, metadata)
        tc = base / "toolchain"
        if not (sdk / "libs/python312.lib").is_file():
            raise FileNotFoundError("Bundled python312.lib is missing")
        preload_msvc_adapter(bundle / "pystencils/cpu/msvc_detection.py", tc, env)
        for name in ("runtime_bin", "compiler_bin"):
            _dll_handles.append(os.add_dll_directory(str(safe_member(base, metadata[name]))))
        compiler = {"os": "windows", "msvc_version": str(tc), "arch": "x64",
                    "flags": metadata["compiler_flags"], "restrict_qualifier": "__restrict"}
    else:
        # Development proof on Linux only, never mislabeled a Windows build.
        compiler = {"os": "linux", "command": metadata["compiler_command"],
                    "flags": metadata["compiler_flags"], "restrict_qualifier": "__restrict__"}
    config = workspace / "pystencils.json"
    temporary = workspace / ('pystencils_' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps({"compiler": compiler, "cache": {"object_cache": str(cache), "clear_cache_on_start": False}}, indent=2) + "\n")
    os.replace(temporary, config)
    os.environ["PYSTENCILS_CONFIG"] = str(config)
    # Parent package initialization reads the config. Replace only this module's
    # sysconfig reference after import; global Python paths remain untouched.
    from pystencils.cpu import cpujit
    cpujit.sysconfig = PythonSDK(sdk)
    return {"bundle": str(bundle), "workspace": str(workspace), "metadata": metadata,
            "jit_include": cpujit.sysconfig.get_paths()["include"],
            "compiler": {key: value for key, value in cpujit.get_compiler_config().items() if key != "env"},
            "compiler_environment": cpujit.get_compiler_config().get("env", {}),
            "frozen": bool(getattr(sys, "frozen", False))}
