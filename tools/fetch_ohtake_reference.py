"""Explicit online preparation of the copyrighted reference PDF; no solver network access."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

URL = 'https://www.jstage.jst.go.jp/article/jjsass/55/644/55_644_439/_pdf/-char/en'
SHA256 = '552c25268b8892c26f0cec7f45bc177f99b4109f926d3c60fc4a771c3a4924ee'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'references/ohtake2007.pdf')
    args = parser.parse_args()
    if args.output.exists():
        if hashlib.sha256(args.output.read_bytes()).hexdigest() != SHA256:
            raise SystemExit('Existing file differs; it has not been replaced.')
        print('Reference PDF already present; SHA-256 verified.')
        return
    request = urllib.request.Request(URL, headers={'User-Agent':'AeroKinetic-reference-preparation/0.5'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    if not data.startswith(b'%PDF') or hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit('Publisher response differs from the recorded PDF. Nothing saved; inspect the source manually: ' + URL)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(data)
    print('Saved reference PDF; original copyright and source conditions apply:', args.output)


if __name__ == '__main__':
    main()
