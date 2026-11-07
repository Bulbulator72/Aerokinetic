"""Descriptive, unfiltered time/domain/acoustic diagnostics; no convergence certificate."""
import csv
import argparse
import json
import os
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    work=Path('/workspace/aerokinetic_stage1_20261106')
    out=args.output or work/'analysis';out.mkdir(exist_ok=False)
    os.environ.update(MPLBACKEND='Agg',MPLCONFIGDIR=str(work/'mpl_analysis'),
                      XDG_CACHE_HOME=str(work/'cache_analysis'))
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from aerokinetic.temporal import diagnose_series
    def read(path):
        with path.open()as f:return [{k:float(v)for k,v in r.items()if v!=''}for r in csv.DictReader(f)]
    old=Path('/workspace/aerokinetic_validation_20261106/runs/uniform32')
    original=read(old/'history.csv');continued=read(work/'baseline32_t24/history.csv')
    datasets={'baseline32':original+continued,'transverse3':read(work/'transverse3/history.csv'),
              'downstream6':read(work/'downstream6/history.csv')}
    reports={};rows=[]
    def describe(name,data,lo,hi):
        selected=[r for r in data if lo<=r['t_star']<hi]
        t=np.array([r['t_star']for r in selected]);stats={}
        for key in ('cl','cd','cm'):
            v=np.array([r[key]for r in selected]);d=diagnose_series(t,v,lo)
            spectrum=abs(np.fft.rfft((v-v.mean())*np.hanning(len(v))))**2
            frequency=np.fft.rfftfreq(len(v),float(np.median(np.diff(t))))
            keep=(frequency>=.1)&(frequency<=40)
            peak=float(frequency[keep][np.argmax(spectrum[keep])])
            stats[key]={**d,'hann_peak_frequency_per_t_star':peak,'frequency_bin_width':float(frequency[1]-frequency[0])}
            rows.append({'case':name,'window_start':lo,'window_end':hi,'coefficient':key,'mean':d['mean'],
                'rms':d['rms_fluctuation'],'half_window_mean_change':d['last_minus_first_half_mean'],
                'dominant_frequency_per_t_star':peak,'samples':len(v)})
        return stats
    for lo,hi in ((3,6),(6,12),(12,18),(18,24)):
        reports[f'baseline_{lo}_{hi}']=describe('baseline32',datasets['baseline32'],lo,hi)
    for name in ('transverse3','downstream6'):
        reports[name]=describe(name,datasets[name],6,12)
    with(out/'statistics.csv').open('w',newline='')as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    reports['interpretation']=['Independent budgets test implementation, not tunnel accuracy',
        'Unequal original/new sample cadence is analyzed in separate uniform windows',
        'Frequency peaks do not establish an acoustic/physical cause; no coherence or mode fit inferred',
        'Domain comparison is at the same coarse wall resolution; no grid convergence',
        'Transverse-domain change also alters the small original seeding wavelength',
        'Airfoil diagnostic data were produced with unmodified installed 0.5.0; they do not validate the corrected 0.5.1 initializer']
    (out/'DIAGNOSTICS.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
    fig=Figure(figsize=(11,8),constrained_layout=True);FigureCanvasAgg(fig)
    colors={'baseline32':'#a75410','transverse3':'#14749a','downstream6':'#793f9f'}
    for ax,key in zip(fig.subplots(3,1,sharex=True),('cl','cd','cm')):
        for name,data in datasets.items():
            visible=[r for r in data if 6<=r['t_star']<=12]
            ax.plot([r['t_star']for r in visible],[r[key]for r in visible],lw=.7,color=colors[name],label=name)
        ax.set(xlim=(6,12),ylabel=key.capitalize());ax.grid(alpha=.2);ax.legend(fontsize=8)
    ax.set_xlabel('tU/c');fig.suptitle('Re=30000, α=4°, 32 яч./c · чувствительность к внешней области\nИсходные отсчёты; численная и статистическая сходимость не подтверждены')
    fig.savefig(out/'domain_histories.png',dpi=140)
    fig=Figure(figsize=(11,7),constrained_layout=True);FigureCanvasAgg(fig)
    data=datasets['baseline32']
    for ax,key in zip(fig.subplots(3,1,sharex=True),('cl','cd','cm')):
        ax.plot([r['t_star']for r in data],[r[key]for r in data],lw=.7)
        ax.axvline(6,color='k',ls='--',lw=.7,label='Начало продолжения из checkpoint')
        ax.set_ylabel(key.capitalize());ax.grid(alpha=.2);ax.legend(fontsize=8)
    ax.set_xlabel('tU/c');fig.suptitle('Неизменённый baseline 0.5.0 до tU/c=24 · без фильтрации')
    fig.savefig(out/'long_history.png',dpi=140)
    fig=Figure(figsize=(10,5),constrained_layout=True);FigureCanvasAgg(fig)
    ax=fig.subplots()
    for name in ('ubb','extrapolation'):
        a=read(work/'acoustics'/f'{name}.csv')
        ax.plot([r['step']for r in a],[r['r_minus']for r in a],label=name+' · входящая характеристика')
    ax.set(xlabel='Решёточный шаг',ylabel='δuy − cs·δρ',title='Линейная акустическая волна · сигнал после отражения у датчика')
    ax.grid(alpha=.25);ax.legend()
    fig.savefig(out/'acoustic_probe.png',dpi=140)
    print(json.dumps({'statistics':rows,'output':str(out)},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
