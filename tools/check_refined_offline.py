"""Verify the INSTALLED two-level airfoil application, with Python network blocked.

Populate a clean venv from the pinned local wheels before invoking this script.
No downloaded data is needed. The short tunnel comparison is inconclusive for
aerodynamic accuracy, regardless of its coefficient difference.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import xml.etree.ElementTree as ET


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    config=output/'pystencils.json'
    config.write_text(json.dumps({'cache':{'object_cache':str(output/'compiled_cache'),
                                          'clear_cache_on_start':False}}))
    os.environ['PYSTENCILS_CONFIG']=str(config)
    os.environ['PYSTENCILS_CACHE_DIR']=str(output/'joblib_cache')
    os.environ['MPLCONFIGDIR']=str(output/'matplotlib_cache')
    attempts=[]
    def denied(*args,**kwargs):
        attempts.append('Python network operation')
        raise AssertionError('Network forbidden during installed-wheel verification')
    socket.create_connection=denied
    socket.socket.connect=socket.socket.connect_ex=denied
    socket.getaddrinfo=socket.gethostbyname=socket.gethostbyname_ex=denied
    import aerokinetic
    from aerokinetic import RefinedSolver, RefinedPlan, RefinementSettings
    from aerokinetic.refined_solver import load_refined_project
    import pytest
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert aerokinetic.__version__=='0.3.0'
    root=Path(__file__).resolve().parents[1]
    print('Installed package:',aerokinetic.__file__,flush=True)
    started=time.monotonic()
    result=pytest.main([str(root/'tests'),'-q','-o','pythonpath=',
                       f'--junitxml={output / "tests.xml"}'])
    if result:raise SystemExit(result)
    from aerokinetic.interface_checks import write_checks
    print('Periodic interface regression, all transport cases',flush=True)
    write_checks(output/'interface',plot=True)
    # The saved file is also the CLI verification result.
    interface=json.loads((output/'interface/checks.json').read_text())
    assert interface['status']=='passed'
    project,options=load_refined_project(root/'examples/naca0012_refined_re30000_preview.json')
    print('Actual NACA0012 Re=30000, alpha=5, short startup t*=0.2',flush=True)
    run_output=output/'airfoil'
    solver=RefinedSolver(project,options,progress=lambda message:print(message,flush=True))
    budget={'max_abs_mass_residual_per_volume':0.,'max_abs_momentum_residual_per_volume':0.}
    def sample(solver,record):
        budget['max_abs_mass_residual_per_volume']=max(budget['max_abs_mass_residual_per_volume'],
                                                    abs(solver.last_budget['mass_residual_per_volume']))
        budget['max_abs_momentum_residual_per_volume']=max(budget['max_abs_momentum_residual_per_volume'],
                                         *map(abs,solver.last_budget['momentum_residual_per_volume']))
        print(f"t*={record['t_star']:.5f} Cl={record['cl']:.7g} Cd={record['cd']:.7g} Cm={record['cm']:.7g}",flush=True)
    summary=solver.run(run_output,callback=sample)
    assert summary['status']=='completed'
    assert budget['max_abs_mass_residual_per_volume']<=5e-11
    assert budget['max_abs_momentum_residual_per_volume']<=2e-12
    from aerokinetic.animation import animate
    from aerokinetic.experiments import compare_run
    from aerokinetic.temporal import write_analysis
    from PIL import Image
    gif=animate(run_output,field='speed')
    with Image.open(gif) as image:assert image.n_frames>=2
    comparison=compare_run(run_output,'ohtake_re30000')
    assert comparison['validation']=='inconclusive'
    (run_output/'experiment_ohtake_re30000.json').write_text(json.dumps(comparison,indent=2)+'\n')
    write_analysis(run_output)
    assert not attempts
    suites=list(ET.parse(output/'tests.xml').getroot().iter('testsuite'))
    try:
        import resource
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/2**20 if sys.platform=='linux' else None
    except ImportError:
        peak=None
    result={'status':'passed','version':aerokinetic.__version__,
            'installed_package':aerokinetic.__file__,
            'tests_passed':sum(int(s.attrib['tests']) for s in suites),
            'fresh_jit_cache':True,'python_socket_connections_and_dns':'blocked',
            'network_attempt_count':len(attempts),'interface_regression':interface['status'],
            'actual_airfoil':summary,'sampled_budget_maxima':budget,
            'preliminary_wind_tunnel_comparison':comparison,
            'numerical_aerodynamic_convergence':'not_established',
            'aerodynamic_validation':'inconclusive',
            'linux_cpu_peak_rss_gib':peak,
            'elapsed_seconds':time.monotonic()-started,'windows_msvc':'not_run','gpu':'not_run'}
    (output/'OFFLINE_CHECK.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
