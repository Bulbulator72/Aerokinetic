"""Read actual saved fields; plot gap cells without smoothing or altering values."""
import csv
import json
import os
from pathlib import Path

root = Path(__file__).resolve().parents[2]
work = root.parent/'aerokinetic_mechanization_20261102'
checks = work/'check_v2'
os.environ.update(MPLCONFIGDIR=str(work/'analysis_mpl'), MPLBACKEND='Agg')
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from aerokinetic.refined_visualization import draw_frame

results = json.loads((checks/'CHECK.json').read_text())
field_checks = {}
for kind in ('slotted','double','fowler'):
    assert results['cases'][kind]['summary']['status'] == 'completed'
    frame_path = sorted((checks/(kind+'_run')/'frames').glob('frame_*.npz'))[-1]
    probes = []
    with np.load(frame_path, allow_pickle=False) as frame:
        x = (frame['fine_x_edges'][:-1]+frame['fine_x_edges'][1:])/2
        y = (frame['fine_y_edges'][:-1]+frame['fine_y_edges'][1:])/2
        xx, yy = np.meshgrid(x,y,indexing='ij')
        dx = float(frame['fine_dx'])
        for gap in results['cases'][kind]['geometry_32_64']['pairs']:
            if not gap['direct_fluid_gap']:continue
            a,b = np.array(gap['nearest_points_normalized'])
            distance = np.linalg.norm(b-a);unit = (b-a)/distance
            along = (xx-a[0])*unit[0]+(yy-a[1])*unit[1]
            across = np.abs((xx-a[0])*unit[1]-(yy-a[1])*unit[0])
            chosen = (along>0)&(along<distance)&(across<=.75*dx)&(~frame['fine_mask'])
            count = int(np.count_nonzero(chosen))
            assert count > 0, (kind,gap['elements'])
            assert np.isfinite(frame['fine_speed'][chosen]).all() and np.isfinite(frame['fine_cp'][chosen]).all()
            probes.append({'elements':gap['elements'],'selected_fluid_centres_near_gap_segment':count,
                'gap_over_dx':distance/dx,'finite_speed_cp':True,
                'speed_over_freestream_range':[float(frame['fine_speed'][chosen].min()),float(frame['fine_speed'][chosen].max())]})
        field_checks[kind] = {'file':str(frame_path),'actual_t_star':float(frame['t_star']),
                              'probes':probes,'note':'Finite probe values do not establish resolved gap flow'}
        if kind == 'slotted':
            limit = max(float(np.nanmax(np.abs(frame[level+'_speed']))) for level in ('coarse','fine'))
    if kind == 'slotted':
        fig = Figure(figsize=(11,5),constrained_layout=True);FigureCanvasAgg(fig)
        ax = draw_frame(fig,frame_path,'speed',limit=limit)
        ax.set(xlim=(-.35,1.65),ylim=(-.55,.35))
        fig.suptitle('NACA0012 + однощелевой закрылок 20° · Re=10⁴ · α=4°\nСтартовый прогон; 64 ячейки на полную хорду; точность не установлена',fontsize=11)
        fig.savefig(checks/'speed_zoom.png',dpi=150)
        fig = Figure(figsize=(7,6),constrained_layout=True);FigureCanvasAgg(fig)
        ax = draw_frame(fig,frame_path,'speed',limit=limit)
        a,b = np.array(results['cases'][kind]['geometry_32_64']['pairs'][0]['nearest_points_normalized'])
        midpoint = (a+b)/2
        ax.set(xlim=(midpoint[0]-.10,midpoint[0]+.10),ylim=(midpoint[1]-.085,midpoint[1]+.085))
        for v in x:ax.axvline(v-dx/2,color='0.5',alpha=.4,linewidth=.4)
        for v in y:ax.axhline(v-dx/2,color='0.5',alpha=.4,linewidth=.4)
        ax.plot([a[0],b[0]],[a[1],b[1]],'w--',linewidth=1.4)
        fig.suptitle(f"Реальная сетка в щели · g/Δx={np.linalg.norm(b-a)/dx:.2f}\nБез сглаживания; наличие щели не подтверждает точность струи",fontsize=11)
        fig.savefig(checks/'gap_zoom.png',dpi=170)

with (checks/'slotted_run/history.csv').open() as handle:
    rows = list(csv.DictReader(handle))
fig = Figure(figsize=(10,7),constrained_layout=True);FigureCanvasAgg(fig)
for ax,key in zip(fig.subplots(3,1,sharex=True),('cl','cd','cm')):
    ax.plot([float(r['t_star']) for r in rows],[float(r[key]) for r in rows],linewidth=1)
    ax.set_ylabel(key.capitalize());ax.grid(alpha=.25)
ax.set_xlabel('tU/c')
fig.suptitle('Стартовый прогон с закрылком · Re=10⁴ · сходимость средних не установлена',fontsize=11)
fig.savefig(checks/'history.png',dpi=145)
(checks/'GAP_FIELDS_CHECK.json').write_text(json.dumps(field_checks,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(field_checks,ensure_ascii=False,indent=2))
