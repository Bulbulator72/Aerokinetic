"""Packaging, immutable physics, exclusive launch and small return archives."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/research_tests"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules(monkeypatch):
    bootstrap = load("ak_research_bootstrap_test", TOOLS / "bootstrap.py")
    monkeypatch.setitem(sys.modules, "bootstrap", bootstrap)
    suite = load("ak_research_suite_test", TOOLS / "run_suite.py")
    transfer = load("ak_research_transfer_test", TOOLS / "make_transfer.py")
    return bootstrap, suite, transfer


def test_self_contained_text_preserves_exact_core_and_both_line_endings(tmp_path, modules):
    bootstrap, _, transfer = modules
    output = tmp_path / "RUN_RESEARCH_TESTS.bat"
    unpacked = tmp_path / "kit"
    transfer.make(output, unpacked)
    assert output.read_bytes() == output.with_suffix(".txt").read_bytes()
    identity = json.loads((unpacked / "CORE_IDENTITY.json").read_text())
    bootstrap.check_core(unpacked / "source/src", identity)
    assert identity["version"] == "0.5.3"
    assert "exterior.py" in identity["package_files"]
    text = output.read_text(encoding="utf-8")
    for version in (text, text.replace("\n", "\r\n")):
        match = re.search(r"(?ms)^AK_RESEARCH_DATA_BEGIN\r?\n(?P<data>.*?)^AK_RESEARCH_DATA_END\r?$", version)
        payload = json.loads(match["data"])
        assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in payload["files"])
        assert all(not name.endswith((".whl", ".exe", ".npz", ".npy", ".dll", ".pyc")) for name in payload["files"])
    # A changed numerical source cannot be presented as the verified core.
    (unpacked / "source/src/aerokinetic/exterior.py").write_text("# changed\n")
    with pytest.raises(RuntimeError, match="exterior.py"):
        bootstrap.check_core(unpacked / "source/src", identity)


def test_study_changes_resolution_without_changing_flow_or_stop_physics(tmp_path, modules, monkeypatch):
    _, suite, _ = modules
    from aerokinetic import Project
    source = ROOT / "examples/naca0012_re30000_n32_exterior_down6.json"
    (tmp_path / "base_project.json").write_bytes(source.read_bytes())
    monkeypatch.setattr(suite, "resources", lambda: {"physical_memory_bytes": 16 * 2**30})
    args = argparse.Namespace(kit=tmp_path, threads=2, grids="32,64", duration=12., smoke=False)
    result = suite.make_projects(args); base = Project.load(source)
    assert list(result) == ["N32", "N64"]
    for name, project in result.items():
        assert project.flow == base.flow and project.section.chord == base.section.chord
        assert project.section.moment_reference == base.section.moment_reference
        assert all((a.xy == b.xy).all() for a, b in zip(project.section.elements, base.section.elements))
        assert project.grid.downstream_chords == 6 and project.grid.span_chords == .5
        assert project.run.termination.mode == "time" and project.run.termination.wall_time_limit_s == 0
        assert project.run.checkpoint_every * project.plan().dt_convective == 1
        assert project.run.sample_every * project.plan().dt_convective == .01
        assert project.run.duration_convective == 12 and project.run.average_after == 6
        assert project.plan().viscosity == pytest.approx(.04 * int(name[1:]) / 30000)
    args.grids = "16,32"
    with pytest.raises(ValueError, match="N>=32"):
        suite.make_projects(args)


def test_study_lock_prevents_simultaneous_process_and_releases_after_close(tmp_path, modules):
    _, suite, _ = modules
    lock = tmp_path / "RUNNING.lock"
    code = """import sys
sys.path.insert(0, sys.argv[1])
from run_suite import study_lock, StudyAlreadyRunning
try:
    with study_lock(__import__('pathlib').Path(sys.argv[2])):
        pass
except StudyAlreadyRunning:
    raise SystemExit(88)
"""
    with suite.study_lock(lock):
        result = subprocess.run([sys.executable, "-c", code, str(TOOLS), str(lock)])
        assert result.returncode == 88
    assert subprocess.run([sys.executable, "-c", code, str(TOOLS), str(lock)]).returncode == 0


def test_return_archive_excludes_checkpoints_cache_and_earlier_frames(tmp_path, modules):
    _, suite, _ = modules
    case = tmp_path / "study/cases/N32"
    (case / "frames").mkdir(parents=True)
    (case / "summary.json").write_text('{"status":"stopped"}')
    (case / "checkpoint.npz").write_bytes(b"large checkpoint remains local")
    (case / "frames/frame_0000000000.npz").write_bytes(b"early frame")
    (case / "frames/frame_0000000008.npz").write_bytes(b"last section")
    (tmp_path / "study/cache/jit").mkdir(parents=True)
    (tmp_path / "study/cache/jit/private.json").write_text("{}")
    suite.pack(argparse.Namespace(workspace=tmp_path))
    with zipfile.ZipFile(tmp_path / "FOR_REVIEW.zip") as archive:
        names = set(archive.namelist())
        assert "study/cases/N32/summary.json" in names
        assert "study/cases/N32/frames/frame_0000000008.npz" in names
        assert not any("checkpoint" in name or "/cache/" in name or "0000000000.npz" in name for name in names)
        manifest = json.loads(archive.read("FILES_SHA256.json"))["files"]
        for record in manifest:
            content = archive.read(record["path"])
            assert len(content) == record["bytes"] and hashlib.sha256(content).hexdigest() == record["sha256"]


def test_portable_compiler_is_found_in_sibling_distribution(tmp_path, modules):
    _, suite, _ = modules
    program = tmp_path / "source program"; program.mkdir()
    bundle = tmp_path / "portable copy/AeroKinetic/_internal"
    (bundle / "portable").mkdir(parents=True)
    (bundle / "portable/toolchain.json").write_text("{}")
    assert suite.find_portable(program) == bundle


def test_local_wheelhouse_skips_empty_directory_without_changing_installation(tmp_path, modules):
    bootstrap, _, _ = modules
    (tmp_path / "downloads/wheels_windows").mkdir(parents=True)
    full = tmp_path / "wheels_windows"; full.mkdir()
    (full / "fixture.whl").write_bytes(b"name-only fixture; pip is not called")
    assert bootstrap.wheelhouse(tmp_path) == full


def test_missing_openmp_flag_stops_before_launching_a_solver(tmp_path, modules, monkeypatch):
    _, suite, _ = modules
    config = tmp_path / "pystencils.json"
    config.write_text(json.dumps({"cache":{"object_cache":str(tmp_path / "jit"), "clear_cache_on_start":False}}))
    monkeypatch.setenv("PYSTENCILS_CONFIG", str(config))
    monkeypatch.setenv("PYSTENCILS_CACHE_DIR", str(tmp_path / "symbolic"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from pystencils.cpu import cpujit
    monkeypatch.setattr(cpujit, "get_compiler_config", lambda: {"flags":"/Ox /fp:precise"})
    with pytest.raises(RuntimeError, match="OpenMP"):
        suite.preflight(argparse.Namespace(workspace=tmp_path, threads=2))


def test_stop_requested_during_preparation_survives_and_second_launch_is_blocked(tmp_path, modules, monkeypatch):
    bootstrap, suite, _ = modules
    workspace = tmp_path / "work"; (workspace / "study").mkdir(parents=True)
    stop = workspace / "study/STOP_REQUESTED"; stop.touch()
    arguments = ["bootstrap.py", "--kit", str(tmp_path), "--workspace", str(workspace), "--program-root", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", arguments)
    def prepare(args):
        assert not stop.exists(), "Only the previous launch's request is cleared"
        stop.touch()
        assert suite.FileStop(stop).is_set(), "A request made during preparation reaches the worker"
        # A second launch cannot clear the pending request.
        assert bootstrap.main() == 3
        assert stop.exists()
        return 2
    monkeypatch.setattr(bootstrap, "execute", prepare)
    assert bootstrap.main() == 2
    assert stop.exists()
