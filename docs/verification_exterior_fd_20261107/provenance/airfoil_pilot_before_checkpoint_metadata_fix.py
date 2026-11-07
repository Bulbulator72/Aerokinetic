"""Research body adapter around the unchanged tested ExteriorFD step.

CPU1, uniform, no SGS, fixed Re30000/N32/U=.04 only. This is deliberately
separate from the installed library and its checkpoint/run formats.
"""
from dataclasses import asdict,replace
import hashlib
import json
from pathlib import Path

import numpy as np
from lbmpy.boundaries import NoSlipLinearBouzidi
from aerokinetic.config import Plan
from aerokinetic.solver import NumericalFailure,Solver
from exterior_fd import ExteriorFD,NU


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AirfoilPilot(Solver):
    def __init__(self,project):
        self.section,self.flow,self.grid=project.section,project.flow,project.grid
        if(self.grid.target!='cpu' or self.grid.openmp_threads!=1 or self.flow.sgs!='none'
            or self.grid.cells_per_chord!=32 or self.flow.reynolds!=30000 or self.flow.lattice_speed!=.04):
            raise ValueError('This research adapter is restricted to the preregistered CPU1 N32/Re30000/U.04/noSGS fixture')
        self.core_plan=Plan.create(self.section,self.flow,self.grid)
        if self.core_plan.estimated_memory_gib*1.3>self.grid.memory_limit_gib:
            raise MemoryError('Research core+auxiliary memory estimate exceeds the explicit limit')
        self.contours=self.section.normalized(self.flow.alpha_deg)
        self.exterior=ExteriorFD(self.core_plan.shape,(False,False,True),(self.flow.lattice_speed,0,0))
        self._stepper=self.exterior.s
        if abs(self.core_plan.viscosity-NU)>1e-18:raise ValueError('Viscosity mismatch')
        self.plan=replace(self.core_plan,shape=self.exterior.shape,
            origin=tuple(v-self.core_plan.dx for v in self.core_plan.origin))
        self._stencil=np.asarray(list(self._stepper.method.stencil),dtype=np.int64)
        self._inverse=np.array([self._stepper.method.stencil.index(tuple(-e))for e in self._stencil])
        self.steps=0;self.wall_fallback_links=0;self.last_loads=None;self._run_control=None
        self._wall_objects=[]
        for i,contour in enumerate(self.contours):
            wall=NoSlipLinearBouzidi(name=f'solid_{i}',init_wall_distance=self._distance_callback(contour))
            self._stepper.boundary_handling.set_boundary(wall,mask_callback=self._mask(contour))
            self._wall_objects.append(wall)
        x=(np.arange(self.plan.shape[0])+.5)*self.plan.dx+self.plan.origin[0]
        y=(np.arange(self.plan.shape[1])+.5)*self.plan.dx+self.plan.origin[1]
        self._solid_xy=np.zeros(self.plan.shape[:2],bool)
        for contour in self.contours:
            solid=contour.contains(x[:,None],y[None,:])
            if not solid.any():raise ValueError('Unrepresented element')
            self._solid_xy|=solid
        self._fluid=np.broadcast_to(~self._solid_xy[:,:,None],self.plan.shape)
        for b in self._stepper.data_handling.iterate(ghost_layers=True):b[self._stepper.density_data_name][...]=1
        # Original initializer uses Plan.shape[1] for the seed wavelength. Keep
        # that original physical Ny while retaining the shifted auxiliary origin.
        augmented=self.plan
        self.plan=replace(augmented,shape=self.core_plan.shape)
        super()._initialize()
        self.plan=augmented
        # The physical core seed is unchanged; auxiliary/outer reservoir starts
        # at the freestream. Rebuild before any collision, not during a run.
        dh=self._stepper.data_handling
        velocity=dh.cpu_arrays[self._stepper.velocity_data_name]
        physical=np.zeros(tuple(n+2 for n in self.plan.shape),bool)
        physical[self.exterior.core_pdf]=True
        velocity[~physical]=(self.flow.lattice_speed,0,0)
        self._stepper.set_pdf_fields_from_macroscopic_values();self._stepper.pre_run()
        self._stepper.boundary_handling.prepare()
        self._pending_sample=False;self._pending_audit=False;self._captured_loads=None
        self._raw_run=dh.run_kernel

        def observed_run(kernel,**params):
            if kernel is self._stepper._lbmKernels[1]:
                if self._pending_sample or self._pending_audit:self._captured_loads=self._loads()
                if self._pending_audit:self._crossing_flux=self.fluid_flux()
            result=self._raw_run(kernel,**params)
            if kernel is self._stepper._lbmKernels[1]and self._pending_audit:
                # Streaming has written tmp; swap has not happened yet.
                self._after_stream_totals=self.fluid_totals(self._stepper._tmp_arr_name)
            return result
        dh.run_kernel=observed_run
        self._prepare_fluid_audit()

    def _prepare_fluid_audit(self):
        """Independent mask-crossing lists, including complete background PDFs."""
        m=self.exterior;shape=self.plan.shape
        self.core_fluid=self._fluid[m.core].copy();self.fluid_count=int(self.core_fluid.sum())
        box=np.zeros(tuple(n+2 for n in shape),bool);box[m.core_pdf]=self.core_fluid
        solid=np.zeros_like(box);solid[1:-1,1:-1,1:-1]=~self._fluid
        for mask in(box,solid):
            mask[:,:,0]=mask[:,:,-2];mask[:,:,-1]=mask[:,:,1]
        positions=np.column_stack(np.nonzero(self.core_fluid))+m.pad+1
        self._fluid_pdf_index=tuple(positions.T)
        self._rx=positions[:,0]-.5+self.plan.origin[0]/self.plan.dx
        self._ry=positions[:,1]-.5+self.plan.origin[1]/self.plan.dx
        self._crossings=[]
        for q,c in enumerate(self._stencil):
            for direction,sign in((c,-1),(-c,1)):
                neighbor=positions+direction
                crossing=~box[tuple(neighbor.T)]
                wall=solid[tuple(neighbor.T)]
                for kind,selected in(('wall',crossing&wall),('outer',crossing&~wall)):
                    if not selected.any():continue
                    at=positions[selected]if sign<0 else neighbor[selected]
                    rx=self._rx[selected];ry=self._ry[selected]
                    self._crossings.append((q,sign,kind,tuple(at.T),rx*c[1]-ry*c[0]))

    def fluid_totals(self,name=None):
        pdf=self._stepper.data_handling.cpu_arrays[name or self._stepper.pdf_array_name]
        g=pdf[self._fluid_pdf_index];moment=g@self._stencil
        return np.r_[self.fluid_count+g.sum(),moment.sum(axis=0),np.sum(self._rx*moment[:,1]-self._ry*moment[:,0])]

    def fluid_flux(self):
        pdf=self._stepper.data_handling.cpu_arrays[self._stepper.pdf_array_name]
        result={k:np.zeros(5)for k in('wall','outer')}
        for q,sign,kind,indices,arm in self._crossings:
            f=pdf[indices+(q,)]+self.exterior.weights[q]
            result[kind]+=sign*np.r_[f.sum(),f.sum()*self._stencil[q],np.sum(f*arm)]
        return result

    def step(self,sample=False,audit=False):
        before=self.fluid_totals()if audit else None
        self._pending_sample=sample;self._pending_audit=audit;self._captured_loads=None
        report=self.exterior.step(audit=False)
        self.steps=self.exterior.step_count;self._stepper.time_steps_run=self.steps
        loads=self._captured_loads
        if loads is not None:
            loads.update(step=self.steps,t_star=(self.steps-.5)*self.core_plan.dt_convective)
            if self.plan.dt_seconds is not None:loads['time_s']=(self.steps-.5)*self.plan.dt_seconds
            self.last_loads=loads
        if audit:
            flux=self._crossing_flux;totalflux=flux['wall']+flux['outer']
            # Momentum exchange on the body is minus the independently counted
            # wall contribution to fluid momentum. Moment uses the same input
            # reporting origin, computed here from the mask rather than wall lists.
            body=loads['element_lattice_loads'].sum(axis=0)
            report.update(fluid_budget_per_cell=((self._after_stream_totals-before-totalflux)/self.fluid_count).tolist(),
                physical_fluid_pdf_overwrite_budget=((self.fluid_totals()-self._after_stream_totals)/self.fluid_count).tolist(),
                wall_force_balance_error=(body[:3]+flux['wall'][1:4]).tolist(),
                wall_torque_balance_error=float(body[3]+flux['wall'][4]),
                wall_mass_exchange=float(flux['wall'][0]),wall_flux=flux['wall'].tolist(),outer_flux=flux['outer'].tolist())
        self._pending_sample=False;self._pending_audit=False
        return loads,report

    def identity(self):
        return {'model':'research_exterior_fd_v1','core_plan':self.core_plan.as_dict(),
            'auxiliary_plan':self.plan.as_dict(),'storage':'pre_collision',
            'exterior_source_sha256':sha(Path(__file__).with_name('exterior_fd.py')),
            'adapter_source_sha256':sha(__file__),'installed_solver_source_sha256':sha(__import__('aerokinetic.solver',fromlist=['']).__file__),
            'wall_fallback_links':self.wall_fallback_links,
            'limits':'CPU1 prototype; unchanged installed wheel. N32 is not a resolved airfoil validation.'}

    def save_research_checkpoint(self,path):
        s=self._stepper;arrays=s.data_handling.cpu_arrays
        identity=json.dumps(self.identity(),sort_keys=True)
        with Path(path).open('wb')as f:
            np.savez(f,src=arrays[s.pdf_array_name],dst=arrays[s._tmp_arr_name],
                initial=self.exterior.initial,step=self.steps,identity=identity)

    def restore_research_checkpoint(self,path):
        s=self._stepper
        with np.load(path,allow_pickle=False)as d:
            if json.loads(str(d['identity']))!=self.identity():raise ValueError('Research checkpoint identity mismatch')
            if d['initial'].dtype==object:raise ValueError('Only checkpoints after a complete step are supported')
            for key,name in(('src',s.pdf_array_name),('dst',s._tmp_arr_name)):
                if d[key].shape!=s.data_handling.cpu_arrays[name].shape:raise ValueError('Checkpoint shape mismatch')
                s.data_handling.cpu_arrays[name][...]=d[key]
            self.exterior.initial=d['initial'].copy();self.steps=int(d['step'])
            self.exterior.step_count=self.steps;s.time_steps_run=self.steps


def wall_links(solver,shift):
    rows=[];s=solver._stepper;s.boundary_handling.prepare()
    for block in s.data_handling.iterate(ghost_layers=1):
        lists=block[s.boundary_handling._index_array_name].boundary_object_to_index_list
        for element,wall in enumerate(solver._wall_objects):
            data=lists[wall]
            for row in data:
                rows.append((element,int(row['x'])-shift[0],int(row['y'])-shift[1],int(row['z'])-shift[2],int(row['dir']),float(row['q'])))
    return sorted(rows)
