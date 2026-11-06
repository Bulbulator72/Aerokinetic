"""Reproduce collector/transport checks; does not execute Windows probes."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import socket
import tempfile


root = Path(__file__).resolve().parents[2]
source = root / "tools/collect_compiler_diagnostics.py"
spec = importlib.util.spec_from_file_location("collector", source)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
assert collector.sys.platform != "win32", "This verification records Linux-only checks explicitly"

network_attempts = []
def blocked(*args, **kwargs):
    network_attempts.append(True)
    raise AssertionError("Network forbidden during collection")
socket.socket = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked

def snapshot(path):
    return {str(p.relative_to(path)): (p.stat().st_mtime_ns, p.read_bytes())
            for p in path.rglob("*") if p.is_file() and not p.name.startswith("COMPILER_DIAGNOSTICS_")}

with tempfile.TemporaryDirectory(prefix="ak_compiler_diagnostics_") as temporary:
    fixture = Path(temporary) / "Папка с пробелами"
    fixture.mkdir()
    temp = Path(temporary) / "Temporary files"
    temp.mkdir()
    layout = fixture / "downloads/vs_buildtools"
    layout.mkdir(parents=True)
    (layout / "vs_installer.opc").write_bytes(b"TEST FIXTURE - NOT AN INSTALLER")
    (fixture / "project.json").write_text('{"keep":"unchanged"}', encoding="utf-8")
    (fixture / "downloads/compiler_install.log").write_text("INSTALL_EXIT_CODE=5003\n", encoding="utf-8")
    (temp / "dd_bootstrapper_utf16.log").write_text(
        "Bootstrapper header\nПроверка подписи: InvalidCertificate 0x80131509\n", encoding="utf-16")
    copied = fixture / "downloads/compiler_install_details/20261028_example"
    copied.mkdir(parents=True)
    (copied / "dd_bootstrapper_copy.log").write_bytes((temp / "dd_bootstrapper_utf16.log").read_bytes())
    large = temp / "dd_setup_large.log"
    large.write_bytes(("HEADER: начало\n" + "x" * collector.LOG_LIMIT + "\nTAIL: конец\n").encode("utf-16"))
    before = snapshot(fixture)
    before_temp = snapshot(temp)
    report = collector.collect(fixture, temp)
    body = report.read_text(encoding="utf-8")
    assert "Проверка подписи: InvalidCertificate 0x80131509" in body
    assert "INSTALL_EXIT_CODE=5003" in body
    assert "Identical log already included above" in body
    assert "[TRUNCATED:" in body and "HEADER: начало" in body and "TAIL: конец" in body
    assert "Latest installation root cause: inconclusive" in body
    assert '"status": "not_run"' in body
    assert snapshot(fixture) == before and snapshot(temp) == before_temp
    second = collector.collect(fixture, temp)
    assert second != report and second.is_file() and report.is_file()
    empty = Path(temporary) / "empty"
    empty.mkdir()
    empty_report = collector.collect(empty, None).read_text(encoding="utf-8")
    assert "No dd_bootstrapper log found" in empty_report
    assert "MISSING: vs_installer.opc" in empty_report

    # Execute the code decoded from the actual downloadable TXT, rather than
    # merely asserting that the text payload looks like a Python file.
    artifact = root.parent / "DOWNLOAD_AEROKINETIC/COLLECT_COMPILER_DIAGNOSTICS.txt"
    raw = artifact.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    raw.decode("ascii")
    assert max(map(len, raw.splitlines())) < 8191
    for variant in (raw, raw.replace(b"\r\n", b"\n")):
        match = re.search(rb"(?ms)^AK_DIAG_BEGIN\r?\n(?P<data>.*?)^AK_DIAG_END\r?$", variant)
        decoded = base64.b64decode(re.sub(rb"\s+", b"", match["data"]), validate=True)
        assert decoded == source.read_bytes()
        digest = hashlib.sha256(decoded).hexdigest()
        assert digest.encode("ascii") in raw[:raw.index(b"\r\nAK_DIAG_BEGIN")]
        decoded_namespace = {"__name__": "decoded_collector"}
        exec(compile(decoded, "downloaded_collector.py", "exec"), decoded_namespace)
        decoded_report = decoded_namespace["collect"](fixture, temp)
        assert "InvalidCertificate" in decoded_report.read_text(encoding="utf-8")
        assert snapshot(fixture) == before and snapshot(temp) == before_temp
    changed = bytearray(decoded)
    changed[100] ^= 1
    assert hashlib.sha256(changed).hexdigest() != digest
    assert not network_attempts

result = {
    "scope": "Read-only offline compiler evidence collection; no installer fix claim",
    "implementation_and_transport": "passed",
    "checks": ["Unicode program path", "UTF-16 logs", "bootstrapper log prioritization",
               "duplicate log suppression", "bounded large log head/tail",
               "missing evidence explicitly marked", "input byte/mtime preservation",
               "unique new reports", "actual downloaded payload executed (CRLF/LF)",
               "payload SHA and corruption rejection contract", "ASCII and CMD line length"],
    "network_connections_blocked": True,
    "observed_network_attempts": len(network_attempts),
    "native_windows_bat_powershell": "not_run",
    "msvc_installation": "not_run",
    "latest_user_root_cause": "inconclusive; awaiting current report",
    "numerical_solver_and_dependencies": "unchanged",
    "sources": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                (source, root / "tools/make_compiler_diagnostics.py", Path(__file__).resolve())},
    "download_sha256": hashlib.sha256(raw).hexdigest(),
    "download_bytes": len(raw),
}
(Path(__file__).parent / "CHECKS.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
