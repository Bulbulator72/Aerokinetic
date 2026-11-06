"""Discovery/configuration tests; these do not pretend to execute Windows MSVC."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location("windows_compiler_setup", Path(__file__).resolve().parents[1] / "tools/configure_windows_compiler.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def fake_toolchain(path):
    build = path / "VC/Auxiliary/Build"
    build.mkdir(parents=True)
    (build / "vcvarsall.bat").write_text("test fixture only")
    executable = path / "VC/Tools/MSVC/14.44/bin/Hostx64/x64/cl.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test fixture only")
    return build


def test_registered_custom_installation_with_spaces_is_found(tmp_path):
    program_files = tmp_path / "Program Files (x86)"
    vswhere = program_files / "Microsoft Visual Studio/Installer/vswhere.exe"
    vswhere.parent.mkdir(parents=True)
    vswhere.write_bytes(b"fixture")
    installed = tmp_path / "Другая папка/Build Tools"
    build = fake_toolchain(installed)
    def discovery(command, **kwargs):
        assert command[0] == str(vswhere)
        assert "-requires" in command and "-utf8" in command and "*" in command
        return subprocess.CompletedProcess(command, 0, json.dumps([{"isComplete": True, "installationPath": str(installed)}]).encode("utf-8"))
    found, _ = setup.find_toolchains({"ProgramFiles(x86)": str(program_files)}, discovery)
    assert found == [build.resolve()]


def test_layout_or_missing_compiler_is_not_an_installation(tmp_path):
    (tmp_path / "vs_BuildTools.exe").write_bytes(b"fixture")
    assert setup.toolchain_at(tmp_path) is None
    build = tmp_path / "VC/Auxiliary/Build"
    build.mkdir(parents=True)
    (build / "vcvarsall.bat").write_text("fixture")
    assert setup.toolchain_at(tmp_path) is None


def test_standard_install_and_developer_environment_are_supported(tmp_path):
    files = tmp_path / "Program Files"
    install = files / "Microsoft Visual Studio/2022/BuildTools"
    build = fake_toolchain(install)
    found, _ = setup.find_toolchains({"ProgramFiles": str(files)})
    assert found == [build.resolve()]
    found, _ = setup.find_toolchains({"VCToolsInstallDir": str(install / "VC/Tools/MSVC/14.44")})
    assert found == [build.resolve()]


def test_config_preserves_windows_flags_and_changes_only_local_setup(tmp_path):
    original = {"compiler": {"os": "windows", "flags": "/O2 /fp:precise"}, "other": {"preserve": 3}}
    build = Path(r"D:\VS Build Tools\VC\Auxiliary\Build")
    result = setup.make_config(original, build, tmp_path / "fresh_cache")
    assert result["compiler"]["msvc_version"] == str(build)
    assert result["compiler"]["flags"] == "/O2 /fp:precise"
    assert result["compiler"]["arch"] == "x64"
    assert result["other"] == original["other"]
    assert "msvc_version" not in original["compiler"]
    assert result["cache"]["clear_cache_on_start"] is False


@pytest.mark.parametrize("newline", [b"\r\n", b"\n"])
def test_launcher_patch_is_idempotent_and_preserves_version_and_arguments(newline):
    before = newline.join([b"@echo off", b'setlocal', b'cd /d "%~dp0"', b'python -m pip install aerokinetic==0.2.0', b'python -m aerokinetic.gui %*', b''])
    after = setup.launcher_with_local_config(before)
    assert setup.launcher_with_local_config(after) == after
    assert b'aerokinetic==0.2.0' in after and b'%*' in after
    assert after.count(b'PYSTENCILS_CONFIG=') == 1


def test_failed_probe_preserves_configuration_and_launcher(tmp_path, monkeypatch):
    build = fake_toolchain(tmp_path / "VS")
    original = b'{"compiler":{"os":"windows","msvc_version":"latest"}}'
    (tmp_path / "pystencils.json").write_bytes(original)
    launcher = b'@echo off\r\ncd /d "%~dp0"\r\npython -m aerokinetic.gui\r\n'
    (tmp_path / "6_run_AEROKINETIC.bat").write_bytes(launcher)
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(setup, "run_probe", lambda *a: subprocess.CompletedProcess([], 1, "", "Deliberate probe failure"))
    assert setup.configure(tmp_path, explicit=tmp_path / "VS", no_dialog=True) == 1
    assert (tmp_path / "pystencils.json").read_bytes() == original
    assert (tmp_path / "6_run_AEROKINETIC.bat").read_bytes() == launcher
    assert not list(tmp_path.glob("pystencils_probe_*.json"))


def test_success_commits_config_with_backup_after_probe(tmp_path, monkeypatch):
    build = fake_toolchain(tmp_path / "VS")
    launcher = b'@echo off\r\ncd /d "%~dp0"\r\npython -m aerokinetic.gui %*\r\n'
    (tmp_path / "6_run_AEROKINETIC.bat").write_bytes(launcher)
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(setup, "run_probe", lambda *a: subprocess.CompletedProcess([], 0, "Test stub; not a Windows compilation", ""))
    assert setup.configure(tmp_path, explicit=tmp_path / "VS", no_dialog=True) == 0
    config = json.loads((tmp_path / "pystencils.json").read_text())
    assert config["compiler"]["msvc_version"] == str(build)
    backup = next((tmp_path / "compiler_fix_backups").iterdir())
    assert (backup / "6_run_AEROKINETIC.bat").read_bytes() == launcher
