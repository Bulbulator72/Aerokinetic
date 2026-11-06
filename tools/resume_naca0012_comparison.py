"""Continue the original grid-64 case after its per-launch wall-clock limit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, required=True)
    args = parser.parse_args()
    runs = args.runs.resolve()
    output = runs / 'uniform64'
    initial = json.loads((output / 'summary.json').read_text())
    assert initial['status'] == 'stopped' and initial['termination_reason'] == 'wall_time_limit'
    assert initial['steps'] < 9600
    os.environ.update(PYSTENCILS_CONFIG=str(runs / 'pystencils.json'),
        PYSTENCILS_CACHE_DIR=str(runs / 'symbolic'),
        MPLCONFIGDIR=str(runs / 'matplotlib'), OPENBLAS_NUM_THREADS='1', MPLBACKEND='Agg')
    attempts = []
    def denied(*args, **kwargs):
        attempts.append('Python connection/DNS')
        raise RuntimeError('Network is disabled during continuation')
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = denied
    socket.getaddrinfo = socket.gethostbyname = socket.gethostbyname_ex = denied
    import aerokinetic
    from aerokinetic import Solver
    assert aerokinetic.__version__ == '0.5.0'
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    evidence = runs / 'continuation64'
    evidence.mkdir(exist_ok=False)
    for name in ('summary.json', 'metadata.json', 'RUN_CHECK.json'):
        source = runs / name if name == 'RUN_CHECK.json' else output / name
        (evidence / ('before_' + name)).write_bytes(source.read_bytes())
    prefix = (output / 'history.csv').read_bytes()
    previous_meta = json.loads((output / 'metadata.json').read_text())
    started = time.monotonic()
    def sample(solver, row):
        if solver.steps % 400 == 0:
            print(f"N=64 resumed step={solver.steps} t*={row['t_star']:.4f} Cl={row['cl']:.6g}", flush=True)
    summary = Solver.resume_run(output, callback=sample, progress=lambda text: print(text, flush=True))
    current_meta = json.loads((output / 'metadata.json').read_text())
    assert previous_meta['case_fingerprint'] == current_meta['case_fingerprint']
    assert previous_meta['run'] == current_meta['run']
    assert (output / 'history.csv').read_bytes().startswith(prefix)
    report = {'initial_summary':initial, 'final_summary':summary,
        'elapsed_seconds_including_restore':time.monotonic()-started,
        'original_settings_and_case_unchanged':True, 'original_history_prefix_unchanged':True,
        'history_prefix_bytes':len(prefix),'history_prefix_sha256':hashlib.sha256(prefix).hexdigest(),
        'python_network_attempts':attempts}
    (runs / 'RESUME_CHECK.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    assert summary['status'] == 'completed' and summary['steps'] == 9600 and summary['t_star'] == 6


if __name__ == '__main__':
    main()
