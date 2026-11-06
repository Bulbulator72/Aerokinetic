"""Exercise actual Tk controls for a refined run, project roundtrip and restart."""
import argparse
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import filedialog,messagebox

import aerokinetic
from aerokinetic import Project,RefinementSettings
from aerokinetic.gui import Application
from PIL import Image,ImageGrab


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    errors=[]
    messagebox.showerror=lambda *a,**kw:errors.append(str(a))
    root=tk.Tk()
    root.report_callback_exception=lambda *a:errors.append(str(a))
    app=Application(root)
    p=Project.from_dict({'geometry':{'naca0012':{'n_side':51}},
        'flow':{'reynolds':10000,'alpha_deg':0,'physical_chord_m':.15,'perturbation':0},
        'grid':{'cells_per_chord':16,'upstream_chords':1,'downstream_chords':2,
                'transverse_chords':1,'span_chords':.5,'memory_limit_gib':2},
        'run':{'duration_convective':.1,'average_after':0,'sample_every':2,
               'frame_every':10,'check_every':2,'checkpoint_every':8}})
    # Nondefault hidden advanced value must survive GUI save/open unchanged.
    options=RefinementSettings(coarse_bulk_relaxation_time=1.25)
    app.populate(p,options)
    app.preview()
    assert app.grid_mode.get()=='refined'
    assert 'крупная' in app.resolution_label.cget('text')
    assert len(app.figures['geometry'].axes[0].patches)==1
    saved=args.output/'project.json'
    filedialog.asksaveasfilename=lambda **kw:str(saved)
    app.save_project()
    filedialog.askopenfilename=lambda **kw:str(saved)
    app.new()
    assert app.grid_mode.get()=='uniform'
    app.open_project()
    assert app.grid_mode.get()=='refined'
    assert app.refinement_from_form()==options
    root.update()
    ImageGrab.grab().save(args.output/'geometry.png')
    filedialog.askdirectory=lambda **kw:str(args.output)
    app.start()
    phase='running'
    started=time.monotonic()
    while phase!='done':
        root.update()
        if errors or time.monotonic()-started>240:
            app.stopping.set()
            if app.worker:app.worker.join(timeout=30)
            raise AssertionError((phase,errors or 'GUI timeout'))
        alive=app.worker is not None and app.worker.is_alive()
        if phase=='running' and len(app.records)>=2 and alive:
            app.stop()
            phase='stopping'
        elif phase=='stopping' and not alive and not app.busy:
            summary=json.loads((app.output/'summary.json').read_text())
            assert summary['status']=='stopped' and summary['steps']>0
            filedialog.askdirectory=lambda **kw:str(app.output)
            app.resume()
            phase='resuming'
        elif phase=='resuming' and not alive and not app.busy:
            summary=json.loads((app.output/'summary.json').read_text())
            assert summary['status']=='completed' and summary['steps']==40
            assert summary['fine_steps']==80
            assert app.temporal_report is not None
            for label in app.field_choices:
                app.field_choice.set(label)
                app.draw_frame()
                assert len(app.figures['field'].axes[0].collections)==2
            app.field_choice.set('Давление p−p∞, Па')
            app.draw_frame()
            app.tabs.select(app.tab_frames['field'])
            root.update()
            ImageGrab.grab().save(args.output/'pressure.png')
            filedialog.asksaveasfilename=lambda **kw:str(args.output/'pressure_pa.gif')
            app.export_animation()
            phase='animation'
        elif phase=='animation' and not alive and not app.busy:
            with Image.open(args.output/'pressure_pa.gif') as gif:assert gif.n_frames>=2
            phase='done'
        time.sleep(.02)
    root.destroy()
    result={'status':'passed','installed_package':aerokinetic.__file__,'version':aerokinetic.__version__,
            'refined_project_roundtrip':True,'hidden_bulk_parameter_preserved':True,
            'fine_box_preview':True,'actual_worker_stop_resume':True,
            'steps':40,'fine_steps':80,'field_choices_tested':5,'real_two_level_field':True,
            'dimensional_pressure_gif':True,'callback_errors':errors,'output':str(app.output),
            'linux_tk':'passed','native_windows':'not_run'}
    (args.output/'CHECK.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
