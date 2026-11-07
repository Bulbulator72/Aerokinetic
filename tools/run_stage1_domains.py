"""Frozen boundary-sensitivity matrix, installed unmodified 0.5.0 only."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import time


def main():
    work=Path('/workspace/aerokinetic_stage1_20261106')
    root=Path(__file__).resolve().parents[1]
    os.environ.update(PYSTENCILS_CONFIG='/workspace/aerokinetic_validation_20261106/runs/pystencils.json',
        PYSTENCILS_CACHE_DIR='/workspace/aerokinetic_validation_20261106/runs/symbolic',
        MPLCONFIGDIR=str(work/'matplotlib_domains'),OPENBLAS_NUM_THREADS='1',MPLBACKEND='Agg')
    def deny(*a,**k):raise RuntimeError('Network disabled during CFD')
    socket.create_connection=socket.socket.connect=socket.socket.connect_ex=deny
    socket.getaddrinfo=socket.gethostbyname=socket.gethostbyname_ex=deny
    import aerokinetic
    from aerokinetic import Section,naca0012,Flow,Grid,Solver,RunSettings,StopCriteria,Project
    assert aerokinetic.__version__=='0.5.0'
    plan=json.loads((root/'docs/verification_stage1_20261106/DOMAIN_PLAN.json').read_text())
    results={}
    for case in plan['cases']:
        section=Section((naca0012(81,closed_te=False),),1,(.25,0))
        flow=Flow(reynolds=30000,alpha_deg=4,physical_chord_m=.15,lattice_speed=.04,perturbation=.0001,seed=31,sgs='none')
        grid=Grid(cells_per_chord=32,upstream_chords=case['upstream'],downstream_chords=case['downstream'],
            transverse_chords=case['transverse_each_side'],span_chords=.5,memory_limit_gib=2,openmp_threads=2)
        settings=RunSettings(duration_convective=12,sample_every=8,frame_every=2400,check_every=128,
            average_after=6,checkpoint_every=2400,termination=StopCriteria(wall_time_limit_s=3600))
        print('DOMAIN',case['name'],Project(section,flow,grid,settings).plan().shape,flush=True)
        solver=Solver(section,flow,grid,progress=lambda m:print(m,flush=True))
        def sample(s,r):
            if s.steps%1600==0:print(f"DOMAIN {case['name']} t*={r['t_star']:.4f} Cl={r['cl']:.6g}",flush=True)
        started=time.monotonic()
        result=solver.run(output=work/case['name'],callback=sample,**asdict(settings))
        results[case['name']]={'summary':result,'elapsed_s':time.monotonic()-started}
        (work/'DOMAIN_CHECK.json').write_text(json.dumps(results,indent=2)+'\n')
        assert result['status']=='completed' and result['steps']==9600
        del solver
        import gc;gc.collect()
    print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':main()
