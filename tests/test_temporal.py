import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from aerokinetic.temporal import correlation_estimate, diagnose_series, read_run, write_analysis


def test_large_periodic_lift_has_stable_mean_without_constant_instantaneous_load():
    # Analytic sinusoid, 32 complete periods; no simulated or experimental data.
    t = np.arange(4096) / 128
    x = 0.5 + 0.5 * np.sin(2 * np.pi * t)
    result = diagnose_series(t, x)
    assert result["minimum"] == pytest.approx(0, abs=1e-14)
    assert result["maximum"] == pytest.approx(1, abs=1e-14)
    assert result["mean"] == pytest.approx(0.5, abs=1e-14)
    assert result["rms_fluctuation"] == pytest.approx(0.5 / np.sqrt(2), abs=1e-14)
    assert result["first_half_rms_fluctuation"] == pytest.approx(0.5 / np.sqrt(2), abs=1e-14)
    assert result["last_half_rms_fluctuation"] == pytest.approx(0.5 / np.sqrt(2), abs=1e-14)
    assert result["block_mean_range"] < 1e-14
    assert result["temporal_convergence"] == "not_certified"
    assert result["confidence_interval"] is None


def test_linear_drift_is_visible_in_half_and_block_means():
    t = np.arange(80) / 10
    result = diagnose_series(t, t)
    assert result["last_minus_first_half_mean"] == pytest.approx(4)
    assert result["block_mean_range"] == pytest.approx(7)
    assert result["correlation"]["effective_samples_heuristic"] < 8


def test_correlated_record_does_not_count_every_sample_as_independent():
    # Stationary AR(1), a=0.9 has theoretical tau=(1+a)/(1-a)=19.
    rng = np.random.default_rng(732)
    x = np.empty(66000)
    x[0] = rng.normal() / np.sqrt(1 - 0.9**2)
    noise = rng.normal(size=len(x))
    for i in range(1, len(x)):
        x[i] = 0.9 * x[i - 1] + noise[i]
    result = correlation_estimate(x, 0.01)
    assert 12 < result["tau_samples"] < 28
    assert result["effective_samples_heuristic"] < len(x) / 10


@pytest.mark.parametrize("times,values", [([0, 1, 1], [1, 2, 3]), ([0, 1, 3], [1, 2, 3]), ([0, 1, 2], [1, float('nan'), 3])])
def test_invalid_or_irregular_history_is_not_given_correlated_statistics(times, values):
    with pytest.raises(ValueError):
        diagnose_series(times, values)


def test_offline_report_contains_a_snapshot_and_keeps_original_bytes(tmp_path):
    data = tmp_path / "source"
    data.mkdir()
    (data / "project.json").write_text('{}')
    (data / "metadata.json").write_text(json.dumps({"flow": {"reynolds": 30000, "alpha_deg": 5}, "grid": {"cells_per_chord": 64}}))
    (data / "summary.json").write_text(json.dumps({"status": "completed", "t_star": 2, "average_after_t_star": 1}))
    (data / "history.csv").write_text("t_star,cl,cd,cm,local_mach_max,density_error_max\n" +
                                    "\n".join(f"{i/40},{0.5+0.5*np.sin(2*np.pi*i/40)},0.02,0.01,0.1,0.02" for i in range(1,81)))
    original = {p.name: p.read_bytes() for p in data.iterdir()}
    output, report = write_analysis(data, tmp_path / "report")
    assert report["series"]["cl"]["samples"] == 41
    assert report["temporal_convergence"] == "not_certified"
    assert {p.name: p.read_bytes() for p in data.iterdir()} == original
    with zipfile.ZipFile(output / "FOR_REVIEW.zip") as archive:
        assert archive.testzip() is None
        for name, content in original.items():
            assert archive.read("run/" + name) == content
    with pytest.raises(FileExistsError):
        write_analysis(data, output)
    (data / "summary.json").write_text(json.dumps({"status": "running"}))
    with pytest.raises(ValueError, match="Stop/save"):
        read_run(data)
