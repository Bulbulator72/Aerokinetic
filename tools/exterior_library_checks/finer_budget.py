"""Short finer-grid resource pilot. It cannot certify airfoil convergence."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import resource
import time

from pystencils import get_code_str
from aerokinetic import Grid,Plan,Project,Solver


def main():
    p=argparse.ArgumentParser();p.add_argument("--project",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    project=Project.load(a.project)
    started=time.perf_counter();s=Solver(project.section,project.flow,project.grid)
    construction=time.perf_counter()-started
    for _ in range(8):s.step()
    started=time.perf_counter();cpu=time.process_time()
    for _ in range(32):s.step(sample=True)
    elapsed=time.perf_counter()-started;cpu=time.process_time()-cpu
    diagnostics=s.diagnostics();s.save_frame(a.output/"pilot_frame_local.npz")
    kernels=[]
    for kernel in s._stepper._lbmKernels:
        text=str(get_code_str(kernel.ast))
        kernels.append({"openmp_parallel_pragma":"#pragma omp parallel"in text,
                        "explicit_two_threads":"num_threads(2)"in text,
                        "openmp_lines":[line.strip()for line in text.splitlines()if"#pragma omp"in line]})
    larger=replace(project.grid,cells_per_chord=128,memory_limit_gib=128)
    plan128=Plan.create(project.section,project.flow,larger)
    needed=round(12/s.plan.dt_convective)
    result={"cells_per_chord":project.grid.cells_per_chord,"physical_shape":s.plan.shape,
        "storage_shape":s._storage_shape,"molecular_nu":s.plan.viscosity,
        "dt_convective":s.plan.dt_convective,"threads":s.grid.openmp_threads,
        "construction_seconds":construction,"measured_steps":32,"total_steps":s.steps,
        "measured_wall_seconds":elapsed,"measured_cpu_seconds":cpu,
        "peak_rss_gib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
        "estimated_memory_gib":s.plan.estimated_memory_gib,
        "estimated_kernel_wall_hours_for_t12":elapsed/32*needed/3600,
        "estimated_kernel_cpu_hours_for_t12":cpu/32*needed/3600,
        "N128_estimated_memory_gib":plan128.estimated_memory_gib,
        "kernels":kernels,"diagnostics":diagnostics,
        "passed":all(k["openmp_parallel_pragma"]and k["explicit_two_threads"]for k in kernels),
        "grid_convergence":"not_run","experimental_validation":"not_run",
        "limits":"40steps total are a startup/resource pilot. Extrapolation excludes frame/checkpoint/residual overhead and is affected by shared2CPU quota. N128 is estimated only. No i9/Windows scaling or aerodynamic accuracy claim."}
    (a.output/"CHECKS.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2),flush=True)


if __name__=="__main__":main()
