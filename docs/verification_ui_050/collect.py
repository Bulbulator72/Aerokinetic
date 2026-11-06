"""Collect completed installed/frozen checks; does not manufacture run evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

root = Path(__file__).resolve().parents[2]
out = Path(__file__).resolve().parent
work = root.parent/'aerokinetic_ui_050'
load = lambda path: json.loads(path.read_text())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
wheel = work/'release/aerokinetic-0.5.0-py3-none-any.whl'
workflow = load(work/'gui_v2/CHECK.json')
focused = load(work/'final_gui_release/CHECK.json')
frozen = load(work/'frozen_final/checks/CHECK.json')
build = load(work/'frozen_final/BUILD_CHECK.json')
sources = load(work/'SOURCE_COMPARISON.json')
assert workflow['status'] == focused['status'] == frozen['status'] == sources['status'] == 'passed'
assert frozen['frozen'] and frozen['gui'] == 'passed' and frozen['network_attempts'] == 0
assert build['status'] == 'build_checks_passed_target_offline_test_pending'
assert sha(wheel) == sources['new_wheel_sha256'] == build['runtime_wheel_sha256']
suites = list(ET.parse(work/'tests_final.xml').getroot().iter('testsuite'))
tests = sum(int(s.attrib['tests']) for s in suites)
assert tests == 32 and all(int(s.attrib['failures']) == int(s.attrib['errors']) == 0 for s in suites)
with zipfile.ZipFile(wheel) as archive:
    for name in archive.namelist():
        if name.startswith('aerokinetic/') and not name.endswith('/'):
            assert archive.read(name) == (root/'src'/name).read_bytes(), name
old = load(root/'docs/verification_portable/LINUX_FROZEN_CHECK.json')
differences = {kind: {key: abs(frozen[kind][key]-old[kind][key]) for key in ('cl', 'cd', 'cm')}
               for kind in ('uniform_mean', 'refined_mean')}
assert max(d for case in differences.values() for d in case.values()) <= 1e-10
for source, target in (
    (work/'gui_v2/CHECK.json', 'WORKFLOW_CHECK.json'),
    (work/'final_gui_release/CHECK.json', 'FINAL_GUI_CHECK.json'),
    (work/'frozen_final/checks/CHECK.json', 'FROZEN_CHECK.json'),
    (work/'frozen_final/BUILD_CHECK.json', 'LINUX_BUILD.json'),
    (work/'SOURCE_COMPARISON.json', 'SOURCE_COMPARISON.json'),
    (work/'tests_final.xml', 'TESTS.xml'),
    (work/'final_gui_release_check.py', 'final_gui_check.py')):
    shutil.copy2(source, out/target)
drafts = out/'drafts'
drafts.mkdir(exist_ok=True)
for path in out.glob('*_draft.png'):
    shutil.move(path, drafts/path.name)
for name in ('geometry', 'mesh', 'calculation', 'results'):
    shutil.copy2(work/'final_gui_release'/(name+'.png'), out/(name+'.png'))
shutil.copy2(work/'gui_v2/pressure.png', out/'pressure.png')
result = {'date':'2026-10-29', 'version':'0.5.0', 'scope':'Desktop redesign; no numerical kernel changes',
    'status':'passed_with_platform_limits', 'wheel':wheel.name, 'wheel_sha256':sha(wheel),
    'installed_tests':tests, 'installed_real_worker_stop_resume':'passed', 'selected_threads':2,
    'project_input_readonly_view_export':'passed', 'final_layout_and_save_protection':'passed',
    'frozen_relocated_cold_cache_gui':'passed', 'frozen_bundle_files_verified':frozen['bundle_files_verified'],
    'kernel_and_data_unchanged_files':len(sources['unchanged_runtime_files']),
    'runtime_sources_match_wheel':True, 'dependencies_added':False,
    'frozen_startup_mean_differences_vs_040':differences, 'fixed_numerical_tolerance':1e-10,
    'heartbeat_max_s':workflow['heartbeat_max_s'], 'heartbeat_p95_s':workflow['heartbeat_p95_s'],
    'linux_1280x720':'passed', 'linux_large_font_form_reflow_and_primary_actions':'passed',
    'full_large_font_plot_layout':'inconclusive; 200% at 1280x720 needs more plot space; see report',
    'native_windows_050':'not_run; builder performs GUI acceptance on home PC, offline target check required',
    'os_network_isolation':'not_established; Python connection and DNS denied in self-test',
    'aerodynamic_validation':'not_run; no new physical-accuracy claim'}
(out/'CHECKS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
paths = [root/'pyproject.toml', root/'tools/smoke_ui_050.py', root/'tools/make_portable_builder_transfer.py',
         root/'tools/portable/build.py', root/'tools/portable/ak_portable_selftest.py',
         root/'docs/UI_REDESIGN_050_RU.md', root/'docs/RELEASE_050_RU.md']
paths += list((root/'src/aerokinetic').glob('*.py'))
(out/'SOURCE_SHA256.json').write_text(json.dumps({str(p.relative_to(root)):sha(p) for p in paths}, indent=2)+'\n')
print(json.dumps(result, ensure_ascii=False, indent=2))
