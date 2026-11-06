"""Collect completed portable-builder evidence, keeping native Windows separate."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev-root', required=True, type=Path)
    parser.add_argument('--linux-build', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = Path(__file__).parent
    built = json.loads((args.linux_build / 'BUILD_CHECK.json').read_text())
    frozen = json.loads((args.linux_build / 'checks/CHECK.json').read_text())
    assert built['status'] == 'build_checks_passed_target_offline_test_pending'
    assert frozen['status'] == frozen['gui'] == 'passed' and frozen['frozen']
    assert frozen['platform'] == 'linux' and frozen['network_attempts'] == 0
    assert frozen['bundle_files_verified'] == built['bundle_files']
    tests = ET.parse(args.dev_root / 'staging_tests_final.xml').getroot()
    suites = list(tests.iter('testsuite'))
    count = sum(int(s.get('tests', '0')) for s in suites)
    assert count == 8 and all(int(s.get('failures', '0')) == int(s.get('errors', '0')) == 0 for s in suites)
    baseline = json.loads((root / 'docs/verification_040/SOURCE_SHA256.json').read_text())
    runtime = {k: v for k, v in baseline.items() if k.startswith('src/') or k in ('pyproject.toml', 'requirements-offline-py312.txt')}
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == item['sha256'] for name, item in runtime.items())
    first = json.loads((args.dev_root / 'linux_build_v2/checks/CHECK.json').read_text())
    differences = {case: {key: abs(frozen[case][key] - first[case][key]) for key in ('cl', 'cd', 'cm')}
                   for case in ('uniform_mean', 'refined_mean')}
    assert max(v for case in differences.values() for v in case.values()) <= 1e-10
    for src, name in ((args.linux_build / 'BUILD_CHECK.json', 'LINUX_BUILD.json'),
                      (args.linux_build / 'checks/CHECK.json', 'LINUX_FROZEN_CHECK.json'),
                      (args.dev_root / 'staging_tests_final.xml', 'STAGING_TESTS.xml'),
                      (args.dev_root / 'windows_freezer_wheels/CHECK.json', 'WINDOWS_WHEELS.json')):
        shutil.copy2(src, out / name)
    for name in ('history.csv', 'residuals.csv'):
        shutil.copy2(args.linux_build / 'checks/refined_direct' / name, out / ('FROZEN_' + name))
    source_files = list((root / 'tools/portable').rglob('*.py')) + list((root / 'tools/portable').glob('*.spec'))
    source_files += [root / 'tools/portable/requirements-build.txt', root / 'tools/make_portable_builder_transfer.py',
                     root / 'tests/test_portable_packaging.py', root / 'docs/BUILD_PORTABLE_RU.md',
                     root / 'docs/PORTABLE_WINDOWS_RU.md', root / 'README_RU.md', Path(__file__).resolve()]
    source_files = [p for p in source_files if '__pycache__' not in p.parts]
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source_files)}
    (out / 'SOURCE_SHA256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    result = {
        'date': '2026-10-29', 'scope': 'Portable Windows build kit; Windows execution still required',
        'implementation_and_staging': 'passed', 'staging_tests': count,
        'frozen_linux_real_jit_and_solver': 'passed', 'frozen_linux_gui_load_mesh_residuals': 'passed',
        'frozen_relocation_unicode_path': 'passed', 'frozen_manifest_verified_files': frozen['bundle_files_verified'],
        'python_network': 'blocked, zero attempts',
        'os_network_isolation': 'not established; offline target must be disconnected',
        'exact_resume_histories': 'passed', 'original_runtime_and_dependency_files_unchanged': len(runtime),
        'mean_difference_from_first_frozen_linux_proof': differences,
        'windows_freezer_wheels': 'downloaded; windowed PE x64 bootloader inspected',
        'native_windows_build_and_msvc': 'not_run', 'clean_offline_target': 'not_run',
        'aerodynamic_validation': 'not_applicable; packaging only, startup cases',
        'test_harness_corrections': [
            'Residual GUI test now opens actual results before expecting four component rows',
            'GUI settings compare every value/geometry array exactly; raw JSON fingerprints distinguish 10000 and 10000.0'
        ],
        'resolved_packaging_failures': [
            'altgraph 0.17.4 used pkg_resources removed by pinned setuptools; replaced with 0.17.5',
            'Managed Linux Tcl/Tk 9 requires explicit library/resource inclusion (Windows standard hook unchanged)',
            'Pillow 12.3 native Tk bridge dynamically imports PIL._tkinter_finder; added explicit hidden import'
        ],
        'proof_build': str(args.linux_build),
        'distribution': 'One TXT/BAT builder, not a prebuilt Windows EXE'
    }
    (out / 'CHECKS.json').write_text(json.dumps(result, indent=2) + '\n')
    report = f'''# Проверки переносимого сборщика, 29 октября 2026 г.

Реализован сценарий домашней сборки отдельной папки с Windows EXE,
интерпретатором, научными зависимостями и личной копией x64 MSVC/SDK.
Оригинальная программа и её `.venv` не изменяются. Нативный Windows EXE
здесь не собран: в среде разработки доступен Linux.

Выполнены {count} проверок staging/адаптера на синтетических файлах:
целостность копии, перенос путей, отсутствие системного поиска MSVC,
отказ при неполном комплекте/изменённом манифесте, узкий Python SDK и
удаление путей разработчика. Это не исполнение Windows-компилятора.

В Linux построен настоящий frozen executable PyInstaller 6.16.0. Он
скопирован в путь с пробелами и кириллицей. С пустым кешем выполнены
OpenMP-компиляция, NACA0012 на равномерной N=20 и сгущённой N=16/32 сетках,
1/2 CPU-потока, остановка/продолжение с точным совпадением CSV, загрузка
всех встроенных серий продувок, реальные поля, CSV/PNG/GIF и архив анализа.
В замороженном приложении открыты реальные результаты, окно сетки и
четыре компоненты невязок; проверено сохранение значений и исходных файлов.
Проверены SHA-256 {frozen['bundle_files_verified']} файлов перенесённого комплекта.

Соединения и DNS Python блокировались, попыток не было. Системная изоляция
всех нативных библиотек этим не доказана. Для GUI в разработке использован
локальный X-сервер. Windows на целевом ПК требуется проверить без сети
отдельно. Linux-прототип использует системный g++, он не является переносимой
Windows-поставкой и пользователю как EXE не выдаётся.

Научные файлы и закреплённые зависимости совпадают с выпуском 0.4.0
({len(runtime)} файлов). Новая сборка проверяется по той же идентичности
источников и метаданным версий; защита checkpoint не отключена.
Разность средних Cl/Cd/Cm между первым успешным headless frozen-прототипом
и итоговым графическим вариантом не превышает 1e-10. Это регрессия упаковки,
а не численная сходимость или экспериментальная валидация.

При разработке найдены и устранены несовместимость altgraph/pkg_resources,
неполное обнаружение Tcl/Tk 9 в Linux и скрытый модуль Pillow Tk bridge.
Две проверки GUI исправлены в тестовом сценарии: у пустого проекта ещё нет
невязок, а JSON-числа 10000 и 10000.0 имеют разную запись при равных
значениях. Итоговая проверка использует реальные результаты и проверяет
каждое физическое/сеточное/временное значение и координаты без изменения
исходных результатов. Допуски численного ядра не менялись.

Команды: `python -m pytest tests/test_portable_packaging.py -q`;
`python tools/portable/build.py --app-root <dev> --kit-root <source>
--wheel <released-0.4-wheel> --work-dir <new-directory> --prepared-env
--linux-proof` с действующим DISPLAY. Флаг `--linux-proof` — только для
отдельно обозначенного Linux-прототипа. Для пользователя подготовлен BAT,
который на Windows создаёт свою среду и запускает полный протокол.

Машинные доказательства: CHECKS.json, LINUX_BUILD.json,
LINUX_FROZEN_CHECK.json, STAGING_TESTS.xml, WINDOWS_WHEELS.json и CSV.
Windows-пакеты сборщика скачаны, PE x64 bootloader проверен как файл;
их исполнение и работа на Windows/MSVC пока имеют статус `not_run`.
После успешной домашней сборки требуется CHECK_PORTABLE.bat на рабочем ПК.
'''
    (out / 'REPORT_RU.md').write_text(report, encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
