"""Check installed experimental refinement stages, without Python network access.

Populate a CLEAN venv from the existing pinned local wheelhouse first. The
script does not install packages or download anything. Fresh JIT caches are
created under the supplied new directory. This is Linux/CPU evidence when run
there; neither a Windows installer check nor a production airfoil benchmark.
Pass --interface for the spatial-interface suite; default is the first remap stage.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interface", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new/empty offline-check directory")
    output.mkdir(parents=True, exist_ok=True)
    config = output / "pystencils.json"
    config.write_text(json.dumps({"cache": {"object_cache": str(output / "compiled_cache"),
                                            "clear_cache_on_start": False}}), encoding="utf-8")
    os.environ["PYSTENCILS_CONFIG"] = str(config)
    os.environ["PYSTENCILS_CACHE_DIR"] = str(output / "joblib_cache")
    os.environ["MPLCONFIGDIR"] = str(output / "matplotlib_cache")
    network_attempts = []

    def denied(*args, **kwargs):
        network_attempts.append("Python socket network operation")
        raise AssertionError("Network forbidden during installed-wheel refinement check")

    socket.create_connection = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.getaddrinfo = denied
    socket.gethostbyname = denied
    socket.gethostbyname_ex = denied

    import aerokinetic
    from aerokinetic import refinement, refinement_checks
    import pytest
    modules = [aerokinetic, refinement, refinement_checks]
    if args.interface:
        from aerokinetic import interface, interface_checks
        modules += [interface, interface_checks]
    for module in modules:
        if not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise AssertionError(f"Must test the installed wheel, found {module.__file__}")
    print("Installed package:", aerokinetic.__file__, flush=True)
    root = Path(__file__).resolve().parents[1]
    tests = ["test_refinement.py", "test_geometry_and_units.py", "test_case_planning.py",
             "test_temporal.py", "test_experiments.py"]
    if args.interface:
        tests += ["test_interface.py"]
    status = pytest.main([*(str(root / "tests" / p) for p in tests), "-q", "-o", "pythonpath=",
                          f"--junitxml={output / 'tests.xml'}"])
    if status:
        raise SystemExit(status)
    from aerokinetic.cli import main as cli_main
    command = "verify-interface" if args.interface else "verify-refinement"
    cli_main([command, "--output", str(output / "numerical")])
    numerical = json.loads((output / "numerical" / "checks.json").read_text(encoding="utf-8"))
    if numerical["status"] != "passed" or network_attempts:
        raise AssertionError("Numerical or offline check failed")
    junit = ET.parse(output / "tests.xml")
    suites = list(junit.getroot().iter("testsuite"))
    result = {"status": "passed", "installed_package": aerokinetic.__file__,
              "tests_passed": sum(int(s.attrib["tests"]) for s in suites),
              "fresh_jit_caches": True, "python_network_connections_and_dns": "blocked",
              "blocked_attempt_count": len(network_attempts),
              "os_network_namespace_disconnected": False,
              "cli_command": command, "cli_verification": "passed",
              "cli_verify_refinement": "not_run" if args.interface else "passed",
              "analytic_shear_wave_remeshing": "not_run" if args.interface else numerical["status"],
              "numerical_suite": numerical["status"],
              "png_report": (output / "numerical" / ("interface_errors.png" if args.interface else "shear_wave.png")).is_file(),
              "spatial_interface": numerical["status"] if args.interface else "not_run", "airfoil_accuracy": "not_validated",
              "windows_msvc": "not_run", "gpu": "not_run"}
    (output / "OFFLINE_CHECK.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
