"""Compare corrected-version raw history with measured NACA0012 coefficients."""
import csv
import hashlib
import json
from pathlib import Path


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='')as handle:
        return [{k:float(v)for k,v in row.items()if v!=''}for row in csv.DictReader(handle)]


def main():
    import numpy as np
    from aerokinetic.temporal import read_run
    root=Path(__file__).resolve().parents[1]
    work=Path('/workspace/aerokinetic_stage1_20261106')
    run=work/'corrected32_t12';output=work/'corrected_comparison';output.mkdir(exist_ok=False)
    metadata=json.loads((run/'metadata.json').read_text());summary=json.loads((run/'summary.json').read_text())
    assert metadata['version']=='0.5.1'and summary['status']=='completed'and summary['steps']==9600
    assert metadata['flow']['reynolds']==30000 and metadata['flow']['alpha_deg']==4
    assert len(metadata['contours'])==1 and metadata['reference_chord_input_units']==1
    np.testing.assert_allclose(metadata['moment_reference_input_units'],(.25,0),rtol=0,atol=1e-14)
    report,_,_=read_run(run,average_after=6)
    baseline=read_csv(work/'baseline32_t24/history.csv')
    baseline=[r for r in baseline if 6<=r['t_star']<12]
    references={'cl':('ohtake2007_fig5_cl_re30000.csv','cl',5,.12,.008),
        'cd':('ohtake2007_re30000_cd.csv','cd',6,.23,.0031),
        'cm':('ohtake2007_re30000_cm_c4.csv','cm_c4',11,.23,.0016)}
    rows=[];provenance={}
    for key,(name,column,figure,alpha_bound,value_bound)in references.items():
        path=root/'references'/name;samples=read_csv(path)
        angles=[r['alpha_deg']for r in samples]
        assert all(a<b for a,b in zip(angles,angles[1:]))and angles[0]<=4<=angles[-1]
        experiment=float(np.interp(4,angles,[r[column]for r in samples]))
        stat=report['series'][key];old_mean=float(np.mean([r[key]for r in baseline]))
        np.testing.assert_allclose(stat['mean'],summary['mean'][key],rtol=0,atol=2e-14)
        rows.append({'coefficient':key,'calculated':stat['mean'],'experimental_interpolated':experiment,
            'signed_difference':stat['mean']-experiment,'cd_difference_percent':100*(stat['mean']/experiment-1)if key=='cd'else None,
            'rms_fluctuation_not_mean_error':stat['rms_fluctuation'],'half_window_mean_change':stat['last_minus_first_half_mean'],
            'previous_050_mean_same_window':old_mean,'corrected_minus_previous':stat['mean']-old_mean,
            'digitization_half_marker_alpha_bound_deg':alpha_bound,'digitization_half_marker_value_bound':value_bound,
            'digitization_is_tunnel_uncertainty':False,'validation':'inconclusive'})
        provenance[name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'figure':figure}
    with(output/'comparison.csv').open('w',newline='')as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    evidence={'version':'0.5.1','reynolds':30000,'alpha_deg':4,'temporal':report,'rows':rows,
        'source_doi':'10.2322/jjsass.55.439','reference_files':provenance,
        'run_file_sha256':{name:hashlib.sha256((run/name).read_bytes()).hexdigest()for name in ('history.csv','summary.json','project.json','metadata.json')},
        'status':'preliminary_comparison_not_validation',
        'limitations':['Coarse N32 wall, old boundary conditions and default bulk damping retained',
            'No grid/span/time/statistical convergence or exact tunnel-condition agreement established',
            'Only a corrected-seed regression: a tiny change in this point does not validate its absolute coefficients',
            'No high-Re or mechanized-profile accuracy claim']}
    (output/'COMPARISON.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(rows,indent=2))


if __name__=='__main__':main()
