"""Test the actual TXT payload and rollback in an isolated old installation.

Never point this at a user's working program. The test creates synthetic user
files only in /workspace/aerokinetic_040_release/update_app.
"""
import argparse
import base64
import hashlib
import importlib.util
from importlib.metadata import version
import io
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload',type=Path)
    parser.add_argument('--tag',default='final')
    args=parser.parse_args()
    release=Path('/workspace/aerokinetic_040_release')
    root=release/'update_app'
    assert Path(sys.prefix).resolve()==root/'.venv'
    assert version('aerokinetic')=='0.3.0'
    raw=args.payload.read_bytes()
    match=re.search(rb'(?ms)^AEROKINETIC_UPDATE_BEGIN\r?\n(?P<data>.*?)^AEROKINETIC_UPDATE_END\r?$',raw)
    payload=base64.b64decode(re.sub(rb'\s+',b'',match['data']),validate=True)
    case=root/'saved_user_result'
    case.mkdir(exist_ok=True)
    (case/'history.csv').write_bytes(b'step,t_star,cl,cd,cm\n1,1.0,0.25,0.01,-0.02\n')
    preserved={p:digest(p) for p in case.rglob('*') if p.is_file()}
    house=root/'downloads/wheels_windows'
    if not house.exists():shutil.copytree('/workspace/aerokinetic_02_release/wheels_windows',house)
    original_manifest=(house/'SHA256.json').read_bytes()
    original_readme=b'Original local README, preserved on rollback.\n'
    (root/'README_RU.md').write_bytes(original_readme)
    def unpack(name):
        folder=root/'updates'/f'{args.tag}_{name}'
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert archive.testzip() is None
            archive.extractall(folder)
        spec=importlib.util.spec_from_file_location('aerokinetic_update_test',folder/'install_update.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return folder,module
    failed,module=unpack('fault_injection')
    wheel=failed/'aerokinetic-0.4.0-py3-none-any.whl'
    pristine=wheel.read_bytes()
    wheel.write_bytes(pristine[:-1]+bytes([pristine[-1]^1]))
    try:module.install(root,failed)
    except ValueError as error:assert 'Damaged update file' in str(error)
    else:raise AssertionError('Corrupt wheel accepted')
    assert not (failed/'backup').exists()
    wheel.write_bytes(pristine)
    actual_copy=module.shutil.copy2
    def interrupted(source,target,*a,**kw):
        result=actual_copy(source,target,*a,**kw)
        if Path(source)==failed/'source/README_RU.md':raise OSError('Deliberate source-copy interruption')
        return result
    module.shutil.copy2=interrupted
    try:module.install(root,failed)
    except OSError as error:assert 'Deliberate' in str(error)
    else:raise AssertionError('Failure injection did not run')
    finally:module.shutil.copy2=actual_copy
    assert version('aerokinetic')=='0.3.0'
    assert (root/'README_RU.md').read_bytes()==original_readme
    assert (house/'SHA256.json').read_bytes()==original_manifest
    _,purelib=module.check_target(root)
    with zipfile.ZipFile('/workspace/aerokinetic_030_release/final_dist/aerokinetic-0.3.0-py3-none-any.whl') as old:
        for name in old.namelist():
            if name.startswith('aerokinetic/'):assert (purelib/name).read_bytes()==old.read(name)
    final,module=unpack('success')
    status=module.install(root,final)
    assert version('aerokinetic')=='0.4.0'
    with zipfile.ZipFile(io.BytesIO(pristine)) as new:
        for name in new.namelist():
            if name.startswith('aerokinetic/'):assert (purelib/name).read_bytes()==new.read(name)
    for entry in json.loads((house/'SHA256.json').read_text())['files']:
        path=house/entry['file']
        assert path.stat().st_size==entry['bytes'] and digest(path)==entry['sha256']
    assert all(digest(path)==expected for path,expected in preserved.items())
    result={'status':'passed','actual_txt_payload':str(args.payload),'txt_sha256':digest(args.payload),
            'corrupt_wheel_rejected_before_install':True,'interrupted_runtime_and_source_copy_rollback':'passed',
            'restored_runtime_matches_030_wheel':True,'original_readme_restored':True,
            'final_runtime_matches_040_wheel':True,'preserved_user_files':True,
            'prepared_windows_wheelhouse_all_hashes':'passed','install':status,
            'linux_python_transaction':'passed','windows_bat_execution':'not_run'}
    (release/f'UPDATE_CHECK_{args.tag}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
