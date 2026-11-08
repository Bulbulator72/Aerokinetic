"""Independent full-box flux plus explicit exterior-source budget observer."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from exterior_fd import ExteriorFD

def totals(m):
    g=m.s.data_handling.cpu_arrays[m.s.pdf_array_name][1:-1,1:-1,1:-1]
    return np.r_[np.prod(m.shape)+g.sum(),np.einsum('...q,qa->a',g,m.c,optimize=True)]

def flux(m):
    f=m.s.data_handling.cpu_arrays[m.s.pdf_array_name];g=f[1:-1,1:-1,1:-1];shape=m.shape
    p=np.ogrid[tuple(slice(0,n)for n in shape)];answer=np.zeros(4)
    for q,c in enumerate(m.c):
        enter=np.zeros(shape,dtype=bool);leave=enter.copy()
        for a,periodic in enumerate(m.periodic):
            if not periodic:
                leave|=(p[a]+c[a]<0)|(p[a]+c[a]>=shape[a])
                enter|=(p[a]-c[a]<0)|(p[a]-c[a]>=shape[a])
        incoming=f[tuple(slice(1-c[a],shape[a]+1-c[a])for a in range(3))+(q,)]+m.weights[q]
        outgoing=g[...,q]+m.weights[q];dm=incoming[enter].sum()-outgoing[leave].sum()
        answer+=dm*np.r_[1.,c]
    return answer

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(exist_ok=False)
    m=ExteriorFD((8,256,8),(True,False,True))
    def pulse(block,coords):
        _,y,_=coords;dr=2e-5*np.exp(-.5*((y-180)/10)**2)
        block[m.s.density_data_name][...]=1+dr;block[m.s.velocity_data_name][...,1]=np.sqrt(1/3)*dr
    m.initialize(pulse);dh=m.s.data_handling;original=dh.run_kernel;capture={};audit=False;records=[]
    def observer(kernel,**kwargs):
        if audit and kernel is m.s._lbmKernels[1]:capture['flux']=flux(m)
        return original(kernel,**kwargs)
    dh.run_kernel=observer
    try:
        for i in range(1,361):
            audit=i<=12 or i in (100,130,160,200,250,300,360)
            before=totals(m)if audit else None;r=m.step(audit=audit)
            if audit:
                residual=(totals(m)-before-capture['flux']-np.array(r['exterior_source_mass_momentum']))/np.prod(m.shape)
                r['augmented_budget_per_cell']=residual.tolist();records.append(r)
    finally:dh.run_kernel=original
    core=max(abs(v)for r in records for v in r['core_budget_per_cell']);augmented=max(abs(v)for r in records for v in r['augmented_budget_per_cell'])
    edits=max(r['core_pdf_overwrite_max']for r in records)
    result={'records':records,'max_core_budget_per_cell':core,'max_augmented_budget_with_source_per_cell':augmented,'max_core_pdf_overwrite':edits,
            'passed':max(core,augmented)<=2e-12 and edits==0,'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest()}
    (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n');print({k:v for k,v in result.items()if k!='records'},flush=True)

if __name__=='__main__':main()
