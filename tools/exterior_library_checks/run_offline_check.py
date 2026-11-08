"""Launch the Linux offline fixture using a supplied installed-wheel Python."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser();p.add_argument("--python",type=Path,default=Path(sys.executable))
    p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    root=a.output.resolve();root.mkdir(parents=True,exist_ok=False)
    tools=Path(__file__).resolve().parent
    config=root/"pystencils.json"
    config.write_text(json.dumps({"cache":{"object_cache":str(root/"jit"),"clear_cache_on_start":False}})+"\n")
    env=dict(os.environ)
    env.update(PYSTENCILS_CONFIG=str(config),PYSTENCILS_CACHE_DIR=str(root/"symbolic"),
        XDG_CACHE_HOME=str(root/"cache"),MPLCONFIGDIR=str(root/"matplotlib"),
        PYTHONPATH=str(tools/"offline_guard"),OPENBLAS_NUM_THREADS="1",
        AEROKINETIC_NETWORK_AUDIT=str(root/"network_audit.jsonl"))
    # Keep the venv executable path; resolving its symlink selects base Python
    # and loses the isolated installed-wheel environment.
    command=[str(a.python.absolute()),str(tools/"offline_runtime.py"),"--output",str(root/"runtime"),
             "--jit-cache",str(root/"jit")]
    with(root/"runtime.log").open("w")as log:
        result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Offline fixture failed; inspect {root/'runtime.log'}")
    events=[json.loads(line)["event"]for line in(root/"network_audit.jsonl").read_text().splitlines()]
    if not events or any(event!="guard_installed"for event in events):
        raise RuntimeError("Unexpected network audit events")
    check=json.loads((root/"runtime/CHECKS.json").read_text())
    check.update(network_attempts=0,guarded_python_processes=len(events))
    (root/"CHECKS.json").write_text(json.dumps(check,indent=2)+"\n")
    print(json.dumps({k:v for k,v in check.items()if k!="source_files_sha256"},indent=2))


if __name__=="__main__":main()
