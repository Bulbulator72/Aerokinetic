"""Actual installed Tk mesh/residual/multithread workflow on a display."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import filedialog,messagebox

from PIL import Image,ImageGrab
import aerokinetic
from aerokinetic import Project,RefinementSettings
from aerokinetic.gui import Application


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    errors=[];messagebox.showerror=lambda *a,**kw:errors.append(str(a))
    messagebox.showinfo=lambda *a,**kw:None
    root=tk.Tk();root.report_callback_exception=lambda *a:errors.append(str(a))
    app=Application(root)
    p=Project.from_dict({'geometry':{'naca0012':{'n_side':51}},
       'flow':{'reynolds':10000,'alpha_deg':5,'physical_chord_m':.15,'perturbation':.001,'seed':12},
       'grid':{'cells_per_chord':16,'upstream_chords':1,'downstream_chords':2,'transverse_chords':1,
               'span_chords':.5,'memory_limit_gib':2},
       'run':{'duration_convective':.12,'sample_every':2,'check_every':2,'frame_every':10,
              'checkpoint_every':4,'average_after':0.}})
    app.populate(p,RefinementSettings())
    app.open_mesh();mesh=app.mesh_window
    mesh.variables['cells_per_chord'].set('32');mesh.build()
    mesh.window.destroy()
    assert int(app.variables['cells_per_chord'].get())==16 # closing a draft never applies it
    app.open_mesh();mesh=app.mesh_window
    mesh.variables['openmp_threads'].set('2');mesh.variables['refine_upstream_chords'].set('.5')
    mesh.apply()
    assert int(app.variables['openmp_threads'].get())==2
    assert app.refinement_from_form().upstream_chords==.5
    assert app.project_from_form().flow.seed==12 and app.project_from_form().flow.perturbation==.001
    mesh.redraw(True);mesh.window.lift();root.update();ImageGrab.grab().save(args.output/'mesh.png')
    app.open_convergence();window=app.residual_window
    window.variables['max_steps'].set('30');window.variables['consecutive_checks'].set('4')
    window.apply()
    assert app.termination_settings.max_steps==30
    saved=args.output/'project.json'
    filedialog.asksaveasfilename=lambda **kw:str(saved);app.save_project()
    original=app.project_from_form()
    app.new();filedialog.askopenfilename=lambda **kw:str(saved);app.open_project()
    assert app.project_from_form().run.termination==original.run.termination
    assert app.project_from_form().flow==original.flow
    filedialog.askdirectory=lambda **kw:str(args.output)
    app.start();phase='running';started=time.monotonic()
    while phase!='done':
        root.update()
        if errors or time.monotonic()-started>300:
            app.stopping.set()
            if app.worker:app.worker.join(timeout=60)
            raise AssertionError((phase,errors or 'GUI timeout'))
        alive=app.worker is not None and app.worker.is_alive()
        if phase=='running' and len(app.records)>=2 and alive:
            assert str(mesh.apply_button.cget('state'))=='disabled'
            assert str(window.widgets[-1].cget('state'))=='disabled'
            app.stop();phase='stopping'
        elif phase=='stopping' and not alive and not app.busy:
            summary=json.loads((app.output/'summary.json').read_text())
            assert summary['status']=='stopped'
            filedialog.askdirectory=lambda **kw:str(app.output)
            app.resume();phase='resuming'
        elif phase=='resuming' and not alive and not app.busy:
            summary=json.loads((app.output/'summary.json').read_text())
            assert summary['status']=='completed' and summary['steps']==30
            assert summary['termination_reason']=='max_steps_reached'
            assert app.residual_records and len(window.table.get_children())==4
            with (app.output/'residuals.csv').open() as handle:rows=list(csv.DictReader(handle))
            assert len(rows)==len({r['step'] for r in rows})==15
            for view in ('Стационарность L2','Стационарность L∞','Интегральный баланс'):
                window.view.set(view);window.refresh()
                assert len(window.figure.axes)==4
            window.view.set('Стационарность L2');window.refresh()
            window.window.lift();root.update();ImageGrab.grab().save(args.output/'residuals.png')
            filedialog.asksaveasfilename=lambda **kw:str(args.output/'pressure.gif')
            app.field_choice.set('Давление p−p∞, Па');app.export_animation();phase='animation'
        elif phase=='animation' and not alive and not app.busy:
            with Image.open(args.output/'pressure.gif') as gif:assert gif.n_frames>=2
            phase='done'
        time.sleep(.02)
    root.destroy()
    result={'status':'passed','version':aerokinetic.__version__,'installed_package':aerokinetic.__file__,
            'mesh_draft_cancel':True,'mesh_apply_and_zoom':True,'threads_selected':2,
            'hidden_flow_settings_preserved':True,'stopping_settings_roundtrip':True,
            'actual_worker_stop_resume':True,'actual_step_limit':30,'four_live_residual_components':True,
            'residual_views':3,'residual_rows':15,'pressure_gif':True,'callback_errors':errors,
            'output':str(app.output),'native_windows':'not_run'}
    (args.output/'CHECK.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
