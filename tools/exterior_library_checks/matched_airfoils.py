"""Installed-library full runs and fixed-window comparison with frozen data."""
import argparse
import csv
from dataclasses import asdict, replace
import gc
import hashlib
import json
from pathlib import Path
import resource
import time

import numpy as np
import aerokinetic
from aerokinetic import Project, Solver


def write(path, data):
    Path(path).write_text(json.dumps(data,indent=2,allow_nan=False)+"\n")


def read(path):
    with Path(path).open(newline="")as f:
        return list(csv.DictReader(f))


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--project-root",type=Path,required=True)
    p.add_argument("--prototype-runs",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--threads",type=int,default=2)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    package=Path(aerokinetic.__file__).parent
    write(a.output/"SOURCE_IDENTITY.json",{"version":aerokinetic.__version__,"installed":str(package),
        "sha256":{f.name:hashlib.sha256(f.read_bytes()).hexdigest()for f in package.glob("*.py")},
        "threads":a.threads,"scope":"Research CPU uniform; no accuracy or i9 scaling certification"})
    gates=[]
    for distance in (3,6):
        project=Project.load(a.project_root/f"examples/naca0012_re30000_n32_characteristic_down{distance}.json")
        project=replace(project,grid=replace(project.grid,farfield="exterior_regularized_fd_v1",openmp_threads=a.threads))
        output=a.output/f"airfoil_down{distance}"
        s=Solver(project.section,project.flow,project.grid)
        started=time.perf_counter()
        def progress(solver,record):
            if solver.steps%800==0:
                print(json.dumps({"distance":distance,"step":solver.steps,"t_star":solver.steps*solver.plan.dt_convective,
                    "cl":record["cl"],"cd":record["cd"],"elapsed_s":time.perf_counter()-started}),flush=True)
            return True
        result=s.run(output=output,callback=progress,**asdict(project.run))
        project.save(output/"input_project.json")
        complete=result["status"]=="completed" and result["steps"]==9600 and result["t_star"]==12
        check={"status":result["status"],"steps":s.steps,"t_star":s.steps*s.plan.dt_convective,
            "completion_gate_passed":complete,"identity":{"model":"exterior_regularized_fd_v1","core_plan":s.plan.as_dict()},
            "threads":a.threads,"max_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "experimental_validation":"not_performed","grid_convergence":"not_run"}
        if complete:
            rows=read(output/"history.csv")
            old=read(a.prototype_runs/f"airfoil_down{distance}/history.csv")
            assert [int(r["step"])for r in rows]==list(range(8,9601,8))
            assert len(old)==len(rows)==1200
            values=np.array([[float(r[k])for k in("cl","cd","cm")]for r in rows if 6<=float(r["t_star"])<=12])
            original=np.array([[float(r[k])for k in("cl","cd","cm")]for r in old if 6<=float(r["t_star"])<=12])
            assert len(values)==len(original)==600
            mean_error=float(np.max(abs(values.mean(axis=0)-original.mean(axis=0))))
            rms_error=float(np.max(abs(values.std(axis=0)-original.std(axis=0))))
            check.update(window6_12={"samples":600,"mean":dict(zip(("cl","cd","cm"),values.mean(axis=0).tolist())),
                "rms_about_mean":dict(zip(("cl","cd","cm"),values.std(axis=0).tolist()))},
                prototype_comparison={"mean_max_error":mean_error,"rms_max_error":rms_error,
                "history_max_error":max(abs(float(r[k])-float(q[k]))for r,q in zip(rows,old)for k in("t_star","cl","cd","cm")),
                "passed":mean_error<=2e-9 and rms_error<=2e-9})
            check["passed"]=check["prototype_comparison"]["passed"]
        else:
            check.update(passed=False,window6_12={"status":"not_evaluated","reason":"Incomplete fixed window"})
        write(output/"CHECKS.json",check);gates.append(check)
        write(a.output/"CHECKS.json",{"cases":gates,"passed":len(gates)==2 and all(g["passed"]for g in gates),
            "scope":"Fixed completion and refactor equivalence only; experimental agreement not certified"})
        print("FINISH",distance,json.dumps(check),flush=True)
        if not check["passed"]:
            raise RuntimeError("Fixed matched-airfoil gate failed; inspect the complete evidence")
        del s
        gc.collect()


if __name__=="__main__":main()
