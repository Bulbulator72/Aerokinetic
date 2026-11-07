"""Installed-wheel cold-cache offline smoke, continuation CLI and animation."""
from dataclasses import asdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    work=args.output.resolve()
    work.mkdir(exist_ok=False)
    config=work/'pystencils.json';config.write_text(json.dumps({'cache':{'object_cache':str(work/'jit'),'clear_cache_on_start':False}}))
    os.environ.update(PYSTENCILS_CONFIG=str(config),PYSTENCILS_CACHE_DIR=str(work/'symbolic'),
        XDG_CACHE_HOME=str(work/'cache'),MPLCONFIGDIR=str(work/'matplotlib'),MPLBACKEND='Agg')
    attempts=[]
    def deny(*a,**k):
        attempts.append('Python connection/DNS');raise RuntimeError('Network disabled in offline smoke')
    socket.create_connection=socket.socket.connect=socket.socket.connect_ex=deny
    socket.getaddrinfo=socket.gethostbyname=socket.gethostbyname_ex=deny
    import aerokinetic
    from aerokinetic import Solver,Section,naca0012,Flow,Grid,RunSettings
    from aerokinetic.cli import main as cli
    from aerokinetic.animation import animate
    assert aerokinetic.__version__=='0.5.1'
    assert Path(aerokinetic.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    s=Solver(Section((naca0012(31),)),Flow(reynolds=30000,alpha_deg=4,physical_chord_m=.15),
       Grid(cells_per_chord=16,upstream_chords=1,downstream_chords=2,transverse_chords=1,memory_limit_gib=1))
    dt=s.plan.dt_convective
    run=RunSettings(duration_convective=8*dt,sample_every=2,frame_every=4,check_every=4,average_after=0,checkpoint_every=4)
    result=s.run(output=work/'parent',**asdict(run))
    cli(['continue',str(work/'parent'),'--output',str(work/'child'),'--additional-time',str(7*dt)])
    child=json.loads((work/'child/summary.json').read_text())
    assert result['status']=='completed'and child['steps']==15
    gif=animate(work/'child',work/'speed.gif','speed')
    assert Path(gif).stat().st_size>100
    assert not attempts
    (work/'CHECKS.json').write_text(json.dumps({'installed':aerokinetic.__file__,'version':aerokinetic.__version__,
        'clean_environment':True,'cold_cache_jit':True,'python_network_attempts':attempts,
        'parent_steps':8,'requested_additional_steps':7,'child_steps':15,
        'cli_continuation':'passed','gif_bytes':Path(gif).stat().st_size,
        'windows':'not_run','gpu':'not_run'},indent=2)+'\n')
    print('COLD OFFLINE SMOKE PASSED',flush=True)


if __name__=='__main__':main()
