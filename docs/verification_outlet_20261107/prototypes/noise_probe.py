"""Fixed no-body diagnostic: identical linear noise, different outer topology."""
import argparse,csv,hashlib,json,sys,time
from pathlib import Path
import numpy as np
sys.path.insert(0,'/workspace/aerokinetic/tools')
from farfield_checks.preflight import lattice,initialize,macros
from aerokinetic.farfield import install_characteristic_farfield
import aerokinetic


def main():
 parser=argparse.ArgumentParser();parser.add_argument('case',choices=('periodic_control','straight_outlet','rectangle'));parser.add_argument('--output',type=Path,required=True);parser.add_argument('--candidate',choices=('original','euler','ab2','ab3','constant','regularized','convective','copy'),default='original');args=parser.parse_args()
 import importlib
 module=importlib.import_module('aerokinetic.farfield'if args.candidate=='original'else'boundary_'+args.candidate)
 args.output.mkdir(parents=True,exist_ok=False)
 shape=(96,48,8);periodic={'periodic_control':(True,True,True),'straight_outlet':(False,True,True),'rectangle':(False,False,True)}[args.case]
 base=np.array((.04,0,0));rng=np.random.default_rng(3107)
 dr=rng.normal(0,1e-8,shape);dr-=dr.mean();du=rng.normal(0,1e-8,shape+(3,));du-=du.mean(axis=(0,1,2))
 initial_sha=hashlib.sha256(dr.tobytes()+du.tobytes()).hexdigest()
 s=lattice(shape,periodic)
 for name in (s.pdf_array_name,s._tmp_arr_name):s.data_handling.cpu_arrays[name].fill(0)
 if not all(periodic):module.install_characteristic_farfield(s,shape,periodic,base)
 initialize(s)
 s.data_handling.cpu_arrays[s.density_data_name][1:-1,1:-1,1:-1]=1+dr
 s.data_handling.cpu_arrays[s.velocity_data_name][1:-1,1:-1,1:-1]=base+du
 s.set_pdf_fields_from_macroscopic_values()
 rows=[];energy0=None;started=time.perf_counter();finite=True;last=0
 ix,iy=np.ogrid[:shape[0],:shape[1]]
 corner=(ix>=shape[0]-6)&((iy<6)|(iy>=shape[1]-6));middle=(ix>=shape[0]-6)&(iy>=6)&(iy<shape[1]-6)
 regions={'outlet_corner':corner,'outlet_middle':middle,'side':(ix>=6)&(ix<shape[0]-6)&((iy<6)|(iy>=shape[1]-6)), 'core':(ix>=6)&(ix<shape[0]-6)&(iy>=6)&(iy<shape[1]-6)}
 for step in range(10001):
  if step%100==0:
   rho,v=macros(s);finite=bool(np.isfinite(rho).all()and np.isfinite(v).all());last=step
   if not finite:break
   energy=np.sum((v-base)**2,axis=-1)+(rho-1)**2/3
   if energy0 is None:energy0=float(energy.mean())
   index=np.unravel_index(np.argmax(energy),shape)
   row={'step':step,'energy_over_initial':float(energy.mean()/energy0),'max_density_error':float(abs(rho-1).max()),'max_velocity_error':float(abs(v-base).max()),'max_mach':float(np.sqrt(3)*np.linalg.norm(v,axis=-1).max()),'peak_x':int(index[0]),'peak_y':int(index[1]),'peak_z':int(index[2])}
   for name,mask in regions.items():row[name+'_energy_over_initial']=float(energy[np.broadcast_to(mask[...,None],shape)].mean()/energy0)
   rows.append(row)
   if step%1000==0:print(args.case,step,row['energy_over_initial'],index,flush=True)
   if row['energy_over_initial']>10000 or row['max_density_error']>.05 or row['max_mach']>.3:break
  if step<10000:s.time_step()
 with(args.output/'history.csv').open('w',newline='')as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 spectrum=np.abs(np.fft.fft2(v-base,axes=(1,2)))**2
 spectrum=spectrum.sum(axis=(0,3))+(np.abs(np.fft.fft2(rho-1,axes=(1,2)))**2).sum(axis=0)/3
 with(args.output/'terminal_transverse_spectrum.csv').open('w',newline='')as f:
  w=csv.writer(f);w.writerow(('integer_mode_y','integer_mode_z','normalized_energy'))
  w.writerows((iy,iz,float(spectrum[iy,iz]/spectrum.sum()))for iy in range(shape[1])for iz in range(shape[2]))
 boundaries={}
 if not all(periodic):
  s.boundary_handling.prepare()
  for block in s.data_handling.iterate(ghost_layers=1):
   for boundary,a in block[s.boundary_handling._index_array_name].boundary_object_to_index_list.items():
    boundaries[boundary.name]={key:[float(a[key].min()),float(a[key].max())]for key in ('incoming','derivative','clock')if key in a.dtype.names and len(a)}
 result={'case':args.case,'version':aerokinetic.__version__,'installed':aerokinetic.__file__,'initial_noise_sha256':initial_sha,'shape':shape,'periodic':periodic,'last_sampled_step':last,'finite':finite,'max_energy_over_initial':max(r['energy_over_initial']for r in rows),'last':rows[-1],'boundary_ranges':boundaries,'elapsed_s':time.perf_counter()-started}
 result['boundedness_gate_passed']=finite and last==10000 and result['max_energy_over_initial']<=1.1
 result['strong_growth_detected']=result['max_energy_over_initial']>100
 package=Path(aerokinetic.__file__).parent
 result['candidate']=args.candidate
 result['farfield_source']=module.__file__
 result['farfield_sha256']=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
 (args.output/'CHECKS.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items()if k not in ('boundary_ranges','last')},indent=2),flush=True)


if __name__=='__main__':main()
