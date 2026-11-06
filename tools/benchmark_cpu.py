"""Warm whole-step timings; compare identical cases, not just collision kernels.

--reference-python may point to a separate installed AeroKinetic 0.3.0 venv.
All workers import installed wheels with -I. JIT and file output are outside
timed batches. This does not predict speed on a different CPU or a different mesh.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def worker(args):
    import aerokinetic
    from aerokinetic import Project,RefinedSolver
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    p=Project.from_dict({'geometry':{'naca0012':{'n_side':51}},
       'flow':{'reynolds':10000,'alpha_deg':5,'physical_chord_m':.15,'perturbation':.001},
       'grid':{'cells_per_chord':args.cells,'upstream_chords':1,'downstream_chords':2,'transverse_chords':1,
               'span_chords':.5,'memory_limit_gib':4,'openmp_threads':args.worker_threads}})
    solver=RefinedSolver(p)
    for _ in range(4):solver.step()
    times=[];cpu=[]
    for _ in range(3):
        start=time.perf_counter();process=time.process_time()
        for _ in range(8):record=solver.step()
        cpu.append(time.process_time()-process);times.append(time.perf_counter()-start)
    result={'version':aerokinetic.__version__,'threads':args.worker_threads,'coarse_cells_per_chord':args.cells,
            'fine_cells_per_chord':2*args.cells,'shape':solver.plan.shape,'fine_shape':solver.fine_shape,
            'steps_per_batch':8,'warmup_steps':4,'seconds_per_batch':times,'cpu_seconds_per_batch':cpu,
            'median_seconds_per_coarse_step':statistics.median(times)/8,
            'median_cpu_time_per_wall_time':statistics.median(c/t for c,t in zip(cpu,times)),
            'final_loads':record,'integrals':solver.conserved_integrals(),'last_budget':solver.last_budget,
            'imported_package':aerokinetic.__file__}
    print(json.dumps(result),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--threads',type=int,nargs='+',default=[1,2])
    parser.add_argument('--cells',type=int,default=32)
    parser.add_argument('--reference-python',type=Path)
    parser.add_argument('--worker',action='store_true');parser.add_argument('--worker-threads',type=int,default=1)
    args=parser.parse_args()
    if args.worker:worker(args);return
    if args.output is None:parser.error('--output is required')
    args.output.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_WAIT_POLICY='PASSIVE')
    jobs=[]
    if args.reference_python:jobs.append(('reference030',args.reference_python,1))
    jobs.extend((f'current_{n}_threads',Path(sys.executable),n) for n in args.threads)
    results=[]
    for label,python,threads in jobs:
        print(f'{label}: {args.cells} -> {2*args.cells} cells/chord, {threads} CPU threads',flush=True)
        command=[str(python),'-I',str(Path(__file__).resolve()),'--worker','--worker-threads',str(threads),'--cells',str(args.cells)]
        process=subprocess.run(command,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        (args.output/(label+'.log')).write_text(process.stdout+process.stderr)
        result=json.loads(process.stdout.splitlines()[-1]);result['label']=label;results.append(result)
        print(f"  {result['median_seconds_per_coarse_step']:.6f} seconds/coarse step",flush=True)
    baseline=results[0]['median_seconds_per_coarse_step']
    for result in results:result['speedup_against_first']=baseline/result['median_seconds_per_coarse_step']
    context={'logical_cpus':os.cpu_count(),'affinity_cpus':len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None}
    quota=Path('/sys/fs/cgroup/cpu.max')
    if quota.is_file():context['cgroup_cpu_max']=quota.read_text().strip()
    report={'status':'passed','hardware':context,'OPENBLAS_NUM_THREADS':'1','OMP_WAIT_POLICY':'PASSIVE',
            'includes':'Whole synchronized coarse step with both fine substeps, walls, loads, fluxes and budgets',
            'excludes':'Construction, JIT compilation, residual checks and output files','results':results,
            'native_windows':'not_run'}
    (args.output/'CHECK.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
