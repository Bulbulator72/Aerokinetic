"""Destructive only inside the dedicated clean_app test installation.

Run after manually restoring that isolated installation to 0.2.0.
Exercise the actual delivered payload and a deliberately interrupted update.
"""
import base64
import hashlib
import importlib.util
from importlib.metadata import version
import io
import json
from pathlib import Path
import re
import sys
import zipfile


def main():
    release = Path('/workspace/aerokinetic_021_release')
    root = release / 'clean_app'
    assert Path(sys.prefix).resolve() == root / '.venv'
    assert version('aerokinetic') == '0.2.0'
    raw = Path('/workspace/DOWNLOAD_AEROKINETIC/UPDATE_AEROKINETIC.txt').read_bytes()
    m = re.search(rb'(?ms)^AEROKINETIC_UPDATE_BEGIN\r?\n(?P<data>.*?)^AEROKINETIC_UPDATE_END\r?$', raw)
    payload = base64.b64decode(re.sub(rb'\s+', b'', m['data']), validate=True)
    case = root / 'saved_user_run'
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob('*') if p.is_file()}
    offline_manifest = root / 'downloads/wheels_windows/SHA256.json'
    offline_before = offline_manifest.read_bytes()
    bundle = root / 'updates/failure_transaction_final'
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(bundle)
    spec = importlib.util.spec_from_file_location('verify_aerokinetic_update', bundle / 'install_update.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wheel = bundle / 'aerokinetic-0.2.1-py3-none-any.whl'
    pristine = wheel.read_bytes()
    wheel.write_bytes(pristine[:-1] + bytes([pristine[-1] ^ 1]))
    try:
        module.install(root, bundle)
    except ValueError as error:
        assert 'Damaged update file' in str(error)
    else:
        raise AssertionError('Damaged wheel was not rejected')
    assert not (bundle / 'backup').exists()
    assert version('aerokinetic') == '0.2.0'
    wheel.write_bytes(pristine)
    original_readme = b'Original local documentation; restore exactly.\n'
    (root / 'README_RU.md').write_bytes(original_readme)
    actual_copy = module.shutil.copy2
    def interrupted_copy(source, target, *args, **kwargs):
        result = actual_copy(source, target, *args, **kwargs)
        if Path(source) == bundle / 'source/README_RU.md':
            # Simulate a failure after pip installed the new runtime and after
            # a pre-existing source/document file was actually replaced.
            raise OSError('Deliberate interruption while copying updated sources')
        return result
    module.shutil.copy2 = interrupted_copy
    try:
        module.install(root, bundle)
    except OSError as error:
        assert 'Deliberate interruption' in str(error)
    else:
        raise AssertionError('Interrupted update did not stop')
    finally:
        module.shutil.copy2 = actual_copy
    assert version('aerokinetic') == '0.2.0'
    assert (root / 'README_RU.md').read_bytes() == original_readme
    assert offline_manifest.read_bytes() == offline_before
    _, purelib = module.check_target(root)
    with zipfile.ZipFile('/workspace/aerokinetic_02_release/dist/aerokinetic-0.2.0-py3-none-any.whl') as old:
        for name in old.namelist():
            if name.startswith('aerokinetic/'):
                assert (purelib / name).read_bytes() == old.read(name), name
    final = root / 'updates/final_transport_final'
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(final)
    status = module.install(root, final)
    assert version('aerokinetic') == '0.2.1'
    offline = json.loads(offline_manifest.read_text())
    for item in offline['files']:
        data = (offline_manifest.parent / item['file']).read_bytes()
        assert len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256']
    assert any(item['file'] == wheel.name for item in offline['files'])
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())
    with zipfile.ZipFile(io.BytesIO(pristine)) as new:
        for name in new.namelist():
            if name.startswith('aerokinetic/'):
                assert (purelib / name).read_bytes() == new.read(name), name
    checks = {'status': 'passed', 'decoded_actual_delivered_txt': True,
              'txt_sha256': hashlib.sha256(raw).hexdigest(),
              'old_to_new_offline_install': status,
              'manual_backup_restore_to_020': 'passed_before_this_script',
              'corrupt_wheel_rejected_before_install': True,
              'deliberate_failure_after_runtime_and_source_replacement': 'restored_original_020',
              'restored_runtime_bytes_match_original_020_wheel': True,
              'restored_local_document_bytes': True, 'all_original_case_bytes_preserved': True,
              'final_runtime_bytes_match_021_wheel': True,
              'prepared_windows_wheelhouse_all_files_and_sha256': 'passed',
              'windows_bat_execution': 'not_run'}
    (release / 'UPDATE_CHECK.json').write_text(json.dumps(checks, indent=2) + '\n')
    print(json.dumps(checks, indent=2))


if __name__ == '__main__':
    main()
