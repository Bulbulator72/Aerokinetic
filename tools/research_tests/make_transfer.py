"""Build one UTF-8 BAT/TXT containing only verified source text, not binaries."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def entries():
    baseline = json.loads((ROOT / "docs/verification_exterior_library_20261108/BUILD_IDENTITY.json").read_text())
    for name, expected in baseline["builds"]["final"]["module_sha256"].items():
        if sha((ROOT / "src/aerokinetic" / name).read_bytes()) != expected:
            raise ValueError("Research core differs from the verified 0.5.3 source: " + name)
    result = {}
    for path in sorted((ROOT / "src/aerokinetic").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            if path.suffix not in (".py", ".json", ".csv"):
                raise ValueError("Unexpected runtime member: " + str(path))
            result["source/" + path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ("pyproject.toml", "requirements-offline-py312.txt", "LICENSE", "AGENTS.md",
                 "VALIDATION_RU.md", "docs/PROGRAM_REQUIREMENTS_RU.md", "docs/CURRENT_STATE_RU.md"):
        result["source/" + name] = (ROOT / name).read_bytes()
    for path in sorted((ROOT / ".agents/skills").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            result["source/" + path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ("bootstrap.py", "run_suite.py", "study_protocol.json", "README_RU.md"):
        result[name] = (TOOLS / name).read_bytes()
    result["ak_portable_runtime.py"] = (ROOT / "tools/portable/ak_portable_runtime.py").read_bytes()
    result["fluid_budget.py"] = (ROOT / "tools/exterior_library_checks/budget.py").read_bytes()
    result["guard/sitecustomize.py"] = (ROOT / "tools/exterior_library_checks/offline_guard/sitecustomize.py").read_bytes()
    result["base_project.json"] = (ROOT / "examples/naca0012_re30000_n32_exterior_down6.json").read_bytes()
    tunnel = ROOT / "docs/verification_naca0012_20261106/comparison/ohtake_re30000_digitized_all_coefficients.csv"
    result["ohtake_re30000.csv"] = tunnel.read_bytes()
    package = {path.relative_to(ROOT / "src/aerokinetic").as_posix():sha(path.read_bytes())
               for path in sorted((ROOT / "src/aerokinetic").rglob("*"))
               if path.is_file() and "__pycache__" not in path.parts}
    core_id = sha(json.dumps(package, sort_keys=True).encode())[:20]
    result["CORE_IDENTITY.json"] = (json.dumps({"version":"0.5.3", "id":core_id,
        "source_commit":"7b53e22693b5e0971e23f0bf9770697fe1f8a5ac", "package_files":package,
        "scope":"Exact verified research source; no high-Re or validated-polar claim"}, indent=2) + "\n").encode()
    assert len({name.casefold() for name in result}) == len(result)
    for name, data in result.items():
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        data.decode("utf-8")
    return result


def make(output, unpack=None):
    members = entries()
    payload = json.dumps({"schema":1, "files":{
        name:{"content":data.decode("utf-8"), "bytes":len(data), "sha256":sha(data)}
        for name, data in sorted(members.items())}}, ensure_ascii=False, separators=(",", ":"))
    digest = sha(payload.encode("utf-8"))
    powershell = (TOOLS / "launch.ps1").read_text(encoding="utf-8").replace("__PAYLOAD_SHA256__", digest)
    command = ("$ErrorActionPreference='Stop'; try { "
        "$raw=[IO.File]::ReadAllText($env:AK_RESEARCH_SELF,[Text.Encoding]::UTF8); "
        "$m=[regex]::Match($raw,'(?ms)^AK_RESEARCH_LAUNCH_BEGIN\\r?\\n(?<code>.*?)^AK_RESEARCH_LAUNCH_END\\r?$'); "
        "if (-not $m.Success) { throw 'Save the complete BAT/TXT file.' }; "
        "& ([ScriptBlock]::Create($m.Groups['code'].Value)) "
        "} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }")
    assert '"' not in command and len(command) < 7000
    header = ["@echo off", "setlocal DisableDelayedExpansion", "chcp 65001 >nul",
        "title AeroKinetic - research calculations 0.5.3", 'set "AK_RESEARCH_SELF=%~f0"',
        'if not defined AK_TEST_THREADS set "AK_TEST_THREADS=auto"',
        'if not defined AK_TEST_GRIDS set "AK_TEST_GRIDS=32,64"',
        'if not defined AK_TEST_DURATION set "AK_TEST_DURATION=12"',
        "echo AeroKinetic: NACA0012, Re=30000, alpha=4 deg. Offline research calculations.",
        "echo Default grids: N32 and N64. First compilation can take several minutes.",
        "echo Existing installation is preserved. No administrator rights are requested.",
        f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "{command}"',
        'set "AK_TEST_EXIT=%ERRORLEVEL%"', 'if "%AK_TEST_EXIT%"=="2" echo Paused and saved. Run this BAT again to continue.',
        'if not "%AK_TEST_EXIT%"=="0" if not "%AK_TEST_EXIT%"=="2" echo Test run reported an error. Keep the log and FOR_REVIEW.zip.',
        "pause", "exit /b %AK_TEST_EXIT%", "", "AK_RESEARCH_LAUNCH_BEGIN"]
    content = "\r\n".join(header) + "\r\n" + powershell.replace("\r\n", "\n").replace("\n", "\r\n")
    content += "\r\nAK_RESEARCH_LAUNCH_END\r\nAK_RESEARCH_DATA_BEGIN\r\n" + payload + "\r\nAK_RESEARCH_DATA_END\r\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content.encode("utf-8"))
    output.with_suffix(".txt").write_bytes(content.encode("utf-8"))
    for version in (content, content.replace("\r\n", "\n")):
        found = re.search(r"(?ms)^AK_RESEARCH_DATA_BEGIN\r?\n(?P<data>.*?)^AK_RESEARCH_DATA_END\r?$", version)
        parsed = json.loads(found["data"].strip())
        assert sha(found["data"].strip().encode()) == digest
        for name, value in parsed["files"].items():
            data = value["content"].encode("utf-8")
            assert len(data) == value["bytes"] and sha(data) == value["sha256"] and data == members[name]
    if unpack:
        unpack.mkdir(parents=True, exist_ok=False)
        for name, data in members.items():
            path = unpack / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    result = {"artifact":str(output), "bytes":output.stat().st_size, "sha256":sha(output.read_bytes()),
              "text_source_members":len(members), "payload_sha256":digest, "new_embedded_binaries":0,
              "payload_roundtrip_crlf_and_lf":"passed", "windows_execution":"not_run"}
    output.with_suffix(".check.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / "DOWNLOAD_AEROKINETIC/RUN_RESEARCH_TESTS.bat")
    parser.add_argument("--unpack", type=Path)
    args = parser.parse_args(); make(args.output, args.unpack)


if __name__ == "__main__":
    main()
