"""Conservative stationarity residuals and explicit, checkpointed stop criteria.

The four residuals are d(rho,j_common)/dt*, not the error of an implicit NS
solve. Global discrete balance closure is reported separately and never drives
the steady-flow stop. No populations or boundary history are modified here.
"""
from dataclasses import asdict,dataclass
import csv
import hashlib
import math
import os
from pathlib import Path
import shutil
import time
import uuid

import numpy as np

from ._io import file_hash

CHANNELS=('rho','jx','jy','jz')
RESIDUAL_COLUMNS=('step','t_star',*(f'l2_{c}' for c in CHANNELS),
                  *(f'linf_{c}' for c in CHANNELS),*(f'balance_{c}' for c in CHANNELS),'streak')


@dataclass(frozen=True)
class StopCriteria:
    mode: str='time'
    mass_tolerance: float=1e-5
    momentum_x_tolerance: float=1e-5
    momentum_y_tolerance: float=1e-5
    momentum_z_tolerance: float=1e-5
    minimum_t_star: float=1.
    consecutive_checks: int=10
    max_steps: int=0
    wall_time_limit_s: float=0.

    def __post_init__(self):
        if self.mode not in ('time','steady'):raise ValueError('Stop mode must be time or steady')
        for name in ('mass_tolerance','momentum_x_tolerance','momentum_y_tolerance','momentum_z_tolerance'):
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<=0:
                raise ValueError(f'{name} must be finite and positive')
        for name in ('minimum_t_star','wall_time_limit_s'):
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<0:
                raise ValueError(f'{name} must be finite and nonnegative')
        for name,minimum in (('consecutive_checks',1),('max_steps',0)):
            if type(getattr(self,name)) is not int or getattr(self,name)<minimum:
                raise ValueError(f'{name} must be an integer >= {minimum}')

    @property
    def thresholds(self):
        return (self.mass_tolerance,self.momentum_x_tolerance,self.momentum_y_tolerance,self.momentum_z_tolerance)


def residual_norms(previous,current,cell_volumes,dt):
    """Each array contains four conservative components of OWNED FLUID cells."""
    if not math.isfinite(dt) or dt<=0:raise ValueError('Residual time interval must be positive')
    if len(previous)!=len(current) or len(current)!=len(cell_volumes):raise ValueError('Residual levels differ')
    square=np.zeros(4);infinity=np.zeros(4);volume=0.
    for a,b,v in zip(previous,current,cell_volumes):
        a,b=np.asarray(a),np.asarray(b)
        if a.shape!=b.shape or a.ndim!=2 or a.shape[1]!=4:raise ValueError('Residual component arrays differ')
        if not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Non-finite residual state')
        if not math.isfinite(v) or v<=0:raise ValueError('Cell volume must be positive')
        difference=(b-a)/dt
        square+=v*np.einsum('ij,ij->j',difference,difference)
        infinity=np.maximum(infinity,np.max(abs(difference),axis=0,initial=0))
        volume+=len(a)*v
    if volume<=0:raise ValueError('Residual domain has no owned fluid volume')
    return np.sqrt(square/volume),infinity


def conservative_samples(solver):
    if hasattr(solver,'coarse_level'):
        sources=zip(solver._physical_views(),(solver.coarse_owned,solver.fine_fluid),
                    (solver.coarse_level.dx**3,solver.fine_level.dx**3))
        stencil=solver.transfer.stencil
    else:
        lbm=solver._stepper
        if solver.grid.target=='gpu':lbm.data_handling.to_cpu(lbm.pdf_array_name)
        pdf=lbm.data_handling.cpu_arrays[lbm.pdf_array_name][1:-1,1:-1,1:-1]
        sources=((pdf,solver._fluid,solver.plan.dx**3),)
        stencil=solver._stencil
    result=[];volumes=[]
    for pdf,mask,volume in sources:
        q=np.empty((*pdf.shape[:-1],4))
        q[...,0]=pdf.sum(axis=-1)+1
        q[...,1:]=np.einsum('...q,qi->...i',pdf,stencil,optimize=False)/solver.flow.lattice_speed
        owned=q[mask]
        if not np.isfinite(owned).all() or np.any(owned[:,0]<=0):raise ValueError('Invalid fluid state for residuals')
        result.append(owned);volumes.append(volume)
    return tuple(result),tuple(volumes)


def validate_history(output,context):
    if 'residuals_bytes' in context:
        path=Path(output)/'residuals.csv'
        if file_hash(path,context['residuals_bytes'])!=context['residuals_sha256']:
            raise ValueError('Residual history before checkpoint changed')


class RunControl:
    def __init__(self,solver,settings,output,*,resume=False,callback=None,start_step=None):
        self.solver=solver;self.criteria=settings.termination;self.callback=callback
        self.path=Path(output)/'residuals.csv'
        self.started=time.monotonic()
        self.dt=solver.coarse_level.dt if hasattr(solver,'coarse_level') else solver.plan.dt_convective
        self.run_start_step=solver.steps if start_step is None else start_step
        self.previous,self.volumes=conservative_samples(solver)
        self.previous_step=solver.steps;self.streak=0;self.last_record=None
        restored=getattr(solver,'_restored_control',None) if resume else None
        if restored is not None:
            meta,arrays=restored
            if len(arrays)!=len(self.previous) or any(a.shape!=b.shape for a,b in zip(arrays,self.previous)):
                raise ValueError('Residual checkpoint ownership differs')
            if (type(meta['previous_step']) is not int or not 0<=meta['previous_step']<=solver.steps
                    or type(meta['streak']) is not int or meta['streak']<0):
                raise ValueError('Invalid residual checkpoint clock/streak')
            self.previous=arrays;self.previous_step=meta['previous_step'];self.streak=meta['streak']
            self.last_record=meta['last_record'];self.run_start_step=meta['run_start_step']
        context=getattr(solver,'_checkpoint_context',{}) if resume else {}
        validate_history(output,context)
        if resume and self.path.exists():
            prefix=context.get('residuals_bytes',0)
            if self.path.stat().st_size>prefix:
                recovery=Path(output)/'recovery'/uuid.uuid4().hex
                recovery.mkdir(parents=True)
                shutil.copy2(self.path,recovery/'residuals_before_resume.csv')
                with self.path.open('r+b') as handle:handle.truncate(prefix)
        append=resume and 'residuals_bytes' in context
        self.handle=self.path.open('a' if append else 'w',newline='',encoding='utf-8')
        self.writer=csv.DictWriter(self.handle,RESIDUAL_COLUMNS)
        if not append:self.writer.writeheader();self.handle.flush()
        solver._run_control=self

    def observe(self):
        step=self.solver.steps
        if step<=self.previous_step:return None
        current,volumes=conservative_samples(self.solver)
        if volumes!=self.volumes:raise ValueError('Residual cell volumes changed')
        l2,linf=residual_norms(self.previous,current,volumes,(step-self.previous_step)*self.dt)
        record={'step':step,'t_star':step*self.dt}
        record.update(zip((f'l2_{c}' for c in CHANNELS),map(float,l2)))
        record.update(zip((f'linf_{c}' for c in CHANNELS),map(float,linf)))
        if getattr(self.solver,'last_budget',None):
            budget=self.solver.last_budget
            defects=[budget['mass_residual_per_volume'],*budget['momentum_residual_per_volume']]
            record.update(zip((f'balance_{c}' for c in CHANNELS),(abs(float(v))/self.dt for v in defects)))
        eligible=(self.criteria.mode=='steady' and record['t_star']>=self.criteria.minimum_t_star
                  and all(value<=limit for value,limit in zip(l2,self.criteria.thresholds)))
        self.streak=self.streak+1 if eligible else 0
        record['streak']=self.streak
        self.previous=current;self.previous_step=step;self.last_record=record
        self.writer.writerow(record);self.handle.flush()
        if self.callback:self.callback(self.solver,dict(record))
        return record

    def stop_reason(self):
        if self.criteria.mode=='steady' and self.streak>=self.criteria.consecutive_checks:return 'residual_targets_met'
        if self.criteria.max_steps and self.solver.steps-self.run_start_step>=self.criteria.max_steps:return 'max_steps_reached'
        if self.criteria.wall_time_limit_s and time.monotonic()-self.started>=self.criteria.wall_time_limit_s:return 'wall_time_limit'
        return None

    def checkpoint_context(self):
        self.handle.flush()
        os.fsync(self.handle.fileno())
        return {'residuals_bytes':self.path.stat().st_size,'residuals_sha256':file_hash(self.path)}

    def summary(self,reason):
        return {'termination_reason':reason,'termination':asdict(self.criteria),'last_residuals':self.last_record,
                'steady_residual_criterion_met':reason=='residual_targets_met',
                'statistical_convergence':'not_established'}

    def close(self):self.handle.close()


def save_control(solver,arrays,metadata):
    control=getattr(solver,'_run_control',None)
    if control is None:return
    names=[];hashes={}
    for i,array in enumerate(control.previous):
        key=f'control_previous_{i}';arrays[key]=array;names.append(key)
        hashes[key]=hashlib.sha256(array.tobytes(order='C')).hexdigest()
    metadata['run_control']={'previous_step':control.previous_step,'streak':control.streak,
         'last_record':control.last_record,'run_start_step':control.run_start_step,
         'arrays':names,'array_sha256':hashes}


def restore_control(solver,saved,metadata):
    meta=metadata.get('run_control')
    if meta is None:return
    names=meta['arrays']
    if len(names) not in (1,2) or names!=[f'control_previous_{i}' for i in range(len(names))]:
        raise ValueError('Invalid residual checkpoint array manifest')
    arrays=[]
    for name in names:
        array=saved[name]
        if (array.ndim!=2 or array.shape[1]!=4 or array.dtype!=np.dtype('float64')
                or not np.isfinite(array).all()
                or hashlib.sha256(array.tobytes(order='C')).hexdigest()!=meta['array_sha256'][name]):
            raise ValueError('Residual checkpoint array/checksum invalid')
        arrays.append(array.copy())
    solver._restored_control=(meta,tuple(arrays))
