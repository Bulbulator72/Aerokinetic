"""Verify the text packet, sources, frozen comparisons and reported statuses.

This rechecks stored evidence. It does not run CFD or certify accuracy.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def verify(project):
    base = project / "docs/verification_exterior_library_20261108"
    manifest = read(base / "FILES_SHA256.json")["files"]
    names = {record["path"] for record in manifest}
    actual = {path.relative_to(base).as_posix() for path in base.rglob("*")
              if path.is_file() and path.name != "FILES_SHA256.json"}
    assert len(names) == len(manifest) and names == actual, "Incomplete or duplicate evidence manifest"
    for record in manifest:
        path = (base / record["path"]).resolve()
        path.relative_to(base.resolve())
        assert path.stat().st_size == record["bytes"] and sha(path) == record["sha256"], str(path)
    for record in read(base / "TEXT_NORMALIZATION.json"):
        assert sha(base / record["export_path"]) == record["export_sha256"]
        assert len(record["original_sha256"]) == 64
    build = read(base / "BUILD_IDENTITY.json")
    for name, digest in build["builds"]["final"]["module_sha256"].items():
        assert sha(project / "src/aerokinetic" / name) == digest, name
    context = read(base / "SOURCE_IDENTITY.json")
    for name, digest in context["verification_source_sha256"].items():
        assert sha(project / name) == digest, name
    assert context["initial_full_run_identity"]["sha256"] == build["builds"]["initial"]["module_sha256"]
    assert context["frozen_model_sha256"] == sha(project / "tools/exterior_checks/exterior_fd.py")
    assert build["final_numerics_identity"]["all_numerical_methods_identical"]
    assert build["final_numerics_identity"]["other_runtime_modules_identical"]
    assert build["final_numerics_identity"]["changed_solver_methods"] == ["run"]
    for name in ("initial", "final"):
        identity = build["builds"][name]
        assert len(identity["wheel_sha256"]) == 64
    assert "11 passed" in (base / "raw/test_exterior_final.log").read_text()
    assert "146 passed" in (base / "raw/test_regression_initial.log").read_text()
    equivalence = read(base / "raw/equivalence_initial/CHECKS.json")
    assert equivalence["passed"] and [s["start"] for s in equivalence["segments"]] == [0, 2400, 9600]
    assert all(s["pdf_max_error"] == s["macro_max_error"] == s["wall_q_max_error"] == 0
               and s["load_mass_and_clock_max_error"] <= 2e-10 for s in equivalence["segments"])
    assert read(base / "raw/shear_transport/CHECKS.json")["passed"] is False
    reconstruction = read(base / "raw/fd_parameters/CHECKS.json")
    assert reconstruction["script_sha256"] == sha(project / "tools/exterior_library_checks/fd_parameters.py")
    assert reconstruction["passed"] and len(reconstruction["cases"]) == 2
    for case in reconstruction["cases"]:
        assert case["equilibrium_20step_max_error"] <= 5e-13
        assert max(case[name] for name in ("polynomial_gradient_max_error", "full_pdf_mass_max_error",
                   "full_pdf_momentum_max_error", "pre_collision_FD_stress_max_error")) <= 2e-12
    periodic = read(base / "raw/periodic_transport_parity/CHECKS.json")
    assert periodic["script_sha256"] == sha(project / "tools/exterior_library_checks/periodic_transport.py")
    assert periodic["passed"] and len(periodic["cases"]) == 2
    assert all(c["late_relative_error"] <= .05 and c["spectral_relative_error"] <= .05
               and c["modal_map_relative_error"] <= 2e-5
               and c["transient_prediction_relative_error"] <= 2e-5 for c in periodic["cases"])
    offline = read(base / "raw/cold_offline_final_v2/CHECKS.json")
    assert offline["source_files_sha256"] == build["builds"]["final"]["module_sha256"]
    assert offline["passed"] and offline["cold_jit_modules"] > 0 and offline["gif_frames"] >= 2
    assert offline["network_attempts"] == offline["non_loopback_route_rows"] == 0
    assert offline["fresh_process_resume_history_identical"] and offline["fresh_process_resume_pdfs_identical"]
    assert offline["continuation_load_mass_clock_max_error"] == 0 and offline["parent_files_unchanged"]
    budget = read(base / "raw/finer_budget/CHECKS.json")
    assert budget["passed"] and budget["total_steps"] == 40 and budget["grid_convergence"] == "not_run"
    assert all(k["openmp_parallel_pragma"] and k["explicit_two_threads"] for k in budget["kernels"])
    matched = read(base / "raw/matched_airfoils/CHECKS.json")
    assert matched["passed"] and len(matched["cases"]) == 2
    for distance in (3, 6):
        root = base / f"raw/matched_airfoils/airfoil_down{distance}"
        checks = read(root / "CHECKS.json")
        assert checks["completion_gate_passed"] and checks["steps"] == 9600 and checks["t_star"] == 12
        with (root / "history.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [int(r["step"]) for r in rows] == list(range(8, 9601, 8))
        window = [r for r in rows if 6 <= float(r["t_star"]) <= 12]
        assert len(window) == 600
        for name in ("cl", "cd", "cm"):
            assert abs(sum(float(r[name]) for r in window) / len(window) - checks["window6_12"]["mean"][name]) <= 2e-14
        assert checks["prototype_comparison"]["mean_max_error"] <= 2e-9
        assert checks["prototype_comparison"]["rms_max_error"] <= 2e-9
    comparison = read(base / "comparison/CHECKS.json")
    assert all(comparison[name] for name in ("matched_physical_inputs", "complete_fixed_windows", "means_match_run_checkpoints"))
    assert comparison["source_sha256"] == sha(project / "tools/exterior_checks/analyze_airfoil.py")
    assert comparison["experimental_validation"] == "inconclusive"
    analysis = read(base / "comparison/ANALYSIS.json")
    tunnel = project / "docs/verification_naca0012_20261106/comparison/ohtake_re30000_digitized_all_coefficients.csv"
    assert analysis["experiment_csv_sha256"] == sha(tunnel)
    mass = read(base / "mass/ANALYSIS.json")
    assert mass["input_checks_passed"] and mass["wall_impermeability"] == "not_established"
    for case in mass["cases"]:
        history = base / "raw/matched_airfoils" / case["run"] / "history.csv"
        assert sha(history) == case["history_sha256"]
        with history.open(newline="") as stream:
            rows = {int(row["step"]): row for row in csv.DictReader(stream)}
        first, last = case["state_endpoint_steps"]
        for name, field in (("wall", "wall_mass_exchange_accumulated"), ("exterior", "exterior_source_mass_accumulated")):
            total = float(rows[last][field]) - float(rows[first][field])
            assert total == case[name]["net_mass"]
    status = read(base / "CHECKS.json")
    assert status["implementation_integration"] == "passed"
    assert status["grid_convergence"] == status["windows_gpu"] == "not_run"
    assert status["experimental_validation"] == "inconclusive"
    return {"packet_integrity": "passed", "hashed_files": len(manifest),
            "complete_airfoil_cases": 2, "implementation_integration": "passed",
            "experimental_validation": "inconclusive", "grid_convergence": "not_run",
            "scope": "Stored evidence and source identity; no new CFD or accuracy certification"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    print(json.dumps(verify(args.project_root), indent=2))


if __name__ == "__main__":
    main()
