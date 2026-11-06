import csv
from dataclasses import asdict
import json
import numpy as np
import pytest

from aerokinetic import Contour, Flow, Grid, Project, RunSettings, Section, Solver, naca4


def test_project_is_portable_and_preserves_moment_units(tmp_path):
    contour = naca4("2412", 31)
    path = tmp_path / "original.dat"
    np.savetxt(path, contour.xy, header="custom profile", comments="")
    config = {"geometry": {"elements": [{"file": "original.dat"}],
                           "chord": 1.0, "moment_reference": [0.4, -0.02]},
              "flow": {"physical_chord_m": 0.15}, "grid": {"cells_per_chord": 32}}
    p = Project.from_dict(config, tmp_path)
    p.save(tmp_path / "project.json")
    path.unlink()
    loaded = Project.load(tmp_path / "project.json")
    assert p.case_fingerprint == loaded.case_fingerprint
    np.testing.assert_array_equal(p.section.elements[0].xy, loaded.section.elements[0].xy)
    assert loaded.section.moment_reference == (0.4, -0.02)
    assert loaded.flow.physical_chord_m == 0.15


def test_naca_generator_symmetry_camber_and_unsupported_regimes():
    symmetric = naca4("0012", 81)
    upper, lower = symmetric.xy[:81][::-1], symmetric.xy[80:]
    np.testing.assert_allclose(upper[:, 0], lower[:, 0], atol=1e-14)
    np.testing.assert_allclose(upper[:, 1], -lower[:, 1], atol=1e-14)
    cambered = naca4("2412", 81)
    upper, lower = cambered.xy[:81][::-1], cambered.xy[80:]
    mean_line = (upper + lower) / 2
    assert np.interp(0.4, mean_line[:, 0], mean_line[:, 1]) == pytest.approx(0.02, abs=1e-5)
    with pytest.raises(ValueError, match="Re"):
        Project.from_dict({"flow": {"reynolds": 5950000}})
    with pytest.raises(ValueError, match="positive"):
        RunSettings(duration_convective=0)


def make_solver():
    return Solver(Section((naca4("0012", 31),)), Flow(alpha_deg=3, perturbation=1e-3),
                  Grid(cells_per_chord=16, upstream_chords=1, downstream_chords=2,
                       transverse_chords=1, memory_limit_gib=1))


@pytest.mark.integration
@pytest.mark.parametrize("split", [5, 6])
def test_restart_is_identical_to_uninterrupted_pdfs_outflow_and_loads(tmp_path, split):
    original = make_solver()
    for _ in range(split):
        original.step(sample=True)
    checkpoint = original.save_checkpoint(tmp_path / "state.npz")
    for _ in range(9):
        load = original.step(sample=True)
    restored = Solver.from_checkpoint(checkpoint)
    for _ in range(9):
        restored_load = restored.step(sample=True)
    assert restored.steps == original.steps == split + 9
    a, b = original._stepper, restored._stepper
    np.testing.assert_array_equal(a.data_handling.cpu_arrays[a.pdf_array_name],
                                  b.data_handling.cpu_arrays[b.pdf_array_name])
    for name in ("cl", "cd", "cm", "t_star"):
        assert restored_load[name] == load[name]
    original.save_checkpoint(tmp_path / "a.npz")
    restored.save_checkpoint(tmp_path / "b.npz")
    with np.load(tmp_path / "a.npz", allow_pickle=False) as a, np.load(tmp_path / "b.npz", allow_pickle=False) as b:
        meta = json.loads(str(a["metadata"]))
        for i, _ in enumerate(meta["boundaries"]):
            np.testing.assert_array_equal(a[f"boundary_{i}"], b[f"boundary_{i}"])


@pytest.mark.integration
def test_stop_resume_keeps_time_history_averages_and_checkpoint(tmp_path):
    solver = make_solver()
    settings = dict(duration_convective=17 * solver.plan.dt_convective,
                    sample_every=3, frame_every=4, check_every=4, checkpoint_every=4)
    stopped = solver.run(output=tmp_path / "run", callback=lambda s, r: s.steps < 6, **settings)
    assert stopped["status"] == "stopped" and stopped["steps"] == 6
    history = tmp_path / "run/history.csv"
    original = history.read_bytes()
    history.write_bytes(original.replace(b"cl", b"xx", 1))
    with pytest.raises(ValueError, match="History before the checkpoint has changed"):
        Solver.resume_run(tmp_path / "run")
    assert history.read_bytes() != original
    history.write_bytes(original)
    project_path = tmp_path / "run/project.json"
    original_project = project_path.read_bytes()
    changed = json.loads(original_project)
    changed["flow"]["alpha_deg"] += 1
    project_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Saved project or run settings changed"):
        Solver.resume_run(tmp_path / "run")
    assert history.read_bytes() == original
    project_path.write_bytes(original_project)
    result = Solver.resume_run(tmp_path / "run")
    assert result["status"] == "completed" and result["steps"] == 17
    with (tmp_path / "run/history.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [int(row["step"]) for row in rows] == [3, 6, 9, 12, 15]
    assert len({row["t_star"] for row in rows}) == len(rows)
    expected = np.mean([[float(row[k]) for k in ("cl", "cd", "cm")] for row in rows], axis=0)
    np.testing.assert_allclose(list(result["mean"].values()), expected, rtol=1e-14)


@pytest.mark.integration
def test_crash_recovery_archives_tail_and_replays_from_last_checkpoint(tmp_path):
    solver = make_solver()
    def crash(s, record):
        if s.steps == 6:
            raise RuntimeError("simulated interruption after checkpoint")
    with pytest.raises(RuntimeError, match="simulated"):
        solver.run(17 * solver.plan.dt_convective, tmp_path / "crash", sample_every=3,
                   frame_every=2, check_every=4, checkpoint_every=4, callback=crash)
    result = Solver.resume_run(tmp_path / "crash")
    assert result["status"] == "completed" and result["steps"] == 17
    with (tmp_path / "crash/history.csv").open() as f:
        assert [int(row["step"]) for row in csv.DictReader(f)] == [3, 6, 9, 12, 15]
    backups = list((tmp_path / "crash/recovery").glob("*/history_before_resume.csv"))
    assert len(backups) == 1
    assert (backups[0].parent / "frame_0000000006.npz").is_file()
    with backups[0].open() as f:
        assert [int(row["step"]) for row in csv.DictReader(f)] == [3, 6]


def test_atomic_write_failure_keeps_previous_checkpoint(tmp_path):
    from aerokinetic._io import atomic_binary
    path = tmp_path / "checkpoint.npz"
    path.write_bytes(b"previous complete state")
    with pytest.raises(RuntimeError, match="interruption"):
        with atomic_binary(path) as handle:
            handle.write(b"unfinished new state")
            raise RuntimeError("interruption during write")
    assert path.read_bytes() == b"previous complete state"
    assert list(tmp_path.iterdir()) == [path]
