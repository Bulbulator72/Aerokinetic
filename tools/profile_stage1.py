"""Full compute-loop resource pilot; separate processes for 1/2 CPU threads."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--threads',type=int,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--snapshot',action='store_true')
    args=parser.parse_args()
    out=args.output or Path('/workspace/aerokinetic_stage1_20261106')/f'profile_threads{args.threads}.json'
    if out.exists()or out.with_suffix('.npz').exists():raise FileExistsError(out)
    import numpy as np
    import aerokinetic
    from aerokinetic import Solver,Flow,Grid,Section,naca0012
    start=time.perf_counter()
    s=Solver(Section((naca0012(81),),1.,(.25,0)),Flow(reynolds=30000,alpha_deg=4,physical_chord_m=.15),
       Grid(cells_per_chord=32,upstream_chords=1.5,downstream_chords=3,transverse_chords=1.5,
            span_chords=.5,memory_limit_gib=2,openmp_threads=args.threads))
    setup=time.perf_counter()-start
    for i in range(16):s.step(sample=(i+1)%8==0)
    start=time.perf_counter()
    for i in range(256):
        sample=(i+1)%8==0
        record=s.step(sample=sample)
        if sample:s.diagnostics()
    elapsed=time.perf_counter()-start
    a=s._stepper.data_handling.cpu_arrays[s._stepper.pdf_array_name]
    shape=s.plan.shape
    r={'version':aerokinetic.__version__,'threads':args.threads,'shape':shape,
      'warmup_steps':16,'timed_steps':256,'compute_seconds':elapsed,'setup_seconds':setup,
      'compute_loop_mlups':float(np.prod(shape)*256/elapsed/1e6),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
      'pdf_sha256':hashlib.sha256(a.tobytes()).hexdigest(),'last_sample':{k:record[k]for k in ('cl','cd','cm')},
      'owned_fluid_pdf_sha256':hashlib.sha256(a[1:-1,1:-1,1:-1][s._fluid].tobytes()).hexdigest(),
      'cpu_quota':Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
      'memory_limit_bytes':Path('/sys/fs/cgroup/memory.max').read_text().strip(),
      'interpretation':'Cloud compute loop only; no disk/Windows/GPU performance claim'}
    if args.snapshot:
        np.savez_compressed(out.with_suffix('.npz'),pdf=a,fluid_mask=s._fluid)
    out.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2),flush=True)


if __name__=='__main__':main()
