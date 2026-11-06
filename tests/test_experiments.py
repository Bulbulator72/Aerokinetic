import json
import numpy as np
import pytest
from aerokinetic import Project
from aerokinetic.experiments import catalog, load_series, moment_at_reference, compare_run


def test_packaged_sources_have_verified_hashes_and_no_synthetic_coefficients():
    assert len(catalog()) == 2
    meta, values = load_series("ladson_80w_re5950000")
    assert meta["reynolds"] == 5950000 and meta["mach"] == 0.15
    point = next(row for row in values if row["alpha_deg"] == 10.12)
    assert point == {"alpha_deg": 10.12, "cl": 1.0707, "cd": 0.01201, "cm_c4": 0.0052}
    meta, values = load_series("ohtake_re30000")
    assert meta["coefficients"] == ["cl"] and len(values) == 115
    assert meta["digitization_bounds"]["cl"] == 0.008


def test_moment_transfer_matches_force_at_known_application_point():
    # Independently construct body-frame torque of one known force.
    point = np.array([0.1, -0.04])
    force = np.array([0.3, 1.2])
    old, new = np.array([0.4, 0.02]), np.array([0.25, 0.0])
    a = np.deg2rad(17)
    cd = force[0] * np.cos(a) + force[1] * np.sin(a)
    cl = -force[0] * np.sin(a) + force[1] * np.cos(a)
    def direct_moment(reference):
        arm = point - reference
        return -(arm[0] * force[1] - arm[1] * force[0])
    actual = moment_at_reference(direct_moment(old), cl, cd, 17, old, new, 1.0)
    assert actual == pytest.approx(direct_moment(new), abs=1e-14)


def write_fixture(directory, re=30000, alpha=5):
    # A synthetic calculation fixture exercises matching and interpolation only.
    # It is not a simulated aerodynamic result or a validation measurement.
    project = Project.from_dict({"flow": {"reynolds": re, "alpha_deg": alpha, "physical_chord_m": 0.15}})
    data = project.as_dict()
    metadata = {"flow": data["flow"], "contours": data["geometry"]["elements"],
                "reference_chord_input_units": project.section.chord,
                "moment_reference_input_units": project.section.moment_reference}
    summary = {"status": "completed", "averaging_samples": 10, "mean": {"cl": 0.1, "cd": 0.02, "cm": 0.005}}
    (directory / "metadata.json").write_text(json.dumps(metadata))
    (directory / "summary.json").write_text(json.dumps(summary))


def test_comparison_rejects_mismatched_re_and_extrapolation(tmp_path):
    write_fixture(tmp_path, re=10000)
    with pytest.raises(ValueError, match="Re mismatch"):
        compare_run(tmp_path, "ohtake_re30000")
    write_fixture(tmp_path, alpha=25)
    with pytest.raises(ValueError, match="extrapolation"):
        compare_run(tmp_path, "ohtake_re30000")
    write_fixture(tmp_path)
    result = compare_run(tmp_path, "ohtake_re30000")
    assert result["validation"] == "inconclusive"
    assert [r["coefficient"] for r in result["differences"]] == ["cl"]
