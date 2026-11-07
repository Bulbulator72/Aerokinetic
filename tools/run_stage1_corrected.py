"""Fresh installed 0.5.1 calculation under the frozen corrected-version plan."""
from dataclasses import asdict
import json
from pathlib import Path
import sys


def main():
    import aerokinetic
    from aerokinetic import Flow,Grid,Section,naca0012,Solver,RunSettings,StopCriteria
    assert aerokinetic.__version__=='0.5.1'
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    plan=Path(__file__).resolve().parents[1]/'docs/verification_stage1_20261106/CORRECTED_PLAN.json'
    p=json.loads(plan.read_text());c=p['case'];t=p['time']
    section=Section((naca0012(c['naca0012_points_per_side'],closed_te=c['closed_te']),),1.,tuple(c['moment_reference']))
    flow=Flow(**{k:c[k]for k in ('reynolds','alpha_deg','physical_chord_m','lattice_speed','sgs','perturbation','seed')})
    solver=Solver(section,flow,Grid(**p['grid'],memory_limit_gib=2))
    run=RunSettings(**{k:v for k,v in t.items()if k!='wall_time_limit_s'},
                    termination=StopCriteria(wall_time_limit_s=t['wall_time_limit_s']))
    def sample(s,r):
        if s.steps%800==0:print(f"t*={r['t_star']:.3f} Cl={r['cl']:.6g} Cd={r['cd']:.6g}",flush=True)
    output=Path('/workspace/aerokinetic_stage1_20261106/corrected32_t12')
    result=solver.run(output=output,callback=sample,**asdict(run))
    assert result['status']=='completed'and result['steps']==9600
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
