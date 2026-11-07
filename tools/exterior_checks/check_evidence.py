"""Verify archived hashes, source identity and raw numerical acceptance gates.

This checks the evidence packet. It does not rerun LBM or certify accuracy.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def rows(p):
    with p.open(newline='')as f:return list(csv.DictReader(f))


def verify(project,require_airfoil=False):
    docs=project/'docs';report=docs/'verification_exterior_fd_20261107'
    checked=0
    for name in('verification_exterior_fd_20261107','verification_moment_boundary_20261107'):
        base=docs/name
        manifest=read(base/'FILES_SHA256.json')['files']
        actual={p.relative_to(base).as_posix()for p in base.rglob('*')if p.is_file()and p.name!='FILES_SHA256.json'}
        assert actual=={r['path']for r in manifest},f'{name}: incomplete file manifest'
        for record in manifest:
            file=(base/record['path']).resolve();file.relative_to(base.resolve())
            assert file.is_file()and file.stat().st_size==record['bytes']and sha(file)==record['sha256'],str(file)
            checked+=1
    context=read(report/'RUN_CONTEXT.json');tools=project/'tools/exterior_checks'
    for name,digest in context['prototype_sources'].items():assert sha(tools/name)==digest,name
    source=sha(tools/'exterior_fd.py');adapter=sha(tools/'airfoil_pilot.py')
    statuses=read(report/'CHECKS.json')['groups']
    completed=[]
    for name,status in statuses.items():
        if status['status']!='passed':
            if require_airfoil:raise AssertionError(f'{name}: {status["status"]}')
            continue
        result=read(report/status['checks_file'])
        assert result.get('passed',result.get('completion_gate_passed'))is True,name
        if 'source_sha256'in result:
            # Preflight was before an int-only shape metadata correction; the
            # exact historical source is retained and repeated noise CSVs match.
            expected=sha(report/'provenance/exterior_fd_before_metadata_fix.py')if name=='preflight'else source
            assert result['source_sha256']==expected,name
        if 'identity'in result:
            assert result['identity']['exterior_source_sha256']==source,name
            assert result['identity']['adapter_source_sha256']==adapter,name
            assert result['identity']['storage']=='pre_collision',name
        completed.append(name)
    for name in('noise_straight_final','noise_rectangle_final'):
        data=rows(report/f'raw/{name}/history.csv')
        assert [int(r['step'])for r in data]==list(range(0,10001,100)),name
        assert max(float(r[k])for r in data for k in('core_energy_over_initial','combined_energy_over_initial'))<=1.1,name
        result=read(report/f'raw/{name}/CHECKS.json')
        assert result['initial_noise_sha256']=='8cffaab76d458180be99119e04a236fb8809e17707d3dbea7e8dfa625759f5f2',name
        assert result['max_core_overwrite']==0,name
    for name in('glancing_w16','glancing_w8','glancing_w4','glancing_3d'):
        data=rows(report/f'raw/{name}/history.csv')
        assert int(data[-1]['step'])==10000,name
        assert max(float(r[k])for r in data for k in('core_energy_over_initial','combined_energy_over_initial'))<=1.1,name
        assert max(float(r['core_density_max_error'])for r in data)<=2e-6,name
    for name in('vortex_corner','vortex_seam'):
        data=rows(report/f'raw/{name}/history.csv')
        assert int(data[-1]['step'])==3000,name
        assert max(float(r['density_max_error'])for r in data)<=2e-6,name
        assert max(float(r['velocity_l2_error_over_initial'])for r in data)<=.1,name
        assert float(data[-1]['velocity_l2_error_over_initial'])<=.05,name
    for name in('spectral_23_2','spectral_23_2_half','spectral_23_2_nx48'):
        result=read(report/f'raw/{name}/CHECKS.json')
        assert result['spectral_radius']<=1+1e-6 and result['modal_map_relative_error']<=2e-5,name
    for name in('airfoil_down3','airfoil_down6'):
        if statuses[name]['status']!='passed':continue
        result=read(report/f'raw/{name}/CHECKS.json');data=rows(report/f'raw/{name}/history.csv')
        assert result['steps']==9600 and result['t_star']==12,name
        assert [int(r['step'])for r in data]==list(range(8,9601,8)),name
        window=[r for r in data if 6<=float(r['t_star'])<=12];assert len(window)==600,name
        for k in('cl','cd','cm'):
            mean=sum(float(r[k])for r in window)/len(window)
            assert abs(mean-result['window6_12']['mean'][k])<=2e-14,(name,k)
    comparison=report/'comparison/CHECKS.json'
    if comparison.exists():
        data=read(comparison)
        assert all(data[k]for k in('matched_physical_inputs','complete_fixed_windows','means_match_run_checkpoints'))
        assert data['source_sha256']==sha(tools/'analyze_airfoil.py')
        assert data['experimental_validation']=='inconclusive'
    return {'packet_integrity':'passed','hashed_files':checked,'completed_groups':completed,
        'prototype_source_sha256':source,'experimental_validation':'inconclusive'if comparison.exists()else'not_run',
        'limits':'Evidence integrity and stored gate recomputation. No new CFD run or physical accuracy certification.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--require-airfoil',action='store_true');a=p.parse_args()
    print(json.dumps(verify(a.project_root,a.require_airfoil),indent=2))


if __name__=='__main__':main()
