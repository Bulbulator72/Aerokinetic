"""Build a private Windows portable app without modifying the existing install.

The Linux-proof switch is for development evidence only, not a Windows target.
"""
import argparse
from email.parser import Parser
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import tomllib
import uuid
import zipfile

from toolchain import DEFAULT_FLAGS, discover_windows, stage_windows, stage_python_sdk, write_manifest, file_hash

IDENTITY_FILES = ('interface.py', 'refinement.py', 'walls.py', 'refined_solver.py', 'refined_checkpoint.py',
                  'parallel.py', 'convergence.py', 'config.py', 'geometry.py', 'project.py')


def verify_runtime_wheel(kit, wheel):
    """Reject stale versions or source files before copying a toolchain."""
    with (kit / 'pyproject.toml').open('rb') as handle:
        expected_version = tomllib.load(handle)['project']['version']
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise ValueError('Runtime wheel is damaged')
        metadata_names = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise ValueError('Runtime wheel needs one package metadata record')
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode('utf-8'))
        if metadata['Name'] != 'aerokinetic' or metadata['Version'] != expected_version:
            raise ValueError('Runtime wheel version differs from the source project')
        runtime_names = [n for n in archive.namelist() if n.startswith('aerokinetic/') and not n.endswith('/')]
        for name in runtime_names:
            if (kit / 'src' / name).read_bytes() != archive.read(name):
                raise ValueError('Kit source differs from the tested wheel: ' + name)
        identity = {name: hashlib.sha256(archive.read('aerokinetic/' + name)).hexdigest() for name in IDENTITY_FILES}
        runtime_identity = {'version': expected_version, 'files': {
            name.removeprefix('aerokinetic/'): hashlib.sha256(archive.read(name)).hexdigest()
            for name in runtime_names}}
    return expected_version, identity, runtime_identity


def stripped_environment(environment=None, target_platform=None):
    env = dict(os.environ if environment is None else environment)
    for key in list(env):
        if key.upper().startswith(('PYTHON', 'PYSTENCILS', 'VSCMD', 'VCTOOLS', 'VSINSTALL', 'VCINSTALL', 'WINDOWSSDK')) or key.upper() in ('INCLUDE', 'LIB', 'LIBPATH', 'CL', '_CL_', 'LINK', '_LINK_', 'UNIVERSALCRTSDKDIR', 'UCRTVERSION', 'WINDOWSLIBPATH', 'EXTERNAL_INCLUDE') or re.fullmatch(r'VS\d+COMNTOOLS', key.upper()):
            del env[key]
    if (target_platform or sys.platform) == 'win32':
        system = Path(env.get('SystemRoot', r'C:\Windows'))
        env['PATH'] = str(system / 'System32') + ';' + str(system)
    else:
        env['PATH'] = '/usr/bin:/bin'
    env['OPENBLAS_NUM_THREADS'] = '1'
    return env


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', required=True, type=Path)
    parser.add_argument('--kit-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--wheel', type=Path)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--vs-path', type=Path)
    parser.add_argument('--linux-proof', action='store_true')
    parser.add_argument('--prepared-env', action='store_true', help='Development only: use this prevalidated isolated venv')
    parser.add_argument('--skip-gui-proof', action='store_true', help='Development only when no display is available')
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        parser.error('Run with existing 64-bit CPython 3.12; embeddable Python without headers is not sufficient')
    if sys.platform != 'win32' and not (args.linux_proof and sys.platform == 'linux'):
        parser.error('Build Windows on Windows. Linux can only run the explicitly labeled development proof.')
    app, kit = args.app_root.resolve(), args.kit_root.resolve()
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    work = (args.work_dir or app / 'portable_builds' / ('build_' + stamp)).resolve()
    work.mkdir(parents=True, exist_ok=False)
    log_path = work / 'BUILD.log'
    log = log_path.open('w', encoding='utf-8', buffering=1)
    previous_log = app / 'PORTABLE_BUILD_LAST.log'
    if previous_log.is_file():
        shutil.copy2(previous_log, work / 'previous_build.log')
    pointer = app / 'PORTABLE_READY_TO_COPY.txt'
    if pointer.is_file():
        shutil.copy2(pointer, work / 'previous_ready_to_copy.txt')
    pointer.write_text('Build in progress. No new bundle is accepted yet.\nLog: ' + str(log_path) + '\n', encoding='utf-8')
    started = time.monotonic()
    result = {'status': 'failed', 'platform': sys.platform, 'work': str(work), 'log': str(log_path)}

    def note(text):
        print(text, flush=True)
        log.write(text + '\n')

    def run(command, *, environment=None, timeout=1200):
        command = [str(item) for item in command]
        log.write('COMMAND: ' + subprocess.list2cmdline(command) + '\n')
        log.flush()
        process = subprocess.Popen(command, cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=sys.platform != 'win32')
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if sys.platform == 'win32':
                taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe'
                subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'], stdout=log, stderr=subprocess.STDOUT, check=False)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError('Build/check timed out; child process tree stopped. See ' + str(log_path))
        if code:
            raise RuntimeError(f'Command failed with code {code}. See {log_path}')

    try:
        note('AeroKinetic portable builder: app remains unchanged. Build log: ' + str(log_path))
        with (kit / 'pyproject.toml').open('rb') as handle:
            version = tomllib.load(handle)['project']['version']
        wheel_name = f'aerokinetic-{version}-py3-none-any.whl'
        candidates = [kit / 'wheelhouse' / wheel_name,
                      kit / 'downloads/wheels_windows' / wheel_name,
                      app / 'downloads/wheels_windows' / wheel_name]
        wheel = (args.wheel or next((p for p in candidates if p.is_file()), candidates[0])).resolve()
        version, identity, runtime_identity = verify_runtime_wheel(kit, wheel)
        stage = work / 'stage'
        stage.mkdir()
        portable = stage / 'portable'
        flags = DEFAULT_FLAGS
        existing = app / 'pystencils.json'
        if sys.platform == 'win32' and existing.is_file():
            selected = json.loads(existing.read_text(encoding='utf-8-sig')).get('compiler', {})
            if selected.get('os', 'windows') == 'windows':
                flags = selected.get('flags', flags)
        note('[1/6] Copying installed compiler, SDK and Python headers into a PRIVATE portable toolchain...')
        if sys.platform == 'win32':
            discovered = discover_windows(kit / 'tools', args.vs_path)
            metadata = stage_windows(discovered, portable, flags=flags)
            analysis_compiler = {'os': 'windows', 'msvc_version': discovered['AK_VCVARS_DIRECTORY'],
                                 'arch': 'x64', 'flags': flags, 'restrict_qualifier': '__restrict'}
        else:
            portable.mkdir()
            stage_python_sdk(portable / 'python-sdk')
            command = shutil.which('g++')
            if not command:
                raise RuntimeError('Linux proof needs g++ and development headers')
            metadata = {'platform': 'linux', 'python_sdk': 'python-sdk', 'compiler_command': command,
                        'compiler_flags': '-Ofast -DNDEBUG -fPIC -march=native -fopenmp -std=c++11',
                        'usage': 'Linux development proof, not an offline Windows distribution'}
            (portable / 'toolchain.json').write_text(json.dumps(metadata, indent=2) + '\n')
            analysis_compiler = {'os': 'linux', 'command': command, 'flags': metadata['compiler_flags'], 'restrict_qualifier': '__restrict__'}
        (portable / 'source_identity.json').write_text(json.dumps(identity, indent=2) + '\n')
        (portable / 'runtime_identity.json').write_text(json.dumps(runtime_identity, indent=2) + '\n')
        write_manifest(portable)

        note('[2/6] Preparing isolated build environment and PINNED wheels...')
        py = Path(sys.executable)
        if not args.prepared_env:
            venv = work / 'build_env'
            run([sys.executable, '-m', 'venv', venv])
            py = venv / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            house = work / 'build_wheels'
            house.mkdir()
            command = [py, '-m', 'pip', '--disable-pip-version-check', 'download', '--only-binary=:all:', '--no-deps',
                       '--timeout', '30', '--retries', '2', '--dest', house,
                       '-r', kit / 'requirements-offline-py312.txt', '-r', kit / 'tools/portable/requirements-build.txt']
            old_house = app / 'downloads' / ('wheels_windows' if sys.platform == 'win32' else 'wheels_linux')
            if old_house.is_dir():
                command += ['--find-links', old_house]
            run(command)
            shutil.copy2(wheel, house / wheel.name)
            write_manifest(house)
            run([py, '-m', 'pip', '--disable-pip-version-check', 'install', '--no-index', '--find-links', house,
                 '-r', kit / 'requirements-offline-py312.txt', '-r', kit / 'tools/portable/requirements-build.txt'])
            run([py, '-m', 'pip', 'install', '--no-index', '--no-deps', house / wheel.name])
        run([py, '-m', 'pip', 'check'])
        run([py, '-c', f"from importlib.metadata import version; assert version('pyinstaller')=='6.16.0'; assert version('aerokinetic')=={version!r}; assert version('pystencils')=='1.4'; print('Pinned build environment verified')"])
        analysis_config = work / 'analysis_pystencils.json'
        analysis_config.write_text(json.dumps({'compiler': analysis_compiler,
            'cache': {'object_cache': str(work / 'analysis_jit'), 'clear_cache_on_start': False}}, indent=2) + '\n')
        environment = dict(os.environ)
        environment.update(AK_PORTABLE_KIT=str(kit), AK_PORTABLE_STAGE=str(stage),
                           PYSTENCILS_CONFIG=str(analysis_config), PYSTENCILS_CACHE_DIR=str(work / 'analysis_symbolic'),
                           MPLCONFIGDIR=str(work / 'analysis_matplotlib'), OPENBLAS_NUM_THREADS='1',
                           PYTHONIOENCODING='utf-8')
        # Keep the executable's own package path; do not inherit a development PYTHONPATH.
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        note('[3/6] Freezing the app, sources, metadata, toolchain and offline resources...')
        run([py, '-m', 'PyInstaller', '--clean', '--noconfirm', '--distpath', work / 'dist',
             '--workpath', work / 'freeze_work', kit / 'tools/portable/AeroKinetic.spec'], environment=environment, timeout=1800)
        bundle = work / 'dist/AeroKinetic'
        executable = bundle / ('AeroKinetic.exe' if sys.platform == 'win32' else 'AeroKinetic')
        if not executable.is_file():
            raise RuntimeError('Freezer did not produce the executable')
        for name in ('LICENSE', 'README_RU.md', 'docs/PORTABLE_WINDOWS_RU.md'):
            path = kit / name
            if path.is_file():
                shutil.copy2(path, bundle / path.name)
        if (kit / 'examples').is_dir():
            shutil.copytree(kit / 'examples', bundle / 'examples')
        check_bat = ('@echo off\r\nsetlocal DisableDelayedExpansion\r\ncd /d "%~dp0"\r\n'
                     'echo Run this check on the OFFLINE target PC. Disable its network before starting.\r\n'
                     'echo This runs real cold-cache JIT and small calculations; wait several minutes.\r\n'
                     'start "" /wait "%~dp0AeroKinetic.exe" --self-test --gui\r\n'
                     'set "AK_CHECK_RESULT=%ERRORLEVEL%"\r\n'
                     'echo CHECK_EXIT_CODE=%AK_CHECK_RESULT%\r\n'
                     'echo Send the newest checks subfolder CHECK.json and AEROKINETIC_PORTABLE.log if needed.\r\n'
                     'pause\r\nexit /b %AK_CHECK_RESULT%\r\n')
        (bundle / 'CHECK_PORTABLE.bat').write_bytes(check_bat.encode('ascii'))
        (bundle / 'START_HERE_RU.txt').write_text(
            f'AeroKinetic {version}: переносимая сборка для личного использования.\n'
            'Скопируйте ВСЮ эту папку на локальный диск рабочего ПК.\n'
            'Запуск: AeroKinetic.exe. Python и Visual Studio устанавливать на целевом ПК не требуется по замыслу комплекта.\n'
            'Сначала отключите сеть и запустите CHECK_PORTABLE.bat; дождитесь кода 0.\n'
            'Проверка на чистом рабочем ПК обязательна: домашняя проверка не заменяет её.\n'
            'На целевом CPU требуется поддержка набора инструкций, указанного в compiler_flags (обычно AVX).\n'
            'Исходные результаты не изменяются; не смешивайте эту папку с прежней .venv.\n'
            'Это короткие тесты запуска, не подтверждение аэродинамической точности.\n', encoding='utf-8')
        entries = write_manifest(bundle, 'BUNDLE_SHA256.json')
        note('[4/6] Relocating the WHOLE folder to a path with spaces and Cyrillic...')
        relocated = work / 'relocation_test/Папка с пробелами/AeroKinetic'
        relocated.parent.mkdir(parents=True)
        shutil.copytree(bundle, relocated)
        for name, item in entries.items():
            if file_hash(relocated / name) != item['sha256']:
                raise RuntimeError('Relocation checksum differs: ' + name)
        note('[5/6] COLD-CACHE self-test of relocated executable; source Python/VS paths removed...')
        checks = work / 'checks'
        command = [relocated / executable.name, '--self-test', '--output', checks]
        if not args.skip_gui_proof:
            command.append('--gui')
        try:
            run(command, environment=stripped_environment(), timeout=1200)
        finally:
            # A windowed Windows EXE has no stdout, even when redirected.
            # Keep its actual error with the single log requested from users.
            for name in ('AEROKINETIC_PORTABLE.log', 'CHECK.json'):
                path = checks / name
                if path.is_file():
                    log.write('\nFROZEN SELF-TEST ' + name + ':\n')
                    log.write(path.read_text(encoding='utf-8', errors='replace')[-512000:] + '\n')
        evidence = json.loads((checks / 'CHECK.json').read_text())
        if evidence.get('status') != 'passed' or not evidence.get('frozen'):
            raise RuntimeError('Frozen self-test did not pass')
        note('[6/6] Saving build report and exact folder to transfer...')
        result.update(status='build_checks_passed_target_offline_test_pending', version=version,
                      target='Windows x64' if sys.platform == 'win32' else 'Linux development proof ONLY',
                      ready_to_copy=str(bundle), executable=str(executable),
                      self_test=str(checks / 'CHECK.json'), bundle_files=len(entries),
                      bundle_bytes=sum(item['bytes'] for item in entries.values()),
                      compiler_metadata=metadata, runtime_wheel_sha256=file_hash(wheel),
                      relocated_frozen_self_test='passed', gui=evidence['gui'],
                      clean_target_offline_test='not_run', elapsed_seconds=time.monotonic() - started)
        (app / 'PORTABLE_READY_TO_COPY.txt').write_text('Copy this WHOLE folder:\n' + str(bundle) + '\n\nRun CHECK_PORTABLE.bat on the offline target first.\n', encoding='utf-8')
        note('BUILD CHECKS PASSED. Copy the WHOLE folder: ' + str(bundle))
        note('The offline target test is still required. See PORTABLE_READY_TO_COPY.txt.')
        return 0
    except Exception as error:
        result.update(error=str(error), traceback=traceback.format_exc())
        log.write(result['traceback'] + '\n')
        note('BUILD FAILED: ' + str(error))
        pointer.write_text('This build failed. No new bundle is accepted.\nLog: ' + str(log_path) + '\n', encoding='utf-8')
        note('Send PORTABLE_BUILD_LAST.log from the original app folder.')
        return 1
    finally:
        (work / 'BUILD_CHECK.json').write_text(json.dumps(result, indent=2) + '\n')
        log.close()
        shutil.copy2(log_path, previous_log)


if __name__ == '__main__':
    raise SystemExit(main())
