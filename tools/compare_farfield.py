"""Compare matched NACA0012 boundary cases with measured Cl/Cd/Cm, without fitting."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-down3','legacy-down6','characteristic-down3','characteristic-down6'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    from aerokinetic import Project
    from aerokinetic.temporal import read_run
    root=Path(__file__).resolve().parents[1]
    references={}
    for key,name,column in (('cl','ohtake2007_fig5_cl_re30000.csv','cl'),
                            ('cd','ohtake2007_re30000_cd.csv','cd'),
                            ('cm','ohtake2007_re30000_cm_c4.csv','cm_c4')):
        path=root/'references'/name
        with path.open(encoding='utf-8-sig',newline='')as handle:rows=list(csv.DictReader(handle))
        a=np.array([float(r['alpha_deg'])for r in rows]);v=np.array([float(r[column])for r in rows])
        assert np.all(np.diff(a)>0)and a[0]<=4<=a[-1]
        references[key]={'value':float(np.interp(4,a,v)),'file':name,'sha256':digest(path),
            'method':'linear interpolation within measured/digitized angles; no extrapolation',
            'doi':'10.2322/jjsass.55.439'}
    reports={};rows=[];projects={};series={}
    for name in ('legacy_down3','legacy_down6','characteristic_down3','characteristic_down6'):
        directory=getattr(args,name);p=Project.load(directory/'project.json');projects[name]=p
        metadata=json.loads((directory/'metadata.json').read_text())
        summary=json.loads((directory/'summary.json').read_text())
        assert p.flow.reynolds==30000 and p.flow.alpha_deg==4 and p.grid.cells_per_chord==32
        assert p.flow.physical_chord_m==.15 and p.run.average_after==6
        assert p.grid.farfield==('legacy'if name.startswith('legacy')else'characteristic_transport_v1')
        assert p.grid.downstream_chords==(3 if name.endswith('3')else 6)
        np.testing.assert_array_equal(p.section.moment_reference,(.25,0))
        if summary['status']!='completed' or summary['steps']!=9600:
            # Keep the fixed 6..12 acceptance window. A partial window from a
            # failed run must never become an apparently validated average.
            with(directory/'history.csv').open(newline='')as handle:history=list(csv.DictReader(handle))
            series[name]=(np.array([float(r['t_star'])for r in history]),
                {key:np.array([float(r[key])for r in history])for key in ('cl','cd','cm')})
            reports[name]={'runtime_version':metadata['version'],'case_fingerprint':p.case_fingerprint,
                'status':summary['status'],'steps':summary['steps'],'t_star':summary['t_star'],
                'duration_gate':'failed','mean':None,'partial_window_averaging':'withheld',
                'run_sha256':{file:digest(directory/file)for file in ('project.json','metadata.json','history.csv','summary.json','residuals.csv')}}
            for key in ('cl','cd','cm'):
                rows.append({'case':name,'coefficient':key,'calculated':None,
                    'experiment':references[key]['value'],'signed_difference':None,
                    'rms_fluctuation_not_mean_error':None,'last_minus_first_half_mean':None,
                    'validation':'failed duration gate; comparison withheld'})
            continue
        report,t,values=read_run(directory,average_after=6);series[name]=(t,values)
        for key in ('cl','cd','cm'):
            stat=report['series'][key];experiment=references[key]['value']
            np.testing.assert_allclose(stat['mean'],summary['mean'][key],rtol=0,atol=2e-14)
            rows.append({'case':name,'coefficient':key,'calculated':stat['mean'],
                'experiment':experiment,'signed_difference':stat['mean']-experiment,
                'rms_fluctuation_not_mean_error':stat['rms_fluctuation'],
                'last_minus_first_half_mean':stat['last_minus_first_half_mean'],
                'validation':'inconclusive'})
        reports[name]={'runtime_version':metadata['version'],'case_fingerprint':p.case_fingerprint,
            'status':'completed','duration_gate':'passed',
            'temporal':report,'mean':summary['mean'],'rms_fluctuation':summary['rms_fluctuation'],
            'diagnostics':summary.get('diagnostics'),'wall_clock_seconds':summary['wall_clock_seconds'],
            'run_sha256':{file:digest(directory/file)for file in ('project.json','metadata.json','history.csv','summary.json','residuals.csv')}}
    # Check the controlled variables, allowing only the declared boundary/domain change.
    from dataclasses import replace
    first=projects['legacy_down3']
    for p in projects.values():
        assert p.flow==first.flow
        assert replace(p.grid,farfield='legacy',downstream_chords=3)==first.grid
        assert p.run==first.run
        for a,b in zip(p.section.elements,first.section.elements):np.testing.assert_array_equal(a.xy,b.xy)
    with(args.output/'comparison.csv').open('w',newline='')as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    result={'scope':'controlled boundary/domain experiment; coarse N32, no grid/span/Mach/statistical convergence',
        'all_duration_gates_passed':all(r['duration_gate']=='passed'for r in reports.values()),
        'failed_case_averages_withheld':True,
        'experimental_validation':'inconclusive','references':references,'cases':reports,
        'note':'legacy_down3 is the preserved0.5.1 corrected-seed run; other cases are installed0.5.2. Legacy numerical operators are unchanged; exact release restart is checked separately.'}
    (args.output/'COMPARISON.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    figure=Figure(figsize=(11,9));FigureCanvasAgg(figure)
    for i,key in enumerate(('cl','cd','cm'),1):
        ax=figure.add_subplot(3,1,i)
        for name,(t,values)in series.items():ax.plot(t,values[key],label=name,lw=.9)
        ax.axhline(references[key]['value'],color='black',ls='--',label='Ohtake2007 interpolated')
        ax.set_ylabel(key);ax.set_xlim(6,12);ax.grid(alpha=.2)
        if i==1:ax.legend(fontsize=8,ncol=2)
    ax.set_xlabel('t U/c; fixed comparison window6..12')
    figure.tight_layout();figure.savefig(args.output/'loads.png',dpi=150)
    for ax in figure.axes:
        ax.set_xlim(0,12);ax.axvspan(6,12,color='grey',alpha=.08)
    figure.savefig(args.output/'loads_full_history.png',dpi=150)
    print(json.dumps(rows,indent=2),flush=True)


if __name__=='__main__':main()
