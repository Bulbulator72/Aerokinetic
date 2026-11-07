#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v g++ >/dev/null || { echo 'Install g++ and Python 3.12 system development headers before going offline.'; exit 1; }
python3.12 tools/verify_offline.py downloads/wheels_linux
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links downloads/wheels_linux -r requirements-offline-py312.txt
.venv/bin/python -m pip install --no-index --find-links downloads/wheels_linux aerokinetic==0.5.2
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q -o pythonpath=
