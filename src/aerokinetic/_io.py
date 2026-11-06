"""Local, atomic output; no pickle or network access."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile


@contextmanager
def atomic_binary(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    with atomic_binary(path) as handle:
        handle.write((json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def file_hash(path, limit=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        remaining = limit
        while remaining is None or remaining > 0:
            chunk = handle.read(1024 * 1024 if remaining is None else min(remaining, 1024 * 1024))
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        if remaining is not None and remaining:
            raise ValueError("History is shorter than the saved checkpoint")
    return digest.hexdigest()
