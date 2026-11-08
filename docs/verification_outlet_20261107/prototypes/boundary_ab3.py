"""Experimental isothermal characteristic farfield with transverse transport.

At glancing faces the initial incoming characteristic is traced along
freestream tangential characteristics without interior feedback. Elsewhere
the incoming invariant is evolved by a linearized solenoidal-vorticity
correction; outgoing information and non-equilibrium PDFs come from fluid.
This is not a full nonlinear viscous NSCBC or a validated airfoil model.
The NEEP reconstruction follows the framework described by Klass et al.,
arXiv:2307.16766v2, equations28/29; the transverse correction is derived and
verified in docs/verification_farfield_20261107/.
"""
import math
import numpy as np
import sympy as sp
from pystencils import Assignment, AssignmentCollection
from pystencils.typing import CastFunc, create_type
from lbmpy.boundaries.boundaryconditions import LbBoundary
from lbmpy.custom_code_nodes import NeighbourOffsetArrays


class _ReservoirFarfield(LbBoundary):
    def __init__(self, normal, velocity=(.04,0,0), density=1., name=None):
        self.normal=tuple(normal);self.velocity=tuple(velocity);self.density=float(density)
        if len(self.normal)!=3 or any(type(n)is not int or n not in (-1,0,1)for n in self.normal)or not any(self.normal):
            raise ValueError('Normal needs three integer components in -1,0,1 and nonzero length')
        if len(self.velocity)!=3 or not all(math.isfinite(v)for v in self.velocity):raise ValueError('Invalid freestream velocity')
        if not math.isfinite(self.density)or self.density<=0:raise ValueError('Density must be positive')
        if sum(v*v for v in self.velocity)>=1/3:raise ValueError('Reservoir velocity must be subsonic')
        super().__init__(name or 'ReservoirFarfield_'+','.join(map(str,normal)),calculate_force_on_boundary=False)

    def get_additional_code_nodes(self, lb_method):
        return [NeighbourOffsetArrays(lb_method.stencil)]

class CharacteristicFarfield(_ReservoirFarfield):
    def __init__(self,normal,shape,periodic,velocity=(.04,0,0),density=1.,name=None,*,stepper):
        self.shape=tuple(shape);self.periodic=tuple(periodic)
        if len(self.shape)!=3 or any(type(n)is not int or n<4 for n in self.shape):raise ValueError('Three domain sizes >=4 are required')
        if len(self.periodic)!=3 or any(type(p)is not bool for p in self.periodic):raise ValueError('Three periodic-axis booleans are required')
        if getattr(stepper,'_gpu',False):raise ValueError('Characteristic farfield currently requires CPU')
        self.stepper=stepper
        self.evolve=sum(n*v for n,v in zip(normal,velocity))>=0 and any(v!=0 for v in velocity)
        super().__init__(normal,velocity,density,name or 'OutletDiagnostic_ab3_'+','.join(map(str,normal)))
        self.glancing=self.evolve and sum(n*v for n,v in zip(normal,velocity))==0
        if self.glancing:
            label='ff_initial_'+''.join('m'if n<0 else 'p'if n>0 else'z'for n in normal)
            self.initial_field=stepper.data_handling.add_array(label,values_per_cell=1,gpu=False,dtype=np.float64)


    @property
    def additional_data(self):
        state=[('clock',create_type('int64'))]if self.glancing else [('incoming',create_type('double')),('derivative',create_type('double')),('derivative2',create_type('double'))]
        return state+[(f'{kind}{a}',create_type('int32'))for kind in ('edge','wrap')for a in range(3)]

    @property
    def additional_data_init_callback(self):
        def callback(data,**_):
            offsets=data.link_offsets()
            points=np.column_stack([data.index_array[a]+data.offset[k] for k,a in enumerate(('x','y','z'))])
            # data.offset maps ghosted index coordinates to global cell indices.
            foot=points+offsets-np.asarray(self.normal)
            edges=[]
            for a in range(3):
                edge=np.zeros(len(foot),dtype=np.int32)
                wrap=np.zeros(len(foot),dtype=np.int32)
                if not self.periodic[a]:
                    assert np.all((foot[:,a]>=0)&(foot[:,a]<self.shape[a])),(self.normal,a,foot[:,a].min(),foot[:,a].max())
                    edge[foot[:,a]==0]=-1;edge[foot[:,a]==self.shape[a]-1]=1
                else:
                    wrap[foot[:,a]<0]=self.shape[a];wrap[foot[:,a]>=self.shape[a]]=-self.shape[a]
                data[f'edge{a}']=edge
                data[f'wrap{a}']=wrap
                edges.append(edge)
                if self.periodic[a]:foot[:,a]%=self.shape[a]
            normal=np.asarray(self.normal,dtype=float);normal/=np.linalg.norm(normal)
            un_inf=float(normal@self.velocity)
            if self.glancing:
                pdf=self.stepper.data_handling.cpu_arrays[self.stepper.pdf_array_name]
                stencil=np.asarray(list(self.stepper.method.stencil))
                g=pdf[1:-1,1:-1,1:-1]
                r=1+g.sum(axis=-1);u=(g@stencil)/r[...,None]
                initial=self.stepper.data_handling.cpu_arrays[self.initial_field.name]
                initial.fill(0)
                initial[1:-1,1:-1,1:-1]=u@normal-math.sqrt(1/3)*np.log(r/self.density)-un_inf
                data['clock']=0
                return
            if not self.evolve:
                data['incoming']=un_inf;data['derivative']=0.;data['derivative2']=0.;return
            pdf=self.stepper.data_handling.cpu_arrays[self.stepper.pdf_array_name]
            stencil=np.asarray(list(self.stepper.method.stencil));cs=math.sqrt(1/3)
            def macro(coordinates):
                coordinates=coordinates.copy()
                for a,p in enumerate(self.periodic):
                    if p:coordinates[:,a]%=self.shape[a]
                g=pdf[tuple(coordinates[:,a]+1 for a in range(3))]
                rho=1+g.sum(axis=-1);u=(g@stencil)/rho[:,None]
                return rho,u
            rho,u=macro(foot);rm=u@normal-cs*np.log(rho/self.density)
            projection=np.eye(3)-normal[:,None]*normal[None,:]
            ut_inf=np.asarray(self.velocity)-un_inf*normal;F=np.zeros(len(foot))
            for a in range(3):
                if ut_inf[a]==0 and not np.any(projection[a]):continue
                edge=edges[a]
                off1=np.where(edge==-1,1,-1);off2=np.where(edge==0,1,-2*edge)
                c0=1.5*edge;c1=np.where(edge==0,-.5,-2*edge);c2=np.where(edge==0,.5,.5*edge)
                one=foot.copy();two=foot.copy();one[:,a]+=off1;two[:,a]+=off2
                r1,u1=macro(one);r2,u2=macro(two)
                du=c0[:,None]*u+c1[:,None]*u1+c2[:,None]*u2
                rm1=u1@normal-cs*np.log(r1/self.density);rm2=u2@normal-cs*np.log(r2/self.density)
                F+=un_inf*(du@projection[a])-ut_inf[a]*(c0*rm+c1*rm1+c2*rm2)
            # Seed the preceding boundary clock by a backward Taylor step.
            # The first trapezoidal update then recovers the actual initial
            # fluid invariant, without a sentinel or a constant flag write.
            data['incoming']=rm-F;data['derivative']=F;data['derivative2']=F
        return callback

    def _transport_value(self,index_field,foot,un_inf):
        import itertools
        axes=[a for a,v in enumerate(self.velocity)if v!=0]
        base=[index_field(axis)-1 for axis in ('x','y','z')]
        target=[base[a]+foot[a]-self.velocity[a]*index_field('clock')for a in range(3)]
        lower=[CastFunc(sp.floor(t),create_type('int32'))for t in target]
        fraction=[t-k for t,k in zip(target,lower)]
        terms=[]
        for bits in itertools.product((0,1),repeat=len(axes)):
            bit=dict(zip(axes,bits));offset=[];weight=sp.Integer(1);valid=[]
            for a in range(3):
                if a not in axes:
                    offset.append(foot[a]);continue
                k=lower[a]+bit[a]
                weight*=fraction[a]if bit[a]else 1-fraction[a]
                if self.periodic[a]:
                    coordinate=CastFunc(k-self.shape[a]*sp.floor(CastFunc(k,create_type('double'))/self.shape[a]),create_type('int32'))
                else:
                    coordinate=CastFunc(sp.Min(sp.Max(k,0),self.shape[a]-1),create_type('int32'))
                    valid.extend((k>=0,k<self.shape[a]))
                offset.append(coordinate-base[a])
            value=self.initial_field[tuple(offset)]
            if valid:value=sp.Piecewise((value,sp.And(*valid)),(0,True))
            terms.append(weight*value)
        # One interpolation of saved initial samples at the total traced shift;
        # no repeated stepwise interpolation or feedback from current PDFs.
        return un_inf+sum(terms)

    def __call__(self,f_out,f_in,dir_symbol,inv_dir,lb_method,index_field,force_vector):
        cqc=lb_method.conserved_quantity_computation
        if lb_method.dim!=3 or len(lb_method.stencil)!=27 or not cqc.compressible or not cqc.zero_centered_pdfs:
            raise ValueError('Research boundary requires compressible zero-centered D3Q27')
        d=NeighbourOffsetArrays.neighbour_offset(dir_symbol,lb_method.stencil)
        foot=tuple(x-n+index_field(f'wrap{a}')for a,(x,n)in enumerate(zip(d,self.normal)))
        normal=sp.Matrix(self.normal)/sp.sqrt(sum(n*n for n in self.normal));cs=sp.sqrt(sp.Rational(1,3))
        un_inf=sum(n*v for n,v in zip(normal,self.velocity));ut_inf=[v-un_inf*n for v,n in zip(self.velocity,normal)]
        projection=sp.eye(3)-normal*normal.T
        rho=sp.Symbol('ff_rho');u=sp.symbols('ff_ux ff_uy ff_uz')
        def local_macro(offset,r,vel):
            g=[f_out[offset](i)for i in range(27)]
            return [Assignment(r,1+sum(g))]+[Assignment(vel[a],sum(c[a]*p for c,p in zip(lb_method.stencil,g))/r)for a in range(3)]
        sub=local_macro(foot,rho,u)
        un=sp.Symbol('ff_un');rm=sp.Symbol('ff_rm');rp=sp.Symbol('ff_rp');incoming=sp.Symbol('ff_incoming')
        sub += [Assignment(un,sum(n*v for n,v in zip(normal,u))),Assignment(rm,un-cs*sp.log(rho/self.density)),
                Assignment(rp,un+cs*sp.log(rho/self.density))]
        F=sp.Symbol('ff_derivative');terms=[]
        if self.evolve and not self.glancing:
            for a in range(3):
                if ut_inf[a]==0 and all(projection[a,b]==0 for b in range(3)):continue
                edge=index_field(f'edge{a}')
                off1=sp.Piecewise((1,sp.Eq(edge,-1)),(-1,True))
                off2=sp.Piecewise((1,sp.Eq(edge,0)),(-2*edge,True))
                c0=sp.Rational(3,2)*edge
                c1=sp.Piecewise((-sp.Rational(1,2),sp.Eq(edge,0)),(-2*edge,True))
                c2=sp.Piecewise((sp.Rational(1,2),sp.Eq(edge,0)),(sp.Rational(1,2)*edge,True))
                rs=[];vs=[]
                for j,delta in enumerate((off1,off2)):
                    shifted=list(foot);shifted[a]+=delta
                    r=sp.Symbol(f'ff_r{a}_{j}');vel=sp.symbols(f'ff_v{a}_{j}_0:3')
                    sub+=local_macro(tuple(shifted),r,vel);rs.append(r);vs.append(vel)
                derivative_u=[c0*u[b]+c1*vs[0][b]+c2*vs[1][b]for b in range(3)]
                rm1=sum(n*v for n,v in zip(normal,vs[0]))-cs*sp.log(rs[0]/self.density)
                rm2=sum(n*v for n,v in zip(normal,vs[1]))-cs*sp.log(rs[1]/self.density)
                terms.append(un_inf*sum(projection[a,b]*derivative_u[b]for b in range(3))-ut_inf[a]*(c0*rm+c1*rm1+c2*rm2))
            sub.append(Assignment(F,sum(terms)))
            previous=sp.Symbol('ff_previous_derivative')
            sub.append(Assignment(previous,index_field('derivative')))
            value=index_field('incoming')+(23*F-16*previous+5*index_field('derivative2'))/12
        elif self.glancing:
            value=self._transport_value(index_field,foot,un_inf)
        else:
            value=un_inf
        sub.append(Assignment(incoming,value))
        unb=sp.Symbol('ff_unb');rhob=sp.Symbol('ff_rhob');ub=sp.symbols('ff_ubx ff_uby ff_ubz')
        sub += [Assignment(unb,(rp+incoming)/2),Assignment(rhob,self.density*sp.exp((rp-incoming)/(2*cs)))]
        for a in range(3):
            tangential=sp.Piecewise((u[a]-normal[a]*un,unb>=0),(self.velocity[a]-normal[a]*un_inf,True))
            sub.append(Assignment(ub[a],normal[a]*unb+tangential))
        def eq(r,v):
            phi=[]
            for a in range(3):
                c=CastFunc(-d[a],create_type('double'));q=v[a]
                phi.append(sp.Rational(2,3)-q*q+(sp.Rational(3,2)*q*q-sp.Rational(1,2))*c*c+c*q/2)
            return r*sp.prod(phi)
        assignments=[Assignment(f_in(inv_dir[dir_symbol]),f_out[foot](inv_dir[dir_symbol])+eq(rhob,ub)-eq(rho,u))]
        if self.evolve and not self.glancing:
            assignments += [Assignment(index_field('incoming'),incoming),Assignment(index_field('derivative2'),previous),Assignment(index_field('derivative'),F)]
        if self.glancing:assignments.append(Assignment(index_field('clock'),index_field('clock')+1))
        result=AssignmentCollection(assignments,subexpressions=sub)
        # Stateful field increment must follow PDF evaluation; it is an
        # ordered update, not a simultaneous algebraic assignment system.
        if not self.glancing:result.topological_sort()
        return result


def install_characteristic_farfield(lbm, shape, periodic, velocity):
    """Partition exterior ghost cells into disjoint face/corner boundaries."""
    import itertools
    for normal in itertools.product(*[(0,) if p else (-1,0,1) for p in periodic]):
        if not any(normal):
            continue
        def mask(x,y,z,normal=normal):
            outside=True
            for axis,(p,n,value) in enumerate(zip(periodic,normal,(x,y,z))):
                if p:
                    continue
                outside=outside & ((value<0) if n<0 else (value>shape[axis]) if n>0
                                   else ((value>=0)&(value<=shape[axis])))
            return outside
        boundary=CharacteristicFarfield(normal,shape,periodic,velocity,stepper=lbm)
        lbm.boundary_handling.set_boundary(boundary,mask_callback=mask)
