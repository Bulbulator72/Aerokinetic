"""Installed-wheel multi-element checks. Short startup runs are not tunnel validation."""
import argparse
from dataclasses import asdict, replace
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sys
import time
import traceback


def polygon_gap(first, second):
    import numpy as np
    best = (math.inf, None, None)
    for points, poly, reverse in ((first, second, False), (second, first, True)):
        starts = poly
        edges = np.roll(poly, -1, axis=0)-poly
        for point in points:
            fractions = np.clip(np.einsum('ij,ij->i', point-starts, edges)/np.sum(edges*edges, axis=1), 0, 1)
            near = starts+fractions[:, None]*edges
            distances = np.linalg.norm(near-point, axis=1)
            k = int(np.argmin(distances))
            if distances[k] < best[0]:
                best = (float(distances[k]), near[k] if reverse else point, point if reverse else near[k])
    return best


def geometry_check(project, refinement):
    import numpy as np
    from aerokinetic.mesh import preview
    mesh = preview(project, refinement)
    dx = mesh.plan.fine_level.dx
    origin = np.array(mesh.fine_origin)
    x, y = [origin[a]+(np.arange(mesh.fine_shape[a])+.5)*dx for a in (0, 1)]
    xx, yy = np.meshgrid(x, y, indexing='ij')
    masks = [e.contains(xx, yy) for e in mesh.contours]
    pairs = []
    for i, first in enumerate(mesh.contours):
        assert np.count_nonzero(masks[i]) > 0, 'Missing solid element: '+first.name
        for j in range(i+1, len(mesh.contours)):
            gap, a, b = polygon_gap(first.xy, mesh.contours[j].xy)
            s = np.linspace(.001, .999, 200)[:, None]
            path = a+(b-a)*s
            crossed = [k for k, e in enumerate(mesh.contours) if e.contains(*path.T).any()]
            assert i not in crossed and j not in crossed, 'Closest pair segment crosses one of its own polygons'
            touching = 0
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    xa, xb = slice(max(0, ox), min(len(x), len(x)+ox)), slice(max(0, -ox), min(len(x), len(x)-ox))
                    ya, yb = slice(max(0, oy), min(len(y), len(y)+oy)), slice(max(0, -oy), min(len(y), len(y)-oy))
                    touching += int(np.count_nonzero(masks[i][xa, ya] & masks[j][xb, yb]))
            pairs.append({'elements':[first.name, mesh.contours[j].name], 'minimum_gap_over_full_chord':gap,
                          'minimum_gap_in_fine_cells':gap/dx, 'discrete_8_neighbour_solid_contacts':touching,
                          'direct_fluid_gap':not crossed, 'intervening_elements':[mesh.contours[k].name for k in crossed],
                          'nearest_points_normalized':[a.tolist(), b.tolist()]})
    return mesh, {'shape':list(mesh.plan.shape), 'fine_shape':list(mesh.fine_shape),
        'memory_gib_estimate':mesh.plan.estimated_memory_gib, 'solid_counts':mesh.solid_counts,
        'assembly_chord_input_units':project.section.chord, 'pairs':pairs,
        'all_elements_represented':True, 'note':'Geometry representation does not establish wall-layer or gap-flow accuracy'}


def wall_check(solver, thresholds):
    import numpy as np
    from lbmpy.maxwellian_equilibrium import get_weights
    walls = solver.walls
    original = walls.objects
    pdf = solver._fine.data_handling.cpu_arrays[solver._fine.pdf_array_name]
    snapshot = pdf.copy()
    result = []
    try:
        for contour, obj in zip(solver.contours, original):
            walls.objects = [obj]
            links = 0;fallback = 0
            for block, _, indices in walls.owned_links():
                links += len(indices)
                e = solver.transfer.stencil[indices['dir']][:, :2]
                p = np.column_stack([indices[a]+block.offset[k]+.5 for k, a in enumerate(('x','y'))])
                p = p*solver.fine_level.dx+walls.origin
                active = indices['q'] > 0
                q = indices['q'][active]
                assert (q <= 1).all()
                hit = p[active]+q[:, None]*e[active]*solver.fine_level.dx
                epsilon = 1e-6*e[active]*solver.fine_level.dx
                assert not contour.contains(*(hit-epsilon).T).any(), contour.name+' crossing before wall'
                assert contour.contains(*(hit+epsilon).T).all(), contour.name+' missing intersection'
                fallback += int(np.count_nonzero(~active))
            assert links > 0, 'Element has no owned wall links: '+contour.name
            pdf[...] = [float(w) for w in get_weights(solver._fine.method.stencil)]
            solver._fine.boundary_handling(**solver._fine.kernel_params)
            loads = walls.measure()
            assert np.max(np.abs(loads['force_lattice'])) <= thresholds['per_body_and_total_isotropic_force_lattice_atol']
            assert abs(loads['moment_z_common_lattice']) <= thresholds['isotropic_moment_common_lattice_atol']
            result.append({'element':contour.name, 'owned_links':links, 'owned_fallback_links':fallback,
                'isotropic_force_lattice':loads['force_lattice'].tolist(),
                'isotropic_moment_common_lattice':loads['moment_z_common_lattice']})
        walls.objects = original
        loads = walls.measure()
        assert np.max(np.abs(loads['force_lattice'])) <= thresholds['per_body_and_total_isotropic_force_lattice_atol']
        assert abs(loads['moment_z_common_lattice']) <= thresholds['isotropic_moment_common_lattice_atol']
    finally:
        walls.objects = original
        pdf[...] = snapshot
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve();out.mkdir(parents=True, exist_ok=False)
    config = out/'pystencils.json'
    config.write_text(json.dumps({'cache':{'object_cache':str(out/'jit'), 'clear_cache_on_start':False}}))
    os.environ.update(PYSTENCILS_CONFIG=str(config), PYSTENCILS_CACHE_DIR=str(out/'symbolic'),
                      MPLCONFIGDIR=str(out/'matplotlib'), MPLBACKEND='Agg', OPENBLAS_NUM_THREADS='1')
    attempts = []
    def denied(*a, **k):
        attempts.append('Python network connection/DNS')
        raise AssertionError('Connections are disabled for this check')
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = denied
    socket.getaddrinfo = socket.gethostbyname = socket.gethostbyname_ex = denied
    import numpy as np
    import aerokinetic
    from aerokinetic import Contour, Section, Project, Flow, Grid, RunSettings, RefinementSettings, RefinedSolver, Solver
    from aerokinetic.refined_solver import refined_project_dict
    from aerokinetic.mesh import draw
    from aerokinetic.refined_visualization import draw_frame, animate
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from PIL import Image
    from aerokinetic.temporal import write_analysis
    root = Path(__file__).resolve().parents[1]
    plan = json.loads((root/'docs/verification_multielement_050/PLAN.json').read_text())
    limits = plan['fixed_tolerances']
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), aerokinetic.__file__
    assert aerokinetic.__version__ == '0.5.0'
    shutil.copytree(args.fixtures, out/'inputs')
    report = {'status':'running', 'version':aerokinetic.__version__, 'installed':aerokinetic.__file__,
              'reynolds_actually_tested':10000, 'requested_reynolds':6000000, 'cases':{},
              'scope':'Installed multi-element functionality, not converged aerodynamics',
              'native_windows':'not_run', 'aerodynamic_validation':'not_performed'}
    started = time.monotonic()
    def save_report():
        (out/'CHECK.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    def make(kind, n=32, duration=1., alpha=4., mirror=False):
        data = json.loads((out/'inputs'/(kind+'.json')).read_text())
        elements = tuple(Contour(np.array(e['xy'])*([1,-1] if mirror else [1,1]), e['name']) for e in data['elements'])
        base = Section(elements)
        xy = np.concatenate([e.xy for e in elements]);le = xy[np.argmin(xy[:,0])]
        section = Section(elements, base.chord, tuple(le+np.array([.25*base.chord, 0.])))
        p = Project(section, Flow(reynolds=10000, alpha_deg=alpha, physical_chord_m=1., perturbation=0),
            Grid(cells_per_chord=n, upstream_chords=1.5, downstream_chords=3., transverse_chords=1.5,
                 span_chords=.25, memory_limit_gib=3., openmp_threads=2),
            RunSettings(duration_convective=duration, sample_every=1, frame_every=100,
                        check_every=8, checkpoint_every=128, average_after=0.), 'NACA0012 '+kind+'; FUNCTIONAL TEST')
        return p
    refinement = RefinementSettings()
    try:
        try:
            Flow(reynolds=6e6, physical_chord_m=2.)
        except ValueError as error:
            report['re6m'] = {'status':'unsupported_not_run', 'reason':str(error)}
        else:
            raise AssertionError('The released guard was unexpectedly bypassed')
        for kind in ('slotted', 'double', 'fowler'):
            p = make(kind)
            for e in p.section.elements:
                dat = Contour.from_dat(out/'inputs'/f'{kind}_{e.name}.dat')
                np.testing.assert_array_equal(dat.xy, e.xy)
            mesh, check = geometry_check(p, refinement)
            assert mesh.plan.estimated_memory_gib <= p.grid.memory_limit_gib
            # No neighbouring solid flags may bridge the slit in these fixtures.
            assert all(x['discrete_8_neighbour_solid_contacts'] == 0 for x in check['pairs']), check['pairs']
            _, finer_check = geometry_check(make(kind,n=48), refinement)
            report['cases'][kind] = {'geometry_32_64':check, 'geometry_48_96_only':finer_check}
            print(kind, 'elements',len(p.section.elements),'memory estimate',check['memory_gib_estimate'],
                  'gaps/dx',[x['minimum_gap_in_fine_cells'] for x in check['pairs']], flush=True)
            (out/(kind+'_project.json')).write_text(json.dumps(refined_project_dict(p,refinement), ensure_ascii=False,indent=2)+'\n')
            fig = Figure(figsize=(11,5),constrained_layout=True);FigureCanvasAgg(fig)
            draw(fig,mesh,body_zoom=True);fig.savefig(out/(kind+'_mesh.png'),dpi=140)
        save_report()
        p = make('slotted')
        solver = RefinedSolver(p,refinement,progress=lambda m:print(m,flush=True))
        report['cases']['slotted']['wall_checks'] = wall_check(solver,limits)
        budgets = [];direct_rows = []
        def sample(s, record):
            budgets.append(s.last_budget.copy());direct_rows.append(record.copy())
            assert abs(s.last_budget['mass_residual_per_volume']) <= limits['mass_budget_per_volume_atol']
            assert max(map(abs,s.last_budget['momentum_residual_per_volume'])) <= limits['momentum_budget_per_volume_atol']
            if s.steps%100 == 0:print('slotted',s.steps,f"t*={record['t_star']:.5f} Cl={record['cl']:.6g}",flush=True)
        summary = solver.run(out/'slotted_run',callback=sample)
        report['cases']['slotted']['summary'] = summary
        save_report()
        assert summary['status'] == 'completed', summary
        report['cases']['slotted']['max_mass_budget_per_volume'] = max(abs(b['mass_residual_per_volume']) for b in budgets)
        report['cases']['slotted']['max_momentum_budget_per_volume'] = max(max(map(abs,b['momentum_residual_per_volume'])) for b in budgets)
        del solver;gc.collect()
        short = make('slotted', duration=.04)
        first = RefinedSolver(short,refinement)
        stopped = first.run(out/'slotted_restart',callback=lambda s,r:s.steps < 16)
        assert stopped['status'] == 'stopped' and stopped['steps'] == 16
        del first;gc.collect()
        resumed = RefinedSolver.resume_run(out/'slotted_restart')
        assert resumed['status'] == 'completed'
        for name in ('history.csv','residuals.csv'):
            reference = (out/'slotted_run'/name).read_bytes().splitlines(keepends=True)
            actual = (out/'slotted_restart'/name).read_bytes()
            assert b''.join(reference[:len(actual.splitlines())]) == actual, name
        report['restart_exact_csv_prefix'] = 'passed'
        gc.collect()
        mirrored = RefinedSolver(make('slotted',duration=.04,alpha=-4.,mirror=True),refinement)
        mirror_rows = []
        ms = mirrored.run(out/'slotted_mirror',callback=lambda s,r:mirror_rows.append(r.copy()))
        assert ms['status'] == 'completed'
        differences = {key:max(abs(r[key]-((-1 if key in ('cl','cm') else 1)*a[key])) for r,a in zip(mirror_rows,direct_rows)) for key in ('cl','cd','cm')}
        assert max(differences.values()) <= limits['reflection_coefficients_atol'], differences
        report['reflection_max_differences'] = differences
        del mirrored;gc.collect();save_report()
        for kind in ('double','fowler'):
            print('Running',kind,flush=True)
            solver = RefinedSolver(make(kind,duration=.04),refinement)
            report['cases'][kind]['wall_checks'] = wall_check(solver,limits)
            summary = solver.run(out/(kind+'_run'))
            report['cases'][kind]['summary'] = summary
            save_report()
            assert summary['status'] == 'completed', summary
            del solver;gc.collect()
        print('Uniform two-element run',flush=True)
        p = make('slotted',n=64,duration=.04)
        uniform = Solver(p.section,p.flow,p.grid)
        summary = uniform.run(output=out/'uniform_slotted',**asdict(p.run))
        report['uniform_slotted'] = summary
        assert summary['status'] == 'completed', summary
        del uniform;gc.collect()
        print('Exporting real multi-element fields and GIF',flush=True)
        frames = sorted((out/'slotted_run/frames').glob('*.npz'))
        with np.load(frames[-1],allow_pickle=False) as frame:
            assert len(json.loads(str(frame['contours_json']))) == 2
            fluid = ~frame['fine_mask']
            assert np.isfinite(frame['fine_speed'][fluid]).all()
            assert np.isfinite(frame['fine_cp'][fluid]).all()
            report['fine_field_speed_range'] = [float(np.nanmin(frame['fine_speed'])),float(np.nanmax(frame['fine_speed']))]
        fig = Figure(figsize=(11,5),constrained_layout=True);FigureCanvasAgg(fig)
        draw_frame(fig,frames[-1],'speed');fig.savefig(out/'speed.png',dpi=150)
        draw_frame(fig,frames[-1],'pressure_pa');fig.savefig(out/'pressure.png',dpi=150)
        gif = animate(out/'slotted_run',out/'slotted.gif',field='speed',max_frames=10)
        with Image.open(gif) as image:assert image.n_frames >= 2
        folder, _ = write_analysis(out/'slotted_run')
        assert (folder/'FOR_REVIEW.zip').is_file()
        assert not attempts
        report.update(status='passed',export='passed',python_network_attempts=attempts,elapsed_seconds=time.monotonic()-started)
        print('PASS multi-element functionality; Re=6e6 NOT RUN; no aerodynamic validation',flush=True)
    except Exception as error:
        report.update(status='failed',error=str(error),traceback=traceback.format_exc(),elapsed_seconds=time.monotonic()-started)
        raise
    finally:
        save_report()


if __name__ == '__main__':
    main()
