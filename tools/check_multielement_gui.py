"""Actual GUI imports/plots for generated multi-element cases; preserve source runs."""
import argparse
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
from PIL import ImageGrab
import aerokinetic
from aerokinetic.gui import Application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checks', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--results', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve();out.mkdir(parents=True, exist_ok=False)
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    errors = []
    messagebox.showerror = lambda *a, **k: errors.append(str(a))
    messagebox.showinfo = lambda *a, **k: None
    messagebox.askyesnocancel = lambda *a, **k: False
    root = tk.Tk();root.report_callback_exception = lambda *a: errors.append(str(a))
    app = Application(root);root.geometry('1280x800')
    cases = {}
    def shot(name):
        root.update();root.update()
        box = (root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
        ImageGrab.grab().crop(box).save(out/name)
    try:
        for kind in ('slotted','double','fowler'):
            p, options = app.read_project_file(args.checks/(kind+'_project.json'))
            app.populate(p,options)
            data = json.loads((args.checks/'inputs'/(kind+'.json')).read_text())
            paths = [str(args.checks/'inputs'/e['dat']) for e in data['elements']]
            filedialog.askopenfilenames = lambda **k: paths
            app.import_dat()
            actual = app.project_from_form()
            assert len(actual.section.elements) == len(p.section.elements)
            for a, b in zip(actual.section.elements,p.section.elements):
                np.testing.assert_array_equal(a.xy,b.xy)
            assert actual.section.chord == p.section.chord
            assert actual.flow == p.flow and actual.grid == p.grid and actual.run == p.run
            app.show_section('mesh');app.redraw_mesh(True)
            shot(kind+'_mesh.png')
            cases[kind] = {'imported_elements':len(paths),'coordinates_and_settings':'passed'}
            if args.results:
                source = args.checks/(kind+'_run')
                original = {n:(source/n).read_bytes() for n in ('project.json','history.csv','residuals.csv','summary.json')}
                filedialog.askdirectory = lambda **k: str(source)
                app.open_output()
                assert app.records and app.residual_records and app.frame_files
                app.show_section('results','field');app.field_choice.set('Давление Cp');app.draw_frame()
                shot(kind+'_field.png')
                assert len(app.figures['field'].axes[0].patches) >= len(paths)
                app.open_convergence();root.update()
                assert len(app.residual_window.table.get_children()) == 4
                app.residual_window.window.destroy()
                assert all((source/n).read_bytes()==raw for n,raw in original.items())
                cases[kind]['actual_results_and_four_residuals'] = 'passed'
        app.variables['reynolds'].set('6000000');app.preview();root.update()
        assert app.entry_widgets['reynolds'].instate(['invalid'])
        assert app.variables['reynolds'].get() == '6000000'
        assert not errors, errors
    finally:
        root.destroy()
    result = {'status':'passed','version':aerokinetic.__version__,'cases':cases,
              're6m':'GUI rejects unsupported input and preserves it for correction',
              'source_run_files_unchanged':bool(args.results),'callback_errors':errors,'native_windows':'not_run'}
    (out/'CHECK.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    main()
