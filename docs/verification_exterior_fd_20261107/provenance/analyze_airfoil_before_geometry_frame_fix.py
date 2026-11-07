"""Fixed-window domain sensitivity and experimental discrepancy; no fitting."""
import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
from aerokinetic import Project
from aerokinetic.temporal import diagnose_series


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,data):p.write_text(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def read_csv(p):
    with p.open(encoding='utf-8-sig',newline='')as f:return list(csv.DictReader(f))


def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,required=True)
    p.add_argument('--runs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    tunnel=a.project_root/'docs/verification_naca0012_20261106/comparison/ohtake_re30000_digitized_all_coefficients.csv'
    experiment=read_csv(tunnel);exp={}
    for target,column in(('cl','cl'),('cd','cd'),('cm','cm_c4')):
        r=[r for r in experiment if r['coefficient']==column and float(r['reynolds'])==30000]
        x=np.array([float(r['alpha_deg'])for r in r]);y=np.array([float(r['value'])for r in r])
        assert np.all(np.diff(x)>0)and x[0]<=4<=x[-1]
        i=int(np.searchsorted(x,4));assert 0<i<len(x)
        exp[target]={'interpolated':float(np.interp(4,x,y)),
          'bracket_alpha_deg':[float(x[i-1]),float(x[i])],
          'bracket_values':[float(y[i-1]),float(y[i])],
          'digitization_value_half_marker_bound':max(float(r[j]['value_digitization_half_marker_bound'])for j in(i-1,i)),
          'digitization_angle_half_marker_bound_deg':max(float(r[j]['alpha_digitization_half_marker_bound_deg'])for j in(i-1,i)),
          'source_doi':'10.2322/jjsass.55.439'}
    histories={};stats={};sources={};comparison=[];guards={}
    for domain,legacy_name in((3,'corrected32_t12'),(6,'downstream6')):
        root=a.runs/f'airfoil_down{domain}';result=json.loads((root/'CHECKS.json').read_text())
        if not result['completion_gate_passed']or result['steps']!=9600:
            raise ValueError('Fixed full candidate window is unavailable; no partial-window statistics')
        project=Project.load(root/'input_project.json')
        assert project.flow.reynolds==30000 and project.flow.alpha_deg==4 and project.grid.cells_per_chord==32
        if not np.allclose(np.array(project.section.moment_reference)/project.section.chord,[.25,0],rtol=0,atol=1e-14):
            raise ValueError('Experimental Cm is about c/4; input moment reference does not match')
        old=a.project_root/'docs/verification_stage1_20261106'/legacy_name
        old_metadata=json.loads((old/'metadata.json').read_text())
        old_summary=json.loads((old/'summary.json').read_text())
        assert old_summary['status']=='completed'and old_summary['t_star']>=12
        assert old_metadata['flow']==asdict(project.flow)
        for k in('cells_per_chord','upstream_chords','downstream_chords','transverse_chords','span_chords','target'):
            assert old_metadata['grid'][k]==getattr(project.grid,k)
        for k in('shape','origin','dx','span','viscosity','dt_convective'):
            assert np.array_equal(old_metadata['plan'][k],result['identity']['core_plan'][k]),k
        contours=project.section.normalized(project.flow.alpha_deg)
        assert len(contours)==len(old_metadata['contours'])
        assert all(np.array_equal(c.xy,np.asarray(e['xy']))for c,e in zip(contours,old_metadata['contours']))
        guards[f'domain{domain}_legacy_geometry_physics_match']=True
        for family,path in(('exterior_fd',root),('legacy',old)):
            name=f'{family}_down{domain}';history=path/'history.csv';raw=read_csv(history)
            times=np.array([float(r['t_star'])for r in raw]);keep=times<=12
            times=times[keep];assert np.all(np.diff(times)>0)
            histories[name]={'t_star':times,'loads':{k:np.array([float(r[k])for r in raw])[keep]for k in('cl','cd','cm')}}
            stats[name]={k:diagnose_series(times,v,average_after=6,block_count=8)for k,v in histories[name]['loads'].items()}
            assert all(v['samples']==600 and v['selected_t_last']>11.99 for v in stats[name].values())
            sources[name]={'history':str(history),'sha256':sha(history),'completed_target_t_star':12}
            for k,v in stats[name].items():
                comparison.append({'case':name,'coefficient':k,'N':32,'Re':30000,'alpha_deg':4,
                    'window':'6..12','samples':v['samples'],'mean':v['mean'],'rms_about_mean':v['rms_fluctuation'],
                    'last_minus_first_half_mean':v['last_minus_first_half_mean'],'block_mean_range':v['block_mean_range'],
                    'experiment':exp[k]['interpolated'],'signed_difference':v['mean']-exp[k]['interpolated'],
                    'validation':'inconclusive','reason':'N32 and fixed short window; grid/span/Mach and statistical convergence not certified'})
    sensitivity={}
    for family in('exterior_fd','legacy'):
        sensitivity[family]={k:{'mean_down6_minus_down3':stats[f'{family}_down6'][k]['mean']-stats[f'{family}_down3'][k]['mean'],
            'rms_down6_minus_down3':stats[f'{family}_down6'][k]['rms_fluctuation']-stats[f'{family}_down3'][k]['rms_fluctuation']}for k in('cl','cd','cm')}
    with(a.output/'comparison.csv').open('w',newline='')as f:
        w=csv.DictWriter(f,fieldnames=comparison[0].keys());w.writeheader();w.writerows(comparison)
    write(a.output/'ANALYSIS.json',{'experiment':exp,'experiment_csv_sha256':sha(tunnel),'sources':sources,
        'series':stats,'exit_sensitivity':sensitivity,'input_checks':guards,
        'validation':'inconclusive','confidence_intervals':None,
        'limits':['Same-code boundary/domain diagnostic; no other CFD solver used.',
                  'Digitization marker bounds are not total tunnel uncertainty.',
                  'Fixed6..12 window is descriptive; its stationarity is not presumed.',
                  'N32 does not demonstrate a resolved boundary layer; no high-Re claim.']})
    # Standard scientific plotting; SVG contains curves/text, no embedded fields.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'svg.fonttype':'none','svg.hashsalt':'aerokinetic-exterior-20261107','font.size':10})
    fig,axes=plt.subplots(3,1,figsize=(11,8),sharex=True)
    colors={'exterior_fd_down3':'#1768ac','exterior_fd_down6':'#17845e','legacy_down3':'#c76d2d','legacy_down6':'#8c6ab3'}
    for ax,k in zip(axes,('cl','cd','cm')):
        for name,h in histories.items():ax.plot(h['t_star'],h['loads'][k],label=name,color=colors[name],lw=1.0,alpha=.85)
        ax.axhline(exp[k]['interpolated'],color='black',ls='--',lw=1,label='Ohtake2007 interpolated')
        ax.axvspan(6,12,color='grey',alpha=.08);ax.set_ylabel(k.upper());ax.grid(alpha=.2)
    axes[0].legend(ncol=3,fontsize=8);axes[-1].set_xlabel('t U / c');axes[-1].set_xlim(0,12)
    fig.suptitle('NACA0012, Re30000, alpha4deg, N32: diagnostic histories; accuracy unvalidated')
    fig.tight_layout();fig.savefig(a.output/'histories.svg',metadata={'Date':None});plt.close(fig)
    fig,axes=plt.subplots(3,1,figsize=(11,8),sharex=True)
    for ax,k in zip(axes,('cl','cd','cm')):
        for name,h in histories.items():
            selected=(h['t_star']>=6)&(h['t_star']<=12)
            ax.plot(h['t_star'][selected],h['loads'][k][selected],label=name,color=colors[name],lw=1.0)
        ax.axhline(exp[k]['interpolated'],color='black',ls='--',lw=1,label='Ohtake2007 interpolated')
        ax.set_ylabel(k.upper());ax.grid(alpha=.2)
    axes[0].legend(ncol=3,fontsize=8);axes[-1].set_xlabel('t U / c');axes[-1].set_xlim(6,12)
    fig.suptitle('Preregistered6..12 window; RMS and drift are descriptive, accuracy unvalidated')
    fig.tight_layout();fig.savefig(a.output/'window6_12.svg',metadata={'Date':None});plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(12,4))
    names=list(histories)
    for ax,k in zip(axes,('cl','cd','cm')):
        values=[stats[n][k]['mean']for n in names]
        ax.bar(np.arange(4),values,color=[colors[n]for n in names]);ax.axhline(exp[k]['interpolated'],color='black',ls='--',label='Tunnel interpolation')
        ax.set_xticks(np.arange(4),['FD3c','legacy3c','FD6c','legacy6c'],rotation=25)
        ax.set_title(k.upper()+' mean6..12');ax.grid(axis='y',alpha=.2)
    axes[0].legend(fontsize=8);fig.suptitle('Descriptive N32 means; no confidence or grid-convergence claim')
    fig.tight_layout();fig.savefig(a.output/'means.svg',metadata={'Date':None});plt.close(fig)
    write(a.output/'CHECKS.json',{'matched_physical_inputs':True,'complete_fixed_windows':True,
        'means_match_run_checkpoints':all(abs(stats[f'exterior_fd_down{d}'][k]['mean']-json.loads((a.runs/f'airfoil_down{d}/CHECKS.json').read_text())['window6_12']['mean'][k])<2e-14 for d in(3,6)for k in('cl','cd','cm')),
        'experimental_validation':'inconclusive','grid_convergence':'not_run','statistical_convergence':'not_certified',
        'source_sha256':sha(Path(__file__))})
    print(json.dumps({'exit_sensitivity':sensitivity,'experiment':exp},indent=2))


if __name__=='__main__':main()
