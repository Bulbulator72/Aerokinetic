"""Installed desktop acceptance: projects, real worker, restart and read-only plots."""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
from PIL import Image, ImageGrab
import aerokinetic
from aerokinetic import Project, RefinementSettings
from aerokinetic.gui import Application
from aerokinetic.gui_style import FormInputError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve();out.mkdir(parents=True, exist_ok=False)
    config = out/'pystencils.json'
    config.write_text(json.dumps({'cache':{'object_cache':str(out/'jit'), 'clear_cache_on_start':False}}))
    os.environ['PYSTENCILS_CONFIG'] = str(config)
    os.environ['PYSTENCILS_CACHE_DIR'] = str(out/'symbolic')
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert aerokinetic.__version__ == '0.5.0'
    errors = []; attempts = []
    messagebox.showerror = lambda *a, **k: errors.append(str(a))
    messagebox.showinfo = lambda *a, **k: None
    messagebox.askyesnocancel = lambda *a, **k: False
    def denied(*a, **k):
        attempts.append('Python network')
        raise AssertionError('Network disabled for desktop acceptance')
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = denied
    socket.getaddrinfo = socket.gethostbyname = socket.gethostbyname_ex = denied
    root = tk.Tk();root.report_callback_exception = lambda *a: errors.append(str(a))
    app = Application(root);root.geometry('1280x720')

    def update():
        root.update()
        assert not errors, errors

    def shot(name):
        update();update()
        box = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height())
        ImageGrab.grab().crop(box).save(out / name)

    def values(actual, expected):
        assert actual.flow == expected.flow and actual.grid == expected.grid and actual.run == expected.run
        assert actual.title == expected.title and actual.section.chord == expected.section.chord
        np.testing.assert_array_equal(actual.section.moment_reference, expected.section.moment_reference)
        for a, b in zip(actual.section.elements, expected.section.elements):
            np.testing.assert_array_equal(a.xy, b.xy)
        assert len(actual.section.elements) == len(expected.section.elements)

    # Opening an existing 0.4 result does not mutate it.
    old = Path(__file__).resolve().parents[2] / 'aerokinetic_portable_dev/linux_build_v6/checks/refined_direct'
    if old.is_dir():
        before = {n: (old/n).read_bytes() for n in ('project.json', 'history.csv', 'residuals.csv', 'summary.json')}
        filedialog.askdirectory = lambda **k: str(old)
        app.open_output();update()
        expected, options = app.read_project_file(old/'project.json')
        values(app.project_from_form(), expected)
        assert app.refinement_from_form() == options
        assert all((old/n).read_bytes() == raw for n, raw in before.items())

    p = Project.from_dict({'title': 'Проверка интерфейса — NACA0012',
        'geometry': {'naca0012': {'n_side': 51}},
        'flow': {'reynolds': 10000, 'alpha_deg': 5, 'physical_chord_m': .15, 'seed': 12, 'perturbation': .001},
        'grid': {'cells_per_chord': 16, 'upstream_chords': 1, 'downstream_chords': 2,
                 'transverse_chords': 1, 'span_chords': .5, 'memory_limit_gib': 2},
        'run': {'duration_convective': .15, 'sample_every': 2, 'check_every': 2, 'frame_every': 5,
                'checkpoint_every': 4, 'average_after': 0}})
    options = RefinementSettings()
    app.populate(p, options);app.mark_clean()
    values(app.project_from_form(), p)
    for key, raw in (('reynolds', ''), ('alpha_deg', 'nan'), ('physical_chord_m', 'inf'), ('openmp_threads', '1.5')):
        original = app.variables[key].get();app.variables[key].set(raw)
        try:
            app.project_from_form()
            raise AssertionError('Invalid input accepted: '+key)
        except FormInputError as error:
            assert error.key == key
            app.error(error)
        update()
        assert app.variables[key].get() == raw and app.entry_widgets[key].instate(['invalid'])
        app.variables[key].set(original)
    app.variables['reynolds'].set('1e4');app.variables['physical_chord_m'].set('0,15')
    app.variables['openmp_threads'].set('2e0')
    assert app.project_from_form().grid.openmp_threads == 2
    values(app.project_from_form(), replace(p, grid=replace(p.grid, openmp_threads=2)))
    app.refresh_context();assert app.dirty
    signature = app.draft_signature()
    messagebox.askyesnocancel = lambda *a, **k: None
    app.new();assert app.draft_signature() == signature
    messagebox.askyesnocancel = lambda *a, **k: False
    for key in ('geometry', 'mesh', 'calculation', 'results', 'geometry'):
        app.show_section(key);update()
        assert app.draft_signature() == signature
    assert len(app.panes.panes()) == 2
    app.show_section('results');assert len(app.panes.panes()) == 1
    app.show_section('calculation');update()
    for button in (app.start_button, app.resume_button, app.stop_button):
        assert button.winfo_rooty()+button.winfo_height() <= root.winfo_rooty()+root.winfo_height()

    app.open_mesh();mesh = app.mesh_window
    mesh.variables['cells_per_chord'].set('32');mesh.build();mesh.window.destroy()
    assert app.integer('cells_per_chord') == 16
    app.open_mesh();mesh = app.mesh_window
    mesh.variables['refine_upstream_chords'].set('.5');mesh.apply()
    assert app.refinement_from_form().upstream_chords == .5
    assert app.project_from_form().flow.seed == 12 and app.project_from_form().flow.perturbation == .001
    mesh.window.destroy()
    app.show_section('mesh');app.redraw_mesh(True);shot('mesh.png')
    app.open_convergence();window = app.residual_window
    window.variables['max_steps'].set('30');window.variables['consecutive_checks'].set('4');window.apply()
    assert app.termination_settings.max_steps == 30
    window.window.withdraw()
    saved = out/'проект с пробелами.json'
    filedialog.asksaveasfilename = lambda **k: str(saved)
    app.save_project();assert not app.dirty
    expected = app.project_from_form();expected_refinement = app.refinement_from_form()
    app.new();assert app.output is None and not app.frame_files and not app.records and not app.dirty
    filedialog.askopenfilename = lambda **k: str(saved)
    app.open_project();values(app.project_from_form(), expected)
    assert not app.dirty and app.refinement_from_form() == expected_refinement
    app.variables['alpha_deg'].set('6')
    root.focus_force();root.event_generate('<Control-s>');update()
    assert Project.from_dict(json.loads(saved.read_text())['project']).flow.alpha_deg == 6
    app.variables['alpha_deg'].set('5');app.save_project()
    app.show_section('geometry');shot('geometry.png')

    # Multi-DAT import retains the common coordinate system.
    paths = []
    for i, contour in enumerate(expected.section.elements*2):
        path = out/f'element_{i}.dat'
        xy = contour.xy.copy() if i == 0 else contour.xy*.25+np.array([1.05, -.08])
        np.savetxt(path, xy, header=f'Element {i}', comments='')
        paths.append(str(path))
    filedialog.askopenfilenames = lambda **k: paths
    app.import_dat();assert len(app.project_from_form().section.elements) == 2
    app.populate(expected, expected_refinement);app.save_project()

    filedialog.askdirectory = lambda **k: str(out)
    app.start();started = time.monotonic();phase = 'running';delays = [];last_tick = time.monotonic()
    def heartbeat():
        nonlocal last_tick
        now = time.monotonic();delays.append(now-last_tick);last_tick = now
        root.after(50, heartbeat)
    root.after(50, heartbeat)
    while phase != 'done':
        update()
        if time.monotonic()-started > 360:
            app.stopping.set()
            raise AssertionError('GUI workflow timeout at '+phase)
        alive = app.worker is not None and app.worker.is_alive()
        if phase == 'running' and len(app.records) >= 2 and alive:
            assert app.entry_widgets['openmp_threads'].instate(['disabled'])
            assert window.widgets[-1].instate(['disabled'])
            app.stop();assert app.phase.get().startswith('Остановка');phase = 'stopping'
        elif phase == 'stopping' and not alive and not app.busy:
            summary = json.loads((app.output/'summary.json').read_text());assert summary['status'] == 'stopped'
            filedialog.askdirectory = lambda **k: str(app.output)
            app.resume();phase = 'resuming'
        elif phase == 'resuming' and not alive and not app.busy:
            summary = json.loads((app.output/'summary.json').read_text())
            assert summary['status'] == 'completed' and summary['steps'] == 30
            assert summary['termination_reason'] == 'max_steps_reached'
            assert app.current_section == 'results' and len(app.panes.panes()) == 1
            assert len(window.table.get_children()) == 4 and app.frame_files
            assert len({r['step'] for r in app.records}) == len(app.records)
            assert len({r['step'] for r in app.residual_records}) == len(app.residual_records)
            source = app.output
            before = {n: (source/n).read_bytes() for n in ('project.json', 'history.csv', 'residuals.csv', 'summary.json')}
            app.show_section('results', 'history');shot('history.png')
            window.window.deiconify();window.window.lift();update()
            window.window.withdraw()
            app.show_view('field');app.field_choice.set('Давление p−p∞, Па');app.draw_frame();shot('pressure.png')
            app.toolbars['field'].zoom();assert app.toolbars['field'].mode
            app.toolbars['field'].zoom()
            filedialog.asksaveasfilename = lambda **k: str(out/'export.png')
            app.toolbars['field'].save_figure();assert (out/'export.png').is_file()
            filedialog.asksaveasfilename = lambda **k: str(out/'pressure.gif')
            app.export_animation();assert app.stop_button.instate(['disabled']);phase = 'animation'
        elif phase == 'animation' and not alive and not app.busy:
            with Image.open(out/'pressure.gif') as gif:assert gif.n_frames >= 2
            app.export_analysis();phase = 'analysis'
        elif phase == 'analysis' and not alive and not app.busy:
            assert app.temporal_report and list(source.glob('analysis_*/FOR_REVIEW.zip'))
            assert all((source/n).read_bytes() == raw for n, raw in before.items())
            assert app.entry_widgets['openmp_threads'].instate(['!disabled'])
            phase = 'done'
        time.sleep(.01)
    # Predetermined display responsiveness limits; short packaging case, two CPU threads.
    assert max(delays) < 5 and np.percentile(delays, 95) < 1, (max(delays), np.percentile(delays, 95))
    app.show_section('results', 'statistics');shot('statistics.png')
    app.root.tk.call('tk', 'scaling', 2.0)
    app.show_section('calculation');root.geometry('1280x720');update()
    for button in (app.start_button, app.resume_button, app.stop_button):
        assert button.winfo_rooty()+button.winfo_height() <= root.winfo_rooty()+root.winfo_height()
    shot('scaled_linux.png')
    root.destroy()
    result = {'status':'passed', 'version':aerokinetic.__version__, 'installed':aerokinetic.__file__,
        'project_legacy_hidden_fields_roundtrip':'passed', 'numeric_input_and_inline_error':'passed',
        'dirty_cancel_and_keyboard_save':'passed', 'multi_dat':'passed', 'mesh_cancel_apply':'passed',
        'sections_resize_1280x720':'passed', 'actual_threads':2, 'actual_stop_resume':'passed',
        'real_residual_components':4, 'stop_reason':summary['termination_reason'], 'read_only_source_files':'passed',
        'png_gif_analysis':'passed', 'heartbeat_max_s':max(delays), 'heartbeat_p95_s':float(np.percentile(delays,95)),
        'heartbeat_limit_max_s':5, 'heartbeat_limit_p95_s':1, 'python_network_attempts':attempts,
        'linux_scaling_simulation':'passed; not a Windows DPI test', 'native_windows':'not_run',
        'aerodynamic_validation':'not_run; GUI acceptance only', 'errors':errors}
    (out/'CHECK.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
