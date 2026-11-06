"""Portable staging/adapters, with synthetic files, not native Windows proof."""
import importlib.util
import json
from pathlib import Path
import sys
import sysconfig

import pytest

PORTABLE = Path(__file__).resolve().parents[1] / 'tools/portable'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tc = load('portable_toolchain_under_test', PORTABLE / 'toolchain.py')
rt = load('portable_runtime_under_test', PORTABLE / 'ak_portable_runtime.py')


def fixture_files(root):
    for name in ('VC/14.44/bin/Hostx64/x64/cl.exe', 'VC/14.44/bin/Hostx64/x64/link.exe',
                 'VC/14.44/include/math.h', 'VC/14.44/lib/x64/libcmt.lib',
                 'SDK/Include/10.0.1/ucrt/stdio.h', 'SDK/Include/10.0.1/shared/shared.h',
                 'SDK/Include/10.0.1/um/windows.h', 'SDK/Lib/10.0.1/ucrt/x64/ucrt.lib',
                 'SDK/Lib/10.0.1/um/x64/kernel32.lib',
                 'Redist/x64/Microsoft.VC143.CRT/vcruntime140.dll',
                 'Redist/x64/Microsoft.VC143.OpenMP/vcomp140.dll',
                 'Python/Include/Python.h', 'Python/libs/python312.lib', 'Python/LICENSE.txt'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('SYNTHETIC FIXTURE ONLY: ' + name).encode())
    return {'VCTOOLSINSTALLDIR': str(root / 'VC/14.44'), 'WINDOWSSDKDIR': str(root / 'SDK'),
            'WINDOWSSDKVERSION': '10.0.1\\', 'VCTOOLSREDISTDIR': str(root / 'Redist')}


def test_staging_relocation_manifest_and_isolated_environment(tmp_path):
    original = tmp_path / 'Домашний ПК with spaces'
    env = fixture_files(original)
    before = {p.relative_to(original): p.read_bytes() for p in original.rglob('*') if p.is_file()}
    staged = tmp_path / 'Рабочий ПК/portable'
    metadata = tc.stage_windows(env, staged, original / 'Python', flags='/O2 /fp:precise /openmp')
    assert metadata['compiler_flags'] == '/O2 /fp:precise /openmp'
    assert (staged / 'toolchain/msvc/bin/vcomp140.dll').read_bytes() == (staged / 'toolchain/runtime/vcomp140.dll').read_bytes()
    assert str(original) not in (staged / 'toolchain.json').read_text()
    environment = rt.compiler_environment(staged, metadata, system_root=tmp_path / 'Windows')
    assert str(original) not in str(environment)
    assert environment['CL'] == environment['_CL_'] == environment['LINK'] == ''
    assert str(staged / 'toolchain/msvc/bin') in environment['PATH']
    entries = tc.write_manifest(staged)
    for name, entry in entries.items():
        assert tc.file_hash(staged / name) == entry['sha256']
    assert {p.relative_to(original): p.read_bytes() for p in original.rglob('*') if p.is_file()} == before
    with pytest.raises(FileExistsError):
        tc.stage_windows(env, staged, original / 'Python')


def test_incomplete_toolchain_cannot_get_ready_metadata(tmp_path):
    original = tmp_path / 'installed'
    env = fixture_files(original)
    (original / 'Redist/x64/Microsoft.VC143.OpenMP/vcomp140.dll').unlink()
    with pytest.raises(FileNotFoundError, match='vcomp140'):
        tc.stage_windows(env, tmp_path / 'output', original / 'Python')
    assert not (tmp_path / 'output/toolchain.json').exists()


def test_sdk_version_and_bundle_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError):
        tc._version('10.0&echo bad')
    with pytest.raises(ValueError, match='escapes'):
        rt.safe_member(tmp_path, '../elsewhere')


def test_msvc_preload_never_calls_system_discovery(tmp_path):
    source = tmp_path / 'msvc_detection.py'
    source.write_text('def get_environment(*args):\n    raise AssertionError("System VS accessed")\n')
    name = 'pystencils.cpu.msvc_detection'
    previous = sys.modules.get(name)
    try:
        module = rt.preload_msvc_adapter(source, tmp_path / 'toolchain', {'PATH': 'portable only'})
        assert module.get_environment(str(tmp_path / 'toolchain'), 'x64') == {'PATH': 'portable only'}
        with pytest.raises(ValueError, match='fallback is disabled'):
            module.get_environment('latest')
        with pytest.raises(ValueError):
            module.get_environment(str(tmp_path / 'toolchain'), 'x86')
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def test_python_sdk_view_is_narrow_and_does_not_change_global_config(tmp_path):
    before = dict(sysconfig.get_config_vars())
    original_include = sysconfig.get_paths()['include']
    view = rt.PythonSDK(tmp_path)
    assert view.get_paths()['include'] == str(tmp_path / 'include')
    assert view.get_config_vars('installed_base', 'py_version_nodot') == [str(tmp_path), '312']
    assert before == sysconfig.get_config_vars()
    assert sysconfig.get_paths()['include'] == original_include


def test_bundle_manifest_rejects_changed_and_missing_files(tmp_path):
    (tmp_path / 'runtime.dll').write_bytes(b'synthetic runtime')
    tc.write_manifest(tmp_path, 'BUNDLE_SHA256.json')
    assert rt.verify_bundle(tmp_path) == 1
    (tmp_path / 'runtime.dll').write_bytes(b'changed runtime!!')
    with pytest.raises(ValueError):
        rt.verify_bundle(tmp_path)
    (tmp_path / 'runtime.dll').unlink()
    with pytest.raises(ValueError, match='Missing'):
        rt.verify_bundle(tmp_path)


def test_validation_environment_removes_development_paths(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PORTABLE))
    builder = load('portable_builder_environment_test', PORTABLE / 'build.py')
    original = {'SystemRoot': r'C:\Windows', 'PATH': r'C:\home-python;C:\VS',
                'INCLUDE': r'C:\VS\Include', 'LIB': r'C:\VS\lib', 'PYTHONPATH': 'source',
                'VS170COMNTOOLS': 'home', 'UCRTVersion': 'home', 'PYSTENCILS_CONFIG': 'home'}
    env = builder.stripped_environment(original, target_platform='win32')
    assert original['PYTHONPATH'] == 'source'
    assert not any(name in env for name in ('PYTHONPATH', 'INCLUDE', 'LIB', 'VS170COMNTOOLS', 'UCRTVersion', 'PYSTENCILS_CONFIG'))
    assert 'home-python' not in env['PATH'] and r'C:\VS' not in env['PATH']


def test_windows_freezer_spec_uses_matching_crt_and_preserves_other_binaries(tmp_path, monkeypatch):
    from types import SimpleNamespace
    runtime = tmp_path / 'stage/portable/toolchain/runtime'
    runtime.mkdir(parents=True)
    (runtime / 'vcruntime140.dll').write_bytes(b'staged compiler runtime')
    calls = {}
    def analysis(*args, **kwargs):
        calls['analysis'] = kwargs
        return SimpleNamespace(pure=[], scripts=[], datas=[], binaries=[
            ('VCRUNTIME140.dll', '/old/runtime.dll', 'BINARY'), ('other.pyd', '/other.pyd', 'EXTENSION')])
    def executable(*args, **kwargs):
        calls['exe'] = kwargs
        return object()
    def collect(*args, **kwargs):
        calls['binaries'] = args[1]
    monkeypatch.setenv('AK_PORTABLE_KIT', str(tmp_path / 'kit'))
    monkeypatch.setenv('AK_PORTABLE_STAGE', str(tmp_path / 'stage'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    namespace = {'Analysis': analysis, 'PYZ': lambda *a: object(), 'EXE': executable, 'COLLECT': collect}
    exec(compile((PORTABLE / 'AeroKinetic.spec').read_bytes(), 'AeroKinetic.spec', 'exec'), namespace)
    binaries = calls['binaries']
    assert sum(name.casefold() == 'vcruntime140.dll' for name, _, _ in binaries) == 1
    assert next(source for name, source, _ in binaries if name.casefold() == 'vcruntime140.dll') == str(runtime / 'vcruntime140.dll')
    assert any(name == 'other.pyd' for name, _, _ in binaries)
    assert calls['exe']['console'] is False
    assert 'PIL._tkinter_finder' in calls['analysis']['hiddenimports']
