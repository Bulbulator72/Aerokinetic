import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheelhouse", type=Path)
    args = parser.parse_args()
    data = json.loads((args.wheelhouse / "SHA256.json").read_text(encoding="utf-8"))
    for entry in data["files"]:
        path = args.wheelhouse / entry["file"]
        if not path.is_file() or path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise SystemExit(f"Integrity check failed: {path}")
    print(f"Verified {len(data['files'])} wheels")


if __name__ == "__main__":
    main()
