"""Fresh-process replay against the continuing parent CSV, from frozen state."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import time

from aerokinetic import Project
from airfoil_pilot import AirfoilPilot


def main():
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);started=time.perf_counter()
    s=AirfoilPilot(Project.load(a.parent/'input_project.json'));s.restore_research_checkpoint(a.checkpoint)
    first=s.steps+1;last=s.steps+160;rows=[]
    for step in range(first,last+1):
        load,_=s.step(sample=step%8==0)
        if step%8==0:rows.append({k:load[k]for k in('step','t_star','time_s','cl','cd','cm')})
    with(a.output/'replay.csv').open('w',newline='')as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    diagnostic=s.diagnostics()
    with(a.parent/'history.csv').open()as f:reference={int(r['step']):r for r in csv.DictReader(f)}
    if not all(r['step']in reference for r in rows):raise RuntimeError('Parent has not yet completed the replay interval; retain replay and compare later')
    error=max(abs(r[k]-float(reference[r['step']][k]))for r in rows for k in('t_star','time_s','cl','cd','cm'))
    parent_diagnostics=json.loads((a.parent/'diagnostics.json').read_text())
    target=[r for r in parent_diagnostics if r['step']==last]
    if not target:raise RuntimeError('Parent diagnostic at the fixed replay endpoint is not available yet')
    diagnostic_error=max(abs(diagnostic[k]-target[0][k])for k in diagnostic)
    result={'first_replayed_step':first,'last_replayed_step':last,'samples':len(rows),
        'force_and_clock_max_error':error,'diagnostic_max_error':diagnostic_error,'passed':error==0 and diagnostic_error==0,
        'checkpoint_sha256':hashlib.sha256(a.checkpoint.read_bytes()).hexdigest(),
        'identity':s.identity(),'elapsed_s':time.perf_counter()-started,
        'limits':'Fresh interpreter/instance replay of160steps against uninterrupted parent forces and fluid diagnostics. Full-PDF exact equality additionally tested by separate same-instance preflight.'}
    (a.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
