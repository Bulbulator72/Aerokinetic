"""REJECTED diagnostic prototype; vortex gate failed. Not part of the runtime."""
"""Research isothermal Riemann reservoir + NEEP for pinned D3Q27 pull kernels.

This is a locally one-dimensional approximation, not a complete NSCBC.
Its all-face/corner, shear and wave behavior must be verified independently.
"""
import math
import sympy as sp
from pystencils import Assignment, AssignmentCollection
from pystencils.typing import CastFunc, create_type
from lbmpy.boundaries.boundaryconditions import LbBoundary
from lbmpy.custom_code_nodes import NeighbourOffsetArrays


class RiemannFarfield(LbBoundary):
    def __init__(self, normal, velocity=(.04,0,0), density=1., name=None):
        self.normal=tuple(normal);self.velocity=tuple(velocity);self.density=float(density)
        if len(self.normal)!=3 or any(type(n)is not int or n not in (-1,0,1)for n in self.normal)or not any(self.normal):
            raise ValueError('Normal needs three integer components in -1,0,1 and nonzero length')
        if len(self.velocity)!=3 or not all(math.isfinite(v)for v in self.velocity):raise ValueError('Invalid freestream velocity')
        if not math.isfinite(self.density)or self.density<=0:raise ValueError('Density must be positive')
        super().__init__(name or 'RiemannFarfield_'+','.join(map(str,normal)),calculate_force_on_boundary=False)

    def get_additional_code_nodes(self, lb_method):
        return [NeighbourOffsetArrays(lb_method.stencil)]

    def __call__(self, f_out, f_in, dir_symbol, inv_dir, lb_method, index_field, force_vector):
        if lb_method.dim!=3 or len(lb_method.stencil)!=27:raise ValueError('Research boundary requires D3Q27')
        cqc=lb_method.conserved_quantity_computation
        if not cqc.compressible or not cqc.zero_centered_pdfs:raise ValueError('Research boundary requires compressible zero-centered storage')
        offset=NeighbourOffsetArrays.neighbour_offset(dir_symbol,lb_method.stencil)
        tangent=tuple(d-n for d,n in zip(offset,self.normal))
        g=[f_out[tangent](i)for i in range(27)]
        rho=sp.Symbol('ff_rho');u=sp.symbols('ff_ux ff_uy ff_uz')
        un=sp.Symbol('ff_un');unb=sp.Symbol('ff_unb');rhob=sp.Symbol('ff_rhob')
        ub=sp.symbols('ff_ubx ff_uby ff_ubz')
        normal=sp.Matrix(self.normal)/sp.sqrt(sum(n*n for n in self.normal))
        cs=sp.sqrt(sp.Rational(1,3));un_inf=sum(n*v for n,v in zip(normal,self.velocity))
        sub=[Assignment(rho,1+sum(g))]
        sub += [Assignment(u[a],sum(c[a]*p for c,p in zip(lb_method.stencil,g))/rho)for a in range(3)]
        sub += [Assignment(un,sum(n*v for n,v in zip(normal,u))),
            Assignment(unb,(un+un_inf+cs*sp.log(rho/self.density))/2),
            Assignment(rhob,sp.sqrt(rho*self.density)*sp.exp((un-un_inf)/(2*cs)))]
        for a in range(3):
            tangential=sp.Piecewise((u[a]-normal[a]*un,unb>=0),(self.velocity[a]-normal[a]*un_inf,True))
            sub.append(Assignment(ub[a],normal[a]*unb+tangential))
        # Factorized D3Q27 equilibrium; background weights cancel in its difference.
        def equilibrium(r,v):
            terms=[]
            for axis in range(3):
                c=CastFunc(-offset[axis],create_type('double'));q=v[axis]
                terms.append(sp.Rational(2,3)-q*q+(sp.Rational(3,2)*q*q-sp.Rational(1,2))*c*c+c*q/2)
            return r*sp.prod(terms)
        value=f_out[tangent](inv_dir[dir_symbol])+equilibrium(rhob,ub)-equilibrium(rho,u)
        result=AssignmentCollection([Assignment(f_in(inv_dir[dir_symbol]),value)],subexpressions=sub)
        result.topological_sort()
        return result
