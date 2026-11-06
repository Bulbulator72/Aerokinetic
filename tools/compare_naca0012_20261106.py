"""Experimental comparison with explicit incomplete validation and preserved raw data."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as handle:
        return [{k:float(v) for k,v in row.items()} for row in csv.DictReader(handle)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--available',action='store_true',help='Inspect available finished cases without claiming a complete matrix')
    args=parser.parse_args();work=args.work.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    os.environ.update(MPLCONFIGDIR=str(work/'analysis_matplotlib'),MPLBACKEND='Agg')
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from aerokinetic.experiments import load_series
    from aerokinetic.temporal import read_run
    root=Path(__file__).resolve().parents[1]
    cl_meta,cl_rows=load_series('ohtake_re30000')
    data={
        'cl':{'rows':cl_rows,'value_key':'cl','figure':5,'alpha_bound':.12,'coefficient_bound':.008},
        'cd':{'rows':read_csv(root/'references/ohtake2007_re30000_cd.csv'),'value_key':'cd',
              'figure':6,'alpha_bound':.23,'coefficient_bound':.0031},
        'cm':{'rows':read_csv(root/'references/ohtake2007_re30000_cm_c4.csv'),'value_key':'cm_c4',
              'figure':11,'alpha_bound':.23,'coefficient_bound':.0016}}
    experimental_at_four={}
    for key,entry in data.items():
        rows=entry['rows'];angles=[r['alpha_deg']for r in rows]
        assert all(angles[i]<angles[i+1]for i in range(len(angles)-1))
        assert angles[0]<=4<=angles[-1]
        experimental_at_four[key]=float(np.interp(4,angles,[r[entry['value_key']]for r in rows]))
    with (out/'ohtake_re30000_digitized_all_coefficients.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['coefficient','reynolds','alpha_deg','value','figure',
            'alpha_digitization_half_marker_bound_deg','value_digitization_half_marker_bound','source_doi'])
        writer.writeheader()
        for key,e in data.items():
            for r in e['rows']:
                writer.writerow({'coefficient':'cm_c4'if key=='cm'else key,'reynolds':30000,'alpha_deg':r['alpha_deg'],
                    'value':r[e['value_key']],'figure':e['figure'],'alpha_digitization_half_marker_bound_deg':e['alpha_bound'],
                    'value_digitization_half_marker_bound':e['coefficient_bound'],'source_doi':'10.2322/jjsass.55.439'})
    cases={};comparisons=[]
    for n in (32,64):
        directory=work/'runs'/f'uniform{n}'
        if not (directory/'summary.json').is_file():
            if args.available:continue
            raise RuntimeError('Missing final case summary: '+str(directory))
        summary=json.loads((directory/'summary.json').read_text())
        meta=json.loads((directory/'metadata.json').read_text())
        assert meta['version']=='0.5.0' and meta['flow']['reynolds']==30000 and meta['flow']['alpha_deg']==4
        assert len(meta['contours'])==1 and '0012'in meta['contours'][0]['name']
        assert meta['reference_chord_input_units']==1.
        np.testing.assert_allclose(meta['moment_reference_input_units'],(.25,0),rtol=0,atol=1e-14)
        assert summary['status']in('completed','stopped')
        report,_,rows=read_run(directory,average_after=3.)
        cases[str(n)]={'run':str(directory),'summary':summary,'temporal':report,'grid':meta['grid'],'flow':meta['flow']}
        for key in ('cl','cd','cm'):
            stat=report['series'][key];experiment=experimental_at_four[key];difference=stat['mean']-experiment
            comparisons.append({'cells_per_chord':n,'coefficient':'cm_c4'if key=='cm'else key,'alpha_deg':4.,'reynolds':30000,
                'run_status':summary['status'],'final_t_star':summary['t_star'],'average_t_first':stat['selected_t_first'],
                'average_t_last':stat['selected_t_last'],'samples':stat['samples'],'calculated_mean':stat['mean'],
                'rms_fluctuation_not_mean_error':stat['rms_fluctuation'],'experimental_interpolated':experiment,
                'signed_difference':difference,'cd_relative_difference_percent':100*difference/experiment if key=='cd'else None,
                'experimental_digitization_value_half_bound':data[key]['coefficient_bound'],
                'experimental_digitization_angle_half_bound_deg':data[key]['alpha_bound'],
                'last_minus_first_half_mean':stat['last_minus_first_half_mean'],'block_mean_range':stat['block_mean_range'],
                'validation':'inconclusive'})
    assert cases
    if not args.available:assert len(cases)==2
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(comparisons[0]));writer.writeheader();writer.writerows(comparisons)
    nasa_meta,nasa=load_series('ladson_80w_re5950000')
    with (out/'NASA_TM4074_Re5950000_M015_80W.csv').open('w',newline='',encoding='utf-8')as handle:
        writer=csv.DictWriter(handle,fieldnames=['alpha_deg','cl','cd','cm_c4']);writer.writeheader();writer.writerows(nasa)
    figure=Figure(figsize=(11,10),constrained_layout=True);FigureCanvasAgg(figure)
    colors={'32':'#c76c15','64':'#1264a3'}
    for ax,key in zip(figure.subplots(3,1,sharex=True),('cl','cd','cm')):
        e=data[key];r=e['rows']
        ax.plot([x['alpha_deg']for x in r],[x[e['value_key']]for x in r],'.-',color='0.3',markersize=3,linewidth=.8,
            label=f'Ohtake 2007, Re=30000, рис. {e["figure"]} (оцифровка)')
        ax.errorbar(4,experimental_at_four[key],xerr=e['alpha_bound'],yerr=e['coefficient_bound'],fmt='s',markersize=5,
            color='#237a46',capsize=3,label='Интерполяция при 4°; размер маркера, не погрешность трубы')
        for n,c in cases.items():
            style={'s':120,'marker':'o','facecolors':'none','edgecolors':colors[n]}if n=='32'else{'s':75,'marker':'x','color':colors[n]}
            ax.scatter([4],[c['temporal']['series'][key]['mean']],linewidths=2,**style,
                label=f'AeroKinetic 0.5, {n} яч./c, среднее по выбранному окну')
        ax.set_ylabel({'cl':'Cl','cd':'Cd','cm':'Cm(c/4)'}[key]);ax.grid(alpha=.25);ax.legend(fontsize=7,loc='best')
    ax.set(xlabel='Угол атаки, °',xlim=(-1,10))
    figure.suptitle('Предварительное сравнение NACA0012 · Re=30000\nЧисленная и статистическая сходимость не подтверждены',fontsize=12)
    figure.savefig(out/'comparison.png',dpi=145)
    figure=Figure(figsize=(11,9),constrained_layout=True);FigureCanvasAgg(figure)
    for ax,key in zip(figure.subplots(3,1,sharex=True),('cl','cd','cm')):
        for n,c in cases.items():
            rows=read_csv(Path(c['run'])/'history.csv')
            ax.plot([r['t_star']for r in rows],[r[key]for r in rows],linewidth=.8,color=colors[n],label=n+' яч./c')
        ax.axvspan(3,6,color='0.8',alpha=.3,label='Запрошенное окно осреднения')
        ax.axhline(experimental_at_four[key],color='#237a46',linestyle='--',linewidth=1,label='Эксперимент: интерполяция при 4°')
        ax.set_ylabel(key.capitalize());ax.grid(alpha=.2);ax.legend(fontsize=8)
    ax.set_xlabel('tU/c');figure.suptitle('Исходные отсчёты сил · без сглаживания · переходный процесс может сохраняться',fontsize=11)
    figure.savefig(out/'history.png',dpi=140)
    figure=Figure(figsize=(10,8),constrained_layout=True);FigureCanvasAgg(figure)
    for ax,key in zip(figure.subplots(3,1,sharex=True),('cl','cd','cm_c4')):
        ax.plot([r['alpha_deg']for r in nasa],[r[key]for r in nasa],'o-',markersize=4,linewidth=1,color='#237a46')
        ax.set_ylabel({'cl':'Cl','cd':'Cd','cm_c4':'Cm(c/4)'}[key]);ax.grid(alpha=.25)
    ax.set_xlabel('Угол атаки, °')
    figure.suptitle('NASA TM-4074, таблица IX · Re=5.95 млн · M=0.15 · трип 80-W\nТолько эксперимент: расчёта AeroKinetic при этом Re пока нет',fontsize=11)
    figure.savefig(out/'nasa_high_re_experiment_only.png',dpi=140)
    grid_changes={key:cases['64']['temporal']['series'][key]['mean']-cases['32']['temporal']['series'][key]['mean']
                  for key in ('cl','cd','cm')}if len(cases)==2 else None
    continuation_path=work/'runs/RESUME_CHECK.json'
    continuation=json.loads(continuation_path.read_text())if continuation_path.is_file()else None
    result={'date':'2026-11-06','status':'preliminary_comparison_not_validation','cases':cases,'comparison':comparisons,
        'experimental_at_alpha4':experimental_at_four,'grid64_minus_grid32':grid_changes,
        'low_re_source':cl_meta,'high_re_source':nasa_meta,'high_re_simulation':'not_run; released input contract rejects Re=6e6',
        'digitization_extended_provenance':json.loads((root/'references/ohtake2007_re30000_cd_cm.provenance.json').read_text()),
        'checkpoint_continuation':continuation,
        'limitations':['Only two coarse resolutions; no asymptotic grid convergence or GCI established',
            'Fixed lattice speed changes both dx and dt; fixed bulk relaxation rate changes common-unit bulk damping; this is mixed discretization sensitivity',
            'Domain/span/lattice-Mach convergence not established','Finite averaging window; drift and correlation may remain',
            'Exact tunnel geometry, free transition and turbulence spectrum are not reproduced or completely known',
            'Digitization bounds are not tunnel measurement uncertainty','No data from another CFD code are used as truth'],
        'runtime_changes':False}
    (out/'COMPARISON.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['NACA0012: ЭКСПЕРИМЕНТ И AEROKINETIC 0.5.0','Предварительное сопоставление, 6 ноября 2026 года.','',
        '1. КАКИЕ ПРОДУВКИ НАЙДЕНЫ','',
        'Ohtake, Nakae, Motohashi (2007): Re=10000...100000, измерены Cl, Cd, Cm(c/4).',
        'Для Re=30000 подготовлены Cl — 115 маркеров из рис.5; Cd и Cm — по 201 маркеру из рис.6 и 11.',
        'Источник: https://doi.org/10.2322/jjsass.55.439',
        'Это оцифровка опубликованных графиков, не исходные авторские числовые таблицы.',
        'Полумаркеры: alpha ±0.12° и Cl ±0.008; для Cd/Cm alpha ±0.23°, Cd ±0.0031, Cm ±0.0016.',
        'Это границы оцифровки, не доверительные интервалы и не погрешность трубы.','',
        'NASA Ladson TM-4074 (1988): для ближайшего к задаче высокого Re сохранена таблица IX,',
        'Re=5.95 млн, M=0.15, фиксированный переход 80-W, 17 строк Cl/Cd/Cm.',
        'Источник: https://ntrs.nasa.gov/citations/19880019495',
        'На странице TMR Re округлён до 6 млн. В точной выдаче оставлено значение исходной таблицы.',
        'Дополнительная сверка: в текстовом файле TMR при 8.30° записано Cl=0.8873,',
        'а в исходной таблице PDF — 0.8872. В нашем CSV сохранено значение исходной таблицы.',
        'Это одноэлементный NACA0012 без закрылка. Точность механизации эти данные не подтверждают.','',
        '2. ЧТО РАССЧИТАНО','',
        'Выпущенный AeroKinetic 0.5.0, D3Q27 cumulant, равномерная 3D-сетка, CPU, два потока.',
        'Re=30000, alpha=4°, хорда 0.15 м, момент относительно c/4, конечная толщина задней кромки.',
        'ISA: скорость около 2.92 м/с, физический M около 0.00859; решёточная скорость 0.04.',
        'SGS отключена; начальное возмущение 0.0001, seed=31. Периодический размах 0.5c.',
        'Область: 1.5c перед профилем, 3c после, по 1.5c сверху/снизу.',
        'Два разрешения: 32 и 64 ячейки на хорду. Цель времени tU/c=6, осреднение после 3.',
        'При удвоении разрешения также вдвое уменьшается dt*. Частота объёмной релаксации',
        'остаётся равной 1; соответствующее затухание в общих единицах изменяется.',
        'Это совместная чувствительность к сетке, времени и численному затуханию.',
        'Настройки зафиксированы до просмотра новых результатов; подгонка под эксперимент не проводилась.','',
        '3. ЧИСЛЕННОЕ СОПОСТАВЛЕНИЕ','',
        'Эксперимент при 4°: линейная интерполяция внутри опубликованной серии:']
    for key in ('cl','cd','cm'):lines.append(f"  {key.upper() if key!='cm' else 'Cm(c/4)'} ≈ {experimental_at_four[key]:.6g}")
    if continuation:
        lines+=['',f"Сетка 64: штатная остановка по времени одного запуска при t*={continuation['initial_summary']['t_star']:.6g}.",
            'Затем выполнено продолжение из checkpoint до исходной цели. Параметры случая,',
            'интервал осреднения и уже записанная часть истории сохранены без изменений.']
    for n,c in cases.items():
        s=c['summary'];lines+=['',f"Сетка {n} яч./c: статус {s['status']}, причина {s.get('termination_reason')}, фактическое t*={s['t_star']:.6g}."]
        for r in comparisons:
            if r['cells_per_chord']!=int(n):continue
            lines.append(f"  {r['coefficient']}: расчёт {r['calculated_mean']:.6g}; опыт ≈{r['experimental_interpolated']:.6g}; разность {r['signed_difference']:+.6g}.")
            if r['cd_relative_difference_percent'] is not None:lines.append(f"    Относительная разность Cd: {r['cd_relative_difference_percent']:+.2f}%.")
            lines.append(f"    Окно t*={r['average_t_first']:.6g}...{r['average_t_last']:.6g}, {r['samples']} отсчётов; RMS пульсаций {r['rms_fluctuation_not_mean_error']:.6g}.")
            lines.append(f"    Изменение среднего между половинами окна {r['last_minus_first_half_mean']:+.6g}; размах средних по блокам {r['block_mean_range']:.6g}.")
    if grid_changes:
        lines+=['','Изменение среднего при 32 -> 64 яч./c:']
        for key,value in grid_changes.items():lines.append(f'  {key}: {value:+.6g}')
    lines+=['','4. ЧТО МОЖНО ЗАКЛЮЧИТЬ','',
        'Таблица показывает фактические разности с опытом, но не установленную погрешность программы.',
        'RMS — величина пульсаций, не ошибка среднего. При заметном дрейфе и зависимых отсчётах',
        'из имеющегося окна нельзя обоснованно получить доверительный интервал среднего.',
        'Два разрешения показывают чувствительность к дискретизации; они не доказывают сходимость.',
        'Проверки области, размаха и решёточного Mach в эту серию не входили.',
        'Труба имела свои стенки, торцевые условия, остаточную турбулентность и поправки;',
        'их эквивалентность периодическому численному участку не установлена.',
        'Значит, статус — предварительное сопоставление, аэродинамическая валидация не завершена.','',
        '5. ЧТО С RE ОКОЛО 6 МИЛЛИОНОВ','',
        'Экспериментальные данные подготовлены. Нашего расчёта при таком Re в этой выдаче нет:',
        'выпущенное ядро ограничено Re<=100000 и M<=0.15. Ограничения не снимались.',
        'Нельзя сравнивать низкорейнольдсовый расчёт с NASA при 5.95 млн и называть разницу',
        'погрешностью решателя. Необходим новый проверенный рабочий режим для высокого Re.','',
        '6. ФАЙЛЫ','',
        'comparison.csv — численные результаты и разности с опытом.',
        'ohtake_re30000_digitized_all_coefficients.csv — подготовленные экспериментальные маркеры.',
        'NASA_TM4074_Re5950000_M015_80W.csv — точная выбранная серия NASA.',
        'comparison.png, history.png — сравнение и исходная временная история.',
        'nasa_high_re_experiment_only.png — отдельный график высокого Re, только опыт.',
        'COMPARISON.json — полные условия, временные диагностики и ограничения.',
        'METHOD_AEROKINETIC_RU.txt — отдельное подробное объяснение работающего метода.','']
    (out/'NACA0012_TUNNEL_COMPARISON_RU.txt').write_text('\n'.join(lines),encoding='utf-8-sig')
    print(json.dumps({'status':result['status'],'cases':list(cases),'experimental_at_alpha4':experimental_at_four,
                      'comparison':comparisons,'grid_changes':grid_changes},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
