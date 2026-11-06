"""Package completed multi-element evidence, preserving the raw run locations."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

root = Path(__file__).resolve().parents[2]
out = Path(__file__).resolve().parent
work = root.parent/'aerokinetic_mechanization_20261102'
checks = work/'check_v2'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
data = load(checks/'CHECK.json')
gui = load(work/'gui_results/CHECK.json')
assert data['status'] == gui['status'] == 'passed'
assert data['re6m']['status'] == 'unsupported_not_run'
assert data['restart_exact_csv_prefix'] == 'passed'
assert all(case['summary']['status'] == 'completed' for case in data['cases'].values())
assert data['uniform_slotted']['status'] == 'completed'
wheel = root.parent/'aerokinetic_ui_050/release/aerokinetic-0.5.0-py3-none-any.whl'
installed = Path(data['installed']).parent
with zipfile.ZipFile(wheel) as archive:
    for name in archive.namelist():
        if name.startswith('aerokinetic/') and not name.endswith('/'):
            assert (installed/Path(name).relative_to('aerokinetic')).read_bytes() == archive.read(name), name
for source, name in ((checks/'CHECK.json','RUN_CHECK.json'),
                     (checks/'GAP_FIELDS_CHECK.json','GAP_FIELDS_CHECK.json'),
                     (work/'gui_results/CHECK.json','GUI_CHECK.json'),
                     (work/'gui_geometry/CHECK.json','GUI_IMPORT_CHECK.json')):
    shutil.copy2(source,out/name)
for kind in ('slotted','double','fowler'):
    case_out = out/kind
    case_out.mkdir(exist_ok=True)
    for name in ('project.json','metadata.json','summary.json','history.csv','residuals.csv'):
        shutil.copy2(checks/(kind+'_run')/name,case_out/name)
    shutil.copy2(checks/(kind+'_mesh.png'),out/(kind+'_mesh.png'))
    shutil.copy2(work/'gui_results'/(kind+'_field.png'),out/(kind+'_gui_field.png'))
for name in ('speed.png','pressure.png','slotted.gif','speed_zoom.png','gap_zoom.png','history.png'):
    shutil.copy2(checks/name,out/name)
if not (out/'inputs').exists():shutil.copytree(checks/'inputs',out/'inputs')
gaps = {kind:[p['minimum_gap_in_fine_cells'] for p in entry['geometry_32_64']['pairs'] if p['direct_fluid_gap']]
        for kind,entry in data['cases'].items()}
result = {'date':'2026-11-02','version':'0.5.0','status':'functionality_passed_accuracy_not_established',
    'installed_wheel_sha256':sha(wheel),'installed_runtime_matches_released_wheel':True,
    'reynolds_actually_run':10000,'requested_reynolds':6000000,'re6m':'unsupported_not_run',
    'configurations':{'slotted':2,'double':3,'fowler':2},'refined_cpu_threads':2,
    'coarse_fine_cells_per_chord':[32,64],'geometry_only_other_grid':[48,96],
    'actual_minimum_direct_gap_cells':gaps,'main_case_steps':data['cases']['slotted']['summary']['steps'],
    'main_case_t_star':data['cases']['slotted']['summary']['t_star'],
    'refined_wall_intersections_and_isotropic_loads':'passed','refined_mass_momentum_budgets':'passed',
    'restart_csv':'byte_identical','reflection_max_differences':data['reflection_max_differences'],
    'uniform_two_element_run':'passed','actual_multi_dat_gui_result_load':'passed','field_csv_png_gif':'passed',
    'python_connections_and_dns':'blocked during numerical check',
    'gui_network':'local X11 permitted; no new network resources required',
    'native_windows':'not_run','gpu':'not_run','aerodynamic_tunnel_validation':'not_performed',
    'numerical_and_statistical_convergence':'not_established','runtime_code_changes':False,
    'raw_run_location':str(checks),'elapsed_full_check_seconds':data['elapsed_seconds']}
(out/'CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
paths = [root/'tools/check_multielement.py',root/'tools/check_multielement_gui.py',out/'PLAN.json',
         out/'collect.py',out/'analyze_fields.py',out/'REPORT_RU.md']
paths += list(installed.glob('*.py'))
paths += list((checks/'inputs').glob('*'))
(out/'SOURCE_SHA256.json').write_text(json.dumps({str(p):sha(p) for p in paths if p.is_file()},indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
