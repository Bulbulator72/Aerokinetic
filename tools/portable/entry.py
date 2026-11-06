"""Frozen executable entry; errors and self-test evidence survive windowed launch."""
import argparse
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import traceback
import uuid

from ak_portable_runtime import configure, verify_bundle
sys.dont_write_bytecode = True


def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description='AeroKinetic portable application')
    parser.add_argument('project', nargs='?', type=Path)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--gui', action='store_true', help='Exercise actual desktop windows during self-test')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    if args.self_test:
        output = (args.output or (Path(sys.executable).parent / 'checks' /
                  (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]))).resolve()
        output.mkdir(parents=True, exist_ok=False)
        workspace = output / 'runtime'
    else:
        parent = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share')))
        import hashlib
        identity = (bundle / 'portable/FILES_SHA256.json').read_bytes()
        workspace = parent / 'AeroKinetic/portable' / hashlib.sha256(identity).hexdigest()[:16]
        output = workspace / 'logs' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
        output.mkdir(parents=True)
    log = (output / 'AEROKINETIC_PORTABLE.log').open('a', encoding='utf-8', buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    # Record exceptions separately even when the freezer has a console.
    network_attempts = []
    if args.self_test:
        def denied(*a, **k):
            network_attempts.append('Python connection/DNS')
            raise RuntimeError('Network connections are forbidden during the portable self-test')
        socket.create_connection = socket.socket.connect = socket.socket.connect_ex = denied
        socket.getaddrinfo = socket.gethostbyname = socket.gethostbyname_ex = denied
    try:
        print('AeroKinetic portable; log:', log.name, flush=True)
        verified_files = verify_bundle(Path(sys.executable).parent) if args.self_test else None
        if args.self_test and any((workspace / 'jit').glob('*')):
            raise RuntimeError('Self-test requires a fresh empty JIT cache')
        runtime = configure(bundle, workspace)
        if args.self_test:
            from ak_portable_selftest import run_checks
            result = run_checks(output, runtime, gui=args.gui)
            assert not network_attempts
            result.update(python_network='blocked', network_attempts=len(network_attempts),
                          cold_cache_at_start=True, platform=sys.platform, bundle_files_verified=verified_files)
            (output / 'CHECK.json').write_text(json.dumps(result, indent=2) + '\n')
            print('PASS:', output / 'CHECK.json', flush=True)
        else:
            from aerokinetic.gui import main as gui_main
            gui_main([str(args.project.resolve())] if args.project else [])
        return 0
    except Exception as error:
        details = traceback.format_exc()
        log.write(details + '\n')
        print(details, file=sys.stderr, flush=True)
        if args.self_test:
            (output / 'CHECK.json').write_text(json.dumps({'status': 'failed', 'error': str(error),
                'traceback': details, 'platform': sys.platform, 'network_attempts': len(network_attempts)}, indent=2) + '\n')
        elif sys.platform == 'win32':
            try:
                import tkinter as tk
                from tkinter import messagebox
                window = tk.Tk(); window.withdraw()
                messagebox.showerror('AeroKinetic: ошибка запуска', str(error) + '\n\nЖурнал: ' + log.name)
                window.destroy()
            except Exception:
                pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
