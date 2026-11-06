"""Fused CPU/OpenMP evaluation of the existing D3Q27 transfer algebra.

The interpolation, viscosities, regularization and positivity contract stay in
refinement.py. These kernels replace only array-wise moment/equilibrium algebra.
No fast-math, clipping, force or model term is introduced.
"""
from functools import lru_cache

import numpy as np

from .refinement import D3Q27Transfer, SECOND_MOMENTS, _real_array


@lru_cache(maxsize=16)
def _kernels(order,zero_centered,threads):
    import sympy as sp
    import pystencils as ps
    p,m,check=ps.fields('p(27), m(10), check: double[1D]')
    symbols=sp.symbols('g:27')
    rho=sp.Symbol('rho');js=sp.symbols('jx jy jz')
    delta=sum(symbols)
    sub=[ps.Assignment(s,p(i)) for i,s in enumerate(symbols)]
    sub.append(ps.Assignment(rho,delta+(1 if zero_centered else 0)))
    sub += [ps.Assignment(js[a],sum(int(c[a])*symbols[q] for q,c in enumerate(order))) for a in range(3)]
    assignments=[ps.Assignment(m(0),rho)]
    assignments += [ps.Assignment(m(a+1),js[a]) for a in range(3)]
    for k,(a,b) in enumerate(SECOND_MOMENTS):
        raw=sum(int(c[a]*c[b])*symbols[q] for q,c in enumerate(order))
        if a==b:raw-= (rho-(1 if zero_centered else 0))/3
        assignments.append(ps.Assignment(m(k+4),raw-js[a]*js[b]/rho))
    weights=[sp.prod(sp.Rational(2,3) if v==0 else sp.Rational(1,6) for v in c) for c in order]
    assignments.append(ps.Assignment(check.center,sp.Min(*(symbols[i]+(w if zero_centered else 0) for i,w in enumerate(weights)))))
    cfg=ps.CreateKernelConfig(data_type='float64',ghost_layers=0,cpu_openmp=threads if threads>1 else False)
    forward=ps.create_kernel(ps.AssignmentCollection(assignments,subexpressions=sub),config=cfg).compile()

    u=sp.symbols('ux uy uz')
    phi=sp.symbols('phi:9')
    sub=[ps.Assignment(rho,m(0))]+[ps.Assignment(u[a],m(a+1)/rho) for a in range(3)]
    for a in range(3):
        for v in (-1,0,1):
            expression=sp.Rational(2,3)-u[a]**2 if v==0 else (sp.Rational(1,3)+u[a]**2+v*u[a])/2
            sub.append(ps.Assignment(phi[3*a+v+1],expression))
    physical=[];assignments=[]
    for q,c in enumerate(order):
        neq=0
        for k,(a,b) in enumerate(SECOND_MOMENTS):
            h=c[a]*c[b]-(sp.Rational(1,3) if a==b else 0)
            if a!=b:h*=2
            neq+=weights[q]*h*sp.Rational(9,2)*m(k+4)
        value=sp.Symbol(f'eq_{q}')
        sub.append(ps.Assignment(value,rho*sp.prod(phi[3*a+c[a]+1] for a in range(3))+neq))
        physical.append(value)
        assignments.append(ps.Assignment(p(q),value-(weights[q] if zero_centered else 0)))
    assignments.append(ps.Assignment(check.center,sp.Min(*physical)))
    backward=ps.create_kernel(ps.AssignmentCollection(assignments,subexpressions=sub),config=cfg).compile()
    return forward,backward


class CPUTransfer(D3Q27Transfer):
    def __init__(self,*args,threads=1,**kwargs):
        super().__init__(*args,**kwargs)
        if type(threads) is not int or threads<1:raise ValueError('CPU threads must be a positive integer')
        self.threads=threads
        self._native=_kernels(tuple(map(tuple,self.stencil.tolist())),self.zero_centered,threads)

    def moments(self,pdf):
        pdf=_real_array(pdf,'Populations')
        if pdf.ndim<1 or pdf.shape[-1]!=27:raise ValueError('Last population dimension must be 27')
        flat=np.ascontiguousarray(pdf).reshape(-1,27)
        out=np.empty((len(flat),10));check=np.empty(len(flat))
        self._native[0](p=flat,m=out,check=check)
        if np.any(out[:,0]<=0):raise ValueError('Transfer encountered nonpositive density')
        if np.any(check<0):raise ValueError('Donor has negative physical populations')
        if not np.isfinite(out).all():raise ValueError('Populations must be finite')
        self._check_mach(out[:,0],out[:,1:4])
        return out.reshape((*pdf.shape[:-1],10))

    def populations(self,moments):
        fields=_real_array(moments,'Hydrodynamic moments')
        if fields.ndim<1 or fields.shape[-1]!=10:raise ValueError('Last hydrodynamic moment dimension must be 10')
        flat=np.ascontiguousarray(fields).reshape(-1,10)
        if np.any(flat[:,0]<=0):raise ValueError('Prediction produced nonpositive density; do not clip a conservative transfer')
        self._check_mach(flat[:,0],flat[:,1:4])
        out=np.empty((len(flat),27));check=np.empty(len(flat))
        self._native[1](p=out,m=flat,check=check)
        if not np.isfinite(out).all() or np.any(check<0):
            raise ValueError('Prediction produced invalid or negative physical populations')
        return out.reshape((*fields.shape[:-1],27))


# A fluid-only verification fixture for the very same threaded transfer and
# LBM kernels used by RefinedSolver, with the periodic interface reference API.
from .interface import PeriodicRefinedBox,_make_lattice


class ParallelRefinedBox(PeriodicRefinedBox):
    def __init__(self,*args,threads=2,**kwargs):
        self.threads=threads
        super().__init__(*args,**kwargs)

    def _create_lattice(self,shape,level,*,coarse_grid):
        return _make_lattice(shape,level,threads=self.threads)

    def _create_transfer(self,stencil):
        return CPUTransfer(stencil,self.coarse_level,zero_centered=True,pdf_stage='pre_collision',threads=self.threads)
