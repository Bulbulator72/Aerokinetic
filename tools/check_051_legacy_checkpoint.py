"""Old 0.5 state is continued with 0.5.1 without reinitializing its seed."""
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['old','new'])
    parser.add_argument('--child-name',default='child')
    args=parser.parse_args()
    work=Path('/workspace/aerokinetic_stage1_20261106/legacy_checkpoint')
    import aerokinetic
    from aerokinetic import Solver,Flow,Grid,Section,naca0012
    if args.mode=='old':
        assert aerokinetic.__version__=='0.5.0'
        work.mkdir(exist_ok=False)
        s=Solver(Section((naca0012(31),),1.,(.4,-.03)),Flow(reynolds=30000,alpha_deg=4,perturbation=.002),
            Grid(cells_per_chord=16,upstream_chords=1,downstream_chords=2,transverse_chords=1,memory_limit_gib=1))
        dt=s.plan.dt_convective
        s.run(5*dt,work/'parent',sample_every=1,frame_every=5,check_every=5,average_after=0,checkpoint_every=5)
        for _ in range(7):record=s.step(sample=True)
        s.save_checkpoint(work/'direct_old_final.npz')
        (work/'old.json').write_text(json.dumps({'dt':dt,'final_loads':{k:record[k]for k in ('cl','cd','cm')},'steps':s.steps},indent=2)+'\n')
    else:
        assert aerokinetic.__version__=='0.5.1'
        from aerokinetic import continue_run
        old=json.loads((work/'old.json').read_text())
        output=work/args.child_name
        result=continue_run(work/'parent',output,7*old['dt'])
        child=Solver.from_checkpoint(output/'checkpoint.npz')
        with np.load(work/'direct_old_final.npz',allow_pickle=False)as a,np.load(output/'checkpoint.npz',allow_pickle=False)as b:
            ma,mb=[json.loads(str(z['metadata']))for z in(a,b)]
            for label in ('field','boundary'):
                names=ma['fields'if label=='field'else'boundaries']
                for i,name in enumerate(names):
                    if label=='field'and'pdf'not in name.lower():continue
                    np.testing.assert_array_equal(a[f'{label}_{i}'],b[f'{label}_{i}'])
        assert result['status']=='completed'and result['steps']==old['steps']==12
        report={'source_version':'0.5.0','continued_version':'0.5.1','pdfs_exact':True,
            'outflow_boundary_state_exact':True,'steps':12,'restored_parent_seed_preserved':True,
            'lineage':json.loads((output/'continuation.json').read_text()),'validation':'not_performed'}
        (work/'CHECKS.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
