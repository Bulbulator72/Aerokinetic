"""Cold JIT, GIF, fresh-process resume and continuation without network."""
import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import aerokinetic
from aerokinetic import Flow,Grid,Section,Solver,naca4


def rows(path):
    with Path(path).open(newline="")as f:return list(csv.DictReader(f))


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make():
    return Solver(Section((naca4("0012",31),)),Flow(reynolds=30000,alpha_deg=4,physical_chord_m=.15),
        Grid(cells_per_chord=16,upstream_chords=1.5,downstream_chords=3,transverse_chords=1.5,
             span_chords=.5,memory_limit_gib=1,openmp_threads=2,farfield="exterior_regularized_fd_v1"))


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True)
    p.add_argument("--jit-cache",type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    if not getattr(sys,"_aerokinetic_network_denied",False):
        raise RuntimeError("Run this check with the recorded network-denial sitecustomize")
    if a.jit_cache.exists()and any(a.jit_cache.iterdir()):
        raise RuntimeError("The specified JIT object cache must be empty")
    s=make();settings=dict(duration_convective=32*s.plan.dt_convective,sample_every=4,
        check_every=8,frame_every=8,average_after=16*s.plan.dt_convective,checkpoint_every=8)
    partial=s.run(output=a.output/"parent",callback=lambda solver,record:solver.steps<16,**settings)
    assert partial["status"]=="stopped"and partial["steps"]==16
    resume=subprocess.run([sys.executable,"-m","aerokinetic.cli","resume",str(a.output/"parent")],
                          check=True,text=True,capture_output=True)
    (a.output/"resume.log").write_text(resume.stdout+resume.stderr)
    reference=make();reference.run(output=a.output/"reference",**settings)
    assert(a.output/"parent/history.csv").read_bytes()==(a.output/"reference/history.csv").read_bytes()
    restored=Solver.from_checkpoint(a.output/"parent/checkpoint.npz")
    for name in(restored._stepper.pdf_array_name,restored._stepper._tmp_arr_name):
        np.testing.assert_array_equal(restored._stepper.data_handling.cpu_arrays[name],
            reference._stepper.data_handling.cpu_arrays[name])
    assert restored.boundary_diagnostics()==reference.boundary_diagnostics()
    gif=a.output/"flow.gif"
    animation=subprocess.run([sys.executable,"-m","aerokinetic.cli","animate",str(a.output/"parent"),
        "--field","speed","--output",str(gif)],check=True,text=True,capture_output=True)
    (a.output/"animation.log").write_text(animation.stdout+animation.stderr)
    with Image.open(gif)as image:
        gif_frames=image.n_frames
        assert gif_frames>=2
    required=("metadata.json","summary.json","project.json","history.csv","residuals.csv","checkpoint.npz")
    before={name:digest(a.output/"parent"/name)for name in required}
    continuation=subprocess.run([sys.executable,"-m","aerokinetic.cli","continue",str(a.output/"parent"),
        "--output",str(a.output/"child"),"--additional-time",str(8*s.plan.dt_convective)],
        check=True,text=True,capture_output=True)
    (a.output/"continuation.log").write_text(continuation.stdout+continuation.stderr)
    expected=[]
    for _ in range(8):
        record=reference.step(sample=True)
        if record["step"]%4==0:expected.append(record)
    child=rows(a.output/"child/history.csv")
    error=max(abs(float(r[k])-q[k])for r,q in zip(child,expected)
        for k in("t_star","cl","cd","cm","wall_mass_exchange_accumulated","exterior_source_mass_accumulated"))
    assert len(child)==len(expected)==2 and error==0
    assert all(digest(a.output/"parent"/name)==value for name,value in before.items())
    libraries=list(a.jit_cache.rglob("*.so"))
    assert libraries,"No newly compiled object modules in the cold cache"
    routes=max(0,len(Path("/proc/net/route").read_text().splitlines())-1)
    result={"passed":True,"version":aerokinetic.__version__,"installed":aerokinetic.__file__,
        "fresh_process_resume_history_identical":True,"fresh_process_resume_pdfs_identical":True,
        "continuation_load_mass_clock_max_error":error,"parent_files_unchanged":True,
        "gif_frames":gif_frames,"cold_jit_modules":len(libraries),"non_loopback_route_rows":routes,
        "python_network_audit_guard":True,"source_files_sha256":{p.name:digest(p)for p in Path(aerokinetic.__file__).parent.glob("*.py")},
        "scope":"Linux CPU1/2 dependency-local runtime; Windows/GPU not exercised"}
    (a.output/"CHECKS.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items()if k!="source_files_sha256"},indent=2),flush=True)


if __name__=="__main__":main()
