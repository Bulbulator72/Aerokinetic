"""Actual installed 0.5 NACA0012 runs; no calibration against the experiment."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    config=out/'pystencils.json'
    config.write_text(json.dumps({'cache':{'object_cache':str(out/'jit'),'clear_cache_on_start':False}}))
    os.environ.update(PYSTENCILS_CONFIG=str(config),PYSTENCILS_CACHE_DIR=str(out/'symbolic'),
        MPLCONFIGDIR=str(out/'matplotlib'),OPENBLAS_NUM_THREADS='1',MPLBACKEND='Agg')
    attempts=[]
    def denied(*a,**k):
        attempts.append('Python connection/DNS')
        raise RuntimeError('Network is disabled during the calculation')
    socket.create_connection=socket.socket.connect=socket.socket.connect_ex=denied
    socket.getaddrinfo=socket.gethostbyname=socket.gethostbyname_ex=denied
    import aerokinetic
    from aerokinetic import Contour,Section,Flow,Grid,Project,RunSettings,StopCriteria,Solver,naca0012
    assert aerokinetic.__version__=='0.5.0'
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    root=Path(__file__).resolve().parents[1]
    spec=json.loads((root/'docs/verification_naca0012_20261106/PLAN.json').read_text())['new_runs']
    report={'status':'running','installed':aerokinetic.__file__,'version':aerokinetic.__version__,
        'cases':{},'experimental_validation':'inconclusive; comparison only',
        'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(aerokinetic.__file__).parent.glob('*.py')}}
    started=time.monotonic()
    try:
        for n in spec['grids_cells_per_chord']:
            section=Section((naca0012(n_side=81,closed_te=False),),chord=1.,moment_reference=(.25,0.))
            flow=Flow(reynolds=spec['reynolds'],alpha_deg=spec['alpha_deg'],physical_chord_m=spec['physical_chord_m'],
                lattice_speed=spec['lattice_speed'],sgs=spec['sgs'],perturbation=spec['initial_perturbation'],seed=spec['seed'])
            d=spec['domain_chords']
            grid=Grid(cells_per_chord=n,upstream_chords=d['upstream'],downstream_chords=d['downstream'],
                transverse_chords=d['transverse_each_side'],span_chords=d['periodic_span'],
                memory_limit_gib=spec['memory_limit_gib'],openmp_threads=spec['threads'])
            run=RunSettings(duration_convective=spec['duration_t_star'],average_after=spec['average_after_t_star'],
                sample_every=spec['sample_every_steps'],frame_every=spec['frame_every_steps'],
                check_every=spec['field_check_every_steps'],checkpoint_every=spec['checkpoint_every_steps'],
                termination=StopCriteria(wall_time_limit_s=spec['wall_time_limit_seconds_per_run_after_setup']))
            project=Project(section,flow,grid,run,f'NACA0012 Re30000 alpha4 grid{n}; preliminary tunnel comparison')
            plan=project.plan()
            print(f'CASE N={n}: {plan.shape}, estimated {plan.estimated_memory_gib:.3f} GiB, target t*=6, average t*>=3',flush=True)
            solver=Solver(section,flow,grid,progress=lambda m:print(m,flush=True))
            case_start=time.monotonic()
            def sample(s,r):
                if s.steps%400==0:
                    print(f"N={n} step={s.steps} t*={r['t_star']:.4f} Cl={r['cl']:.6g} elapsed={time.monotonic()-case_start:.1f}s",flush=True)
            summary=solver.run(output=out/f'uniform{n}',callback=sample,**asdict(run))
            report['cases'][f'uniform{n}']={'summary':summary,'plan':plan.as_dict(),'elapsed_seconds':time.monotonic()-case_start}
            (out/'RUN_CHECK.json').write_text(json.dumps(report,indent=2)+'\n')
            print(f"CASE N={n}: {summary['status']}; reason={summary.get('termination_reason')}; t*={summary.get('t_star')}",flush=True)
            del solver
            import gc;gc.collect()
        report.update(status='finished',elapsed_seconds=time.monotonic()-started,python_network_attempts=attempts)
    except Exception as error:
        report.update(status='failed',error=str(error),traceback=traceback.format_exc(),elapsed_seconds=time.monotonic()-started)
        raise
    finally:
        (out/'RUN_CHECK.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
