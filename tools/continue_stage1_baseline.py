"""Longer 0.5 baseline in a new directory; original result is immutable."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time


def main():
    work=Path('/workspace/aerokinetic_stage1_20261106')
    work.mkdir(exist_ok=True)
    os.environ.update(PYSTENCILS_CONFIG='/workspace/aerokinetic_validation_20261106/runs/pystencils.json',
        PYSTENCILS_CACHE_DIR='/workspace/aerokinetic_validation_20261106/runs/symbolic',
        MPLCONFIGDIR=str(work/'matplotlib'),OPENBLAS_NUM_THREADS='1',MPLBACKEND='Agg')
    attempts=[]
    def deny(*a,**k):
        attempts.append('Python connection/DNS')
        raise RuntimeError('Network disabled during baseline continuation')
    socket.create_connection=socket.socket.connect=socket.socket.connect_ex=deny
    socket.getaddrinfo=socket.gethostbyname=socket.gethostbyname_ex=deny
    import aerokinetic
    from aerokinetic import Solver,StopCriteria
    assert aerokinetic.__version__=='0.5.0'
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    source=Path('/workspace/aerokinetic_validation_20261106/runs/uniform32')
    original={name:hashlib.sha256((source/name).read_bytes()).hexdigest()
              for name in ('metadata.json','summary.json','project.json','history.csv','residuals.csv')}
    started=time.monotonic()
    solver=Solver.from_checkpoint(source/'checkpoint.npz',progress=lambda m:print(m,flush=True))
    assert solver.steps==4800
    assert solver.flow.reynolds==30000 and solver.flow.alpha_deg==4
    output=work/'baseline32_t24'
    def sample(s,r):
        if s.steps%800==0:print(f"BASELINE step={s.steps} t*={r['t_star']:.5f} Cl={r['cl']:.6g} Cd={r['cd']:.6g}",flush=True)
    summary=solver.run(18,output,sample_every=8,frame_every=2400,check_every=128,
        average_after=12,checkpoint_every=2400,callback=sample,
        termination=StopCriteria(wall_time_limit_s=3600))
    for name,digest in original.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest
    report={'source':str(source),'source_steps':4800,'source_t_star':6,'target_t_star':24,
        'additional_t_star':18,'source_file_sha256':original,'source_unchanged':True,
        'installed':aerokinetic.__file__,'runtime_version':aerokinetic.__version__,
        'case_fingerprint':solver._checkpoint_context.get('case_fingerprint'),
        'summary':summary,'python_network_attempts':attempts,'elapsed_s':time.monotonic()-started,
        'interpretation':'Additional evolution of the unchanged grid32 case; no grid/statistical/experimental validation inferred'}
    (output/'continuation_provenance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (work/'BASELINE_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    assert summary['status']=='completed' and summary['steps']==19200


if __name__=='__main__':main()
