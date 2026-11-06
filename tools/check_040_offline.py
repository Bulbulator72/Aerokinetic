"""Installed-wheel 0.4 checks, fresh JIT including OpenMP, no Python network."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sys
import time
import xml.etree.ElementTree as ET
import zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--after-tests',action='store_true',help='Continue numerical stages only after a validated all-passing JUnit')
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=args.after_tests)
    config=out/'pystencils.json'
    config.write_text(json.dumps({'cache':{'object_cache':str(out/'compiled_cache'),'clear_cache_on_start':False}}))
    os.environ['PYSTENCILS_CONFIG']=str(config);os.environ['PYSTENCILS_CACHE_DIR']=str(out/'symbolic_cache')
    os.environ['MPLCONFIGDIR']=str(out/'matplotlib_cache')
    attempts=[]
    def denied(*a,**kw):
        attempts.append('Python network operation')
        raise AssertionError('Network forbidden during 0.4 verification')
    socket.create_connection=socket.socket.connect=socket.socket.connect_ex=denied
    socket.getaddrinfo=socket.gethostbyname=socket.gethostbyname_ex=denied
    import aerokinetic
    from aerokinetic import RefinedSolver
    import pytest
    import numpy as np
    assert aerokinetic.__version__=='0.4.0'
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    root=Path(__file__).resolve().parents[1];started=time.monotonic()
    print('Installed package:',aerokinetic.__file__,flush=True)
    files=['test_geometry_and_units.py','test_case_planning.py','test_temporal.py','test_experiments.py',
           'test_convergence.py','test_mesh.py','test_parallel.py','test_interface.py','test_run_controls.py']
    tests=[str(root/'tests'/name) for name in files]
    tests += [str(root/'tests/test_refined_airfoil.py')+'::test_fractional_bouzidi_flat_wall_sine_decay',
              str(root/'tests/test_numerics.py')+'::test_cumulant_shear_wave_viscosity_and_conservation']
    if args.after_tests:
        suites=list(ET.parse(out/'tests.xml').getroot().iter('testsuite'))
        assert sum(int(s.attrib['tests']) for s in suites)>=62
        assert all(int(s.attrib['failures'])==0 and int(s.attrib['errors'])==0 for s in suites)
        print('All-passing test evidence loaded; continuing numerical stages',flush=True)
    else:
        status=pytest.main([*tests,'-q','-o','pythonpath=',f'--junitxml={out / "tests.xml"}'])
        if status:raise SystemExit(status)
    print('Threaded/fused interface regression: analytic transport in all directions',flush=True)
    from aerokinetic.interface_checks import write_checks
    from aerokinetic.parallel import ParallelRefinedBox
    write_checks(out/'interface',plot=True,box_factory=ParallelRefinedBox)
    interface=json.loads((out/'interface/checks.json').read_text())
    assert interface['status']=='passed'
    print('NACA0012 Re=30000, alpha=5, two threads, fixed t*=0.2; startup only',flush=True)
    from aerokinetic.refined_solver import load_refined_project
    project,options=load_refined_project(root/'examples/naca0012_refined_re30000_preview.json')
    project=replace(project,grid=replace(project.grid,openmp_threads=2))
    solver=RefinedSolver(project,options)
    samples=[]
    def callback(solver,record):
        samples.append(record)
        print(f"t*={record['t_star']:.5f}; Cl={record['cl']:.8g}",flush=True)
    summary=solver.run(out/'airfoil',callback=callback)
    assert summary['status']=='completed' and summary['termination_reason']=='duration_reached'
    assert summary['last_residuals'] and not summary['steady_residual_criterion_met']
    previous=json.loads((root/'docs/verification_refined_airfoil/startup_summary.json').read_text())
    differences={key:abs(summary['mean'][key]-previous['mean'][key]) for key in ('cl','cd','cm')}
    assert max(differences.values())<=1e-10,differences
    from aerokinetic.animation import animate
    from aerokinetic.experiments import compare_run
    from aerokinetic.temporal import write_analysis
    from PIL import Image
    animation=animate(out/'airfoil',field='speed')
    with Image.open(animation) as gif:assert gif.n_frames>=2
    comparison=compare_run(out/'airfoil','ohtake_re30000')
    assert comparison['validation']=='inconclusive'
    (out/'airfoil/experiment_ohtake_re30000.json').write_text(json.dumps(comparison,indent=2)+'\n')
    folder,analysis=write_analysis(out/'airfoil')
    with zipfile.ZipFile(folder/'FOR_REVIEW.zip') as archive:
        assert archive.read('run/residuals.csv')==(out/'airfoil/residuals.csv').read_bytes()
    assert not attempts
    try:
        import resource
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/2**20 if sys.platform=='linux' else None
    except ImportError:peak=None
    suites=list(ET.parse(out/'tests.xml').getroot().iter('testsuite'))
    result={'status':'passed','version':aerokinetic.__version__,'installed_package':aerokinetic.__file__,
            'tests_passed':sum(int(s.attrib['tests']) for s in suites),'fresh_jit':True,
            'continued_after_test_stage':args.after_tests,
            'python_connections_and_dns':'blocked','network_attempts':len(attempts),
            'openmp_threads_tested':[1,2],'optimized_interface':interface['status'],
            'actual_airfoil':summary,'mean_difference_from_030_startup':differences,
            'residual_csv_in_review_archive':True,'preliminary_experiment':comparison,
            'numerical_aerodynamic_convergence':'not_established','aerodynamic_validation':'inconclusive',
            'linux_process_peak_rss_gib':peak,'elapsed_seconds':time.monotonic()-started,
            'windows_msvc':'not_run','gpu':'not_run'}
    (out/'OFFLINE_CHECK.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
