"""Cold-cache end-to-end acceptance inside the same frozen executable."""
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import time


def run_checks(output, runtime, *, gui=False):
    started = time.monotonic()
    output = Path(output)
    import numpy as np
    import pystencils as ps
    from pystencils import CreateKernelConfig
    import aerokinetic
    from aerokinetic import Project, Solver, RefinedSolver
    from aerokinetic.refined_checkpoint import numerical_identity
    from aerokinetic.experiments import catalog, load_series
    from aerokinetic.animation import animate
    from aerokinetic.temporal import write_analysis
    from PIL import Image

    runtime_identity = json.loads((Path(runtime['bundle']) / 'portable/runtime_identity.json').read_text())
    assert aerokinetic.__version__ == runtime_identity['version']
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(runtime['bundle']))
    for name, digest in runtime_identity['files'].items():
        source = Path(aerokinetic.__file__).parent / name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'Runtime file differs: ' + name
    expected = json.loads((Path(runtime['bundle']) / 'portable/source_identity.json').read_text())
    assert numerical_identity() == expected, 'Numerical source identity differs from the bundled wheel'
    print('[check 1/5] Real OpenMP C++ compilation from bundled headers', flush=True)
    src, dst = ps.fields('source, destination: double[1D]')
    kernel = ps.create_kernel(ps.Assignment(dst.center, 3 * src.center + 1),
                              config=CreateKernelConfig(cpu_openmp=2)).compile()
    a = np.arange(1000, dtype=np.float64)
    b = np.full_like(a, np.nan)
    kernel(source=a, destination=b)
    np.testing.assert_array_equal(b, 3 * a + 1)
    assert '#pragma omp' in ps.get_code_str(kernel.ast)

    def project(n, alpha, threads):
        return Project.from_dict({
            'geometry': {'naca0012': {'n_side': 51}},
            'flow': {'reynolds': 10000, 'alpha_deg': alpha, 'physical_chord_m': .15, 'perturbation': 0},
            'grid': {'cells_per_chord': n, 'upstream_chords': 1, 'downstream_chords': 2,
                     'transverse_chords': 1, 'span_chords': .5, 'memory_limit_gib': 2, 'openmp_threads': threads},
            'run': {'duration_convective': .025, 'sample_every': 2, 'frame_every': 2,
                    'check_every': 2, 'checkpoint_every': 4, 'average_after': 0}})

    print('[check 2/5] NACA0012 uniform grid N=20 (another kernel specialization)', flush=True)
    p = project(20, 0, 1)
    uniform = Solver(p.section, p.flow, p.grid).run(output=output / 'uniform', **asdict(p.run))
    assert uniform['status'] == 'completed'
    print('[check 3/5] NACA0012 two-level N=16/32, two threads, real stop/resume', flush=True)
    p = project(16, 5, 2)
    direct = RefinedSolver(p).run(output / 'refined_direct')
    stopped = RefinedSolver(p).run(output / 'refined_resume', callback=lambda s, r: s.steps < 4)
    assert stopped['status'] == 'stopped'
    resumed = RefinedSolver.resume_run(output / 'refined_resume')
    assert direct['status'] == resumed['status'] == 'completed'
    for name in ('history.csv', 'residuals.csv'):
        assert (output / 'refined_direct' / name).read_bytes() == (output / 'refined_resume' / name).read_bytes(), name
    for summary in (uniform, direct, resumed):
        assert summary['termination_reason'] == 'duration_reached'
        assert summary['last_residuals']
        assert all(np.isfinite(summary['mean'][key]) for key in ('cl', 'cd', 'cm'))

    print('[check 4/5] Built-in references, real fields, CSV/PNG/GIF and review archive', flush=True)
    references = catalog()
    for reference in references:
        load_series(reference['id'])
    gif = animate(output / 'refined_direct', field='speed', max_frames=5)
    with Image.open(gif) as image:
        assert image.n_frames >= 2
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from aerokinetic.refined_visualization import draw_frame
    figure = Figure(figsize=(8, 4))
    FigureCanvasAgg(figure)
    frame = sorted((output / 'refined_direct/frames').glob('*.npz'))[-1]
    draw_frame(figure, frame, 'pressure_pa')
    figure.savefig(output / 'pressure.png')
    review_folder, _ = write_analysis(output / 'refined_direct')
    assert (review_folder / 'FOR_REVIEW.zip').is_file()
    gui_state = gui_checks(output / 'refined_direct', p) if gui else 'not_run'
    return {'status': 'passed', 'frozen': runtime['frozen'], 'version': aerokinetic.__version__,
            'openmp_compile_execute': 'passed', 'tested_threads': [1, 2],
            'different_grid_specializations': [20, [16, 32]], 'checkpoint_exact_history': 'passed',
            'numerical_identity': expected, 'uniform_mean': uniform['mean'], 'refined_mean': direct['mean'],
            'field_csv_png_gif_review': 'passed', 'reference_series_checked': len(references),
            'gui': gui_state, 'runtime': runtime, 'elapsed_seconds': time.monotonic() - started,
            'aerodynamic_validation': 'not_run; short packaging smoke only',
            'clean_windows_without_installed_tools': 'not_run; run transferred folder on offline target',
            'os_level_network_isolation': 'not_established_by_this_test'}


def gui_checks(directory, expected):
    print('[check 5/5] Actual desktop, mesh and residual windows', flush=True)
    import tkinter as tk
    from tkinter import filedialog, messagebox
    import numpy as np
    from aerokinetic.gui import Application
    directory = Path(directory)
    errors = []
    original_error, original_info = messagebox.showerror, messagebox.showinfo
    original_directory = filedialog.askdirectory
    original_save = filedialog.asksaveasfilename
    messagebox.showerror = lambda *a, **k: errors.append(str(a))
    messagebox.showinfo = lambda *a, **k: None
    root = None
    try:
        root = tk.Tk()
        root.report_callback_exception = lambda *a: errors.append(str(a))
        app = Application(root)
        saved = {name: (directory / name).read_bytes()
                 for name in ('project.json', 'history.csv', 'residuals.csv', 'summary.json')}
        filedialog.askdirectory = lambda **kwargs: str(directory)
        app.open_output()
        assert app.document_readonly, 'A saved result must offer Save As, preserving its original project'
        for section in ('geometry', 'mesh', 'calculation', 'results'):
            app.show_section(section)
            root.update()
        assert len(app.panes.panes()) == 1, 'Results must use the full working area'
        root.geometry('1280x720')
        root.update()
        for button in (app.start_button, app.resume_button, app.stop_button):
            assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        exported = directory.parent / 'exported_project.json'
        filedialog.asksaveasfilename = lambda **kwargs: str(exported)
        assert app.save_project() and exported.is_file()
        app.open_mesh()
        app.open_convergence()
        root.update()
        assert not errors, errors
        assert len(app.residual_window.table.get_children()) == 4, 'Loaded residual table must have four components'
        assert app.mesh_window.window.winfo_viewable(), 'Mesh window did not become visible'
        assert app.records and app.residual_records and app.frame_files
        actual = app.project_from_form()
        # GUI number parsing may represent JSON 10000 as 10000.0. Compare every
        # physical/grid/run value, not hashes that encode JSON number spelling.
        assert actual.flow == expected.flow, 'GUI changed flow values'
        assert actual.grid == expected.grid, 'GUI changed grid values'
        assert actual.run == expected.run, 'GUI changed run/stopping values'
        assert actual.section.chord == expected.section.chord
        np.testing.assert_array_equal(actual.section.moment_reference, expected.section.moment_reference)
        assert len(actual.section.elements) == len(expected.section.elements)
        for a, b in zip(actual.section.elements, expected.section.elements):
            np.testing.assert_array_equal(a.xy, b.xy)
        assert all((directory / name).read_bytes() == data for name, data in saved.items())
        return 'passed'
    finally:
        if root:
            root.destroy()
        messagebox.showerror, messagebox.showinfo = original_error, original_info
        filedialog.askdirectory = original_directory
        filedialog.asksaveasfilename = original_save
