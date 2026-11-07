"""Research incoming-characteristic correction of destination-cell moments.

All unknown PDFs arriving at a boundary fluid cell share one target and
normal, including corners. Interior collision/viscosity are unchanged.
This prototype has not passed the scientific acceptance gates.
"""
import itertools
import math
import numpy as np
import sympy as sp
from pystencils import Assignment, AssignmentCollection
from pystencils.typing import CastFunc, create_type, TypedSymbol
from lbmpy.boundaries.boundaryconditions import LbBoundary
from lbmpy.custom_code_nodes import NeighbourOffsetArrays


def regularized_neq(velocity, stress, direction):
    """Linear factorized covariance correction; conserved moments are verified separately."""
    phi=[sp.Rational(2,3)-u*u+(sp.Rational(3,2)*u*u-sp.Rational(1,2))*c*c+c*u/2
         for u,c in zip(velocity,direction)]
    first=[-2*u+3*u*c*c+c/2 for u,c in zip(velocity,direction)]
    second=[sp.Rational(3,2)*c*c-1 for c in direction]
    diagonal=sum(stress[a,a]*second[a]*sp.prod(phi[j]for j in range(3)if j!=a)for a in range(3))
    cross=sum(stress[a,b]*first[a]*first[b]*sp.prod(phi[j]for j in range(3)if j not in (a,b))
              for a in range(3)for b in range(a+1,3))
    return diagonal+cross


class CharacteristicFarfield(LbBoundary):
    def __init__(self, normal, shape, periodic, velocity=(.04,0,0), density=1., name=None, *, stepper):
        # normal is accepted for old test-factory compatibility; the actual
        # destination-cell normal is calculated separately for every link.
        self.shape=tuple(shape);self.periodic=tuple(periodic)
        self.velocity=tuple(velocity);self.density=float(density);self.stepper=stepper
        if len(shape)!=3 or min(shape)<4 or not periodic[2]:
            raise ValueError('Prototype needs three sizes>=4 and periodic z')
        if getattr(stepper,'_gpu',False):raise ValueError('Prototype requires CPU')
        if self.density<=0 or not math.isfinite(self.density):raise ValueError('Invalid density')
        if len(self.velocity)!=3 or not all(math.isfinite(v)for v in self.velocity)or sum(v*v for v in self.velocity)>=1/3:
            raise ValueError('Invalid or supersonic background velocity')
        label='all'if normal is None else ''.join('m'if n<0 else'p'if n>0 else'z'for n in normal)
        self.normal=None if normal is None else tuple(normal)
        super().__init__(name or 'OwnedMomentFDTrial_'+label,calculate_force_on_boundary=False)
        self.initial_field=stepper.data_handling.add_array('owned_ff_initial_'+label,values_per_cell=4,gpu=False,dtype=np.float64)
        # Research storage: ordinary fields avoid pystencils1.4's unsupported
        # hoisting of writes through a structured index-field reinterpret cast.
        # One channel per link avoids read/write races between boundary links.
        self.incoming_field=stepper.data_handling.add_array('owned_ff_incoming_'+label,values_per_cell=27,gpu=False,dtype=np.float64)
        self.clock_field=stepper.data_handling.add_array('owned_ff_clock_'+label,values_per_cell=27,gpu=False,dtype=np.int64)

    def get_additional_code_nodes(self,lb_method):
        return [NeighbourOffsetArrays(lb_method.stencil)]

    @property
    def additional_data(self):
        return [(f'n{a}',create_type('int32'))for a in range(2)]+[(f'edge{a}',create_type('int32'))for a in range(3)]

    @property
    def additional_data_init_callback(self):
        def callback(data,**_):
            points=np.column_stack([data.index_array[a]+data.offset[k]for k,a in enumerate(('x','y','z'))])
            assert np.all(points>=0)and np.all(points<np.asarray(self.shape))
            n=np.zeros_like(points)
            for a,p in enumerate(self.periodic):
                edge=np.zeros(len(points),dtype=np.int32)
                if not p:
                    edge[points[:,a]==0]=-1;edge[points[:,a]==self.shape[a]-1]=1
                    n[:,a]=edge
                data[f'edge{a}']=edge
                if a<2:data[f'n{a}']=n[:,a]
            assert np.all(np.sum(n*n,axis=-1)>0)
            normal=n/np.linalg.norm(n,axis=-1)[:,None]
            pdf=self.stepper.data_handling.cpu_arrays[self.stepper.pdf_array_name]
            stencil=np.asarray(list(self.stepper.method.stencil))
            g=pdf[1:-1,1:-1,1:-1];rho=1+g.sum(axis=-1);u=(g@stencil)/rho[...,None]
            initial=self.stepper.data_handling.cpu_arrays[self.initial_field.name]
            initial.fill(0)
            initial[1:-1,1:-1,1:-1,0]=np.log(rho/self.density)
            initial[1:-1,1:-1,1:-1,1:]=u
            r=rho[tuple(points[:,a]for a in range(3))];v=u[tuple(points[:,a]for a in range(3))]
            incoming=self.stepper.data_handling.cpu_arrays[self.incoming_field.name]
            clock=self.stepper.data_handling.cpu_arrays[self.clock_field.name]
            incoming.fill(0);clock.fill(0)
            location=tuple(points[:,a]+1 for a in range(3))+(data.index_array['dir'],)
            incoming[location]=np.sum(v*normal,axis=-1)-math.sqrt(1/3)*np.log(r/self.density)
        return callback

    def _transport(self,index_field,normal,time_value):
        axes=[a for a,v in enumerate(self.velocity)if v!=0]
        base=[index_field(a)-1 for a in ('x','y','z')]
        target=[base[a]-self.velocity[a]*time_value for a in range(3)]
        lower=[CastFunc(sp.floor(t),create_type('int32'))for t in target]
        fraction=[t-k for t,k in zip(target,lower)]
        terms=[];cs=sp.sqrt(sp.Rational(1,3))
        for bits in itertools.product((0,1),repeat=len(axes)):
            bit=dict(zip(axes,bits));offset=[];weight=sp.Integer(1);valid=[]
            for a in range(3):
                if a not in axes:offset.append(0);continue
                k=lower[a]+bit[a];weight*=fraction[a]if bit[a]else 1-fraction[a]
                if self.periodic[a]:
                    coordinate=CastFunc(k-self.shape[a]*sp.floor(CastFunc(k,create_type('double'))/self.shape[a]),create_type('int32'))
                else:
                    coordinate=CastFunc(sp.Min(sp.Max(k,0,evaluate=False),self.shape[a]-1,evaluate=False),create_type('int32'))
                    valid.extend((sp.Ge(k,0,evaluate=False),sp.Lt(k,self.shape[a],evaluate=False)))
                offset.append(coordinate-base[a])
            value=sum(normal[a]*self.initial_field[tuple(offset)](a+1)for a in range(3))-cs*self.initial_field[tuple(offset)](0)
            if valid:value=sp.Piecewise((value,sp.And(*valid)),(sum(n*v for n,v in zip(normal,self.velocity)),True))
            terms.append(weight*value)
        return sum(terms)

    def __call__(self,f_out,f_in,dir_symbol,inv_dir,lb_method,index_field,force_vector):
        cqc=lb_method.conserved_quantity_computation
        if lb_method.dim!=3 or len(lb_method.stencil)!=27 or not cqc.compressible or not cqc.zero_centered_pdfs:
            raise ValueError('Prototype needs compressible zero-centered D3Q27')
        integer_n=[index_field('n0'),index_field('n1'),sp.Integer(0)]
        squared_length=integer_n[0]**2+integer_n[1]**2
        isface=sp.Eq(squared_length,1)
        inverse_length=sp.Piecewise((1,isface),(sp.sqrt(2)/2,True))
        normal=[n*inverse_length for n in integer_n];tangent=[-normal[1],normal[0],sp.Integer(0)]
        cs=sp.sqrt(sp.Rational(1,3));uninf=sum(n*v for n,v in zip(normal,self.velocity))
        weightsum=sp.Piecewise((sp.Rational(1,6),isface),(sp.Rational(11,36),True))
        vn=sp.Piecewise((-1,isface),(-5*sp.sqrt(2)/11,True))
        variance=sp.Piecewise((sp.Rational(1,3),isface),(sp.Rational(8,11),True))
        rho=sp.Symbol('mc_rho');u=sp.symbols('mc_ux mc_uy mc_uz')
        def macro(offset,r,vel):
            g=[f_out[offset](q)for q in range(27)]
            return [Assignment(r,1+sum(g))]+[Assignment(vel[a],sum(c[a]*v for c,v in zip(lb_method.stencil,g))/r)for a in range(3)]
        time_value=TypedSymbol('mc_time',np.float64)
        sub=[Assignment(time_value,CastFunc(self.clock_field.center(dir_symbol),create_type('double'))+1)]+macro((0,0,0),rho,u)
        rm=sp.Symbol('mc_rm');sub.append(Assignment(rm,sum(n*v for n,v in zip(normal,u))-cs*sp.log(rho/self.density)))
        projection=sp.eye(3)-sp.Matrix(normal)*sp.Matrix(normal).T
        utinf=[v-uninf*n for v,n in zip(self.velocity,normal)]
        terms=[]
        for a in range(3):
            edge=index_field(f'edge{a}')
            off1=sp.Piecewise((1,sp.Eq(edge,-1)),(-1,True))
            off2=sp.Piecewise((1,sp.Eq(edge,0)),(-2*edge,True))
            c0=sp.Rational(3,2)*edge
            c1=sp.Piecewise((-sp.Rational(1,2),sp.Eq(edge,0)),(-2*edge,True))
            c2=sp.Piecewise((sp.Rational(1,2),sp.Eq(edge,0)),(sp.Rational(1,2)*edge,True))
            rs=[];vs=[]
            for j,delta in enumerate((off1,off2)):
                offset=[0,0,0];offset[a]=delta;r=sp.Symbol(f'mc_r{a}_{j}');vel=sp.symbols(f'mc_v{a}_{j}_0:3')
                sub+=macro(tuple(offset),r,vel);rs.append(r);vs.append(vel)
            du=[c0*u[b]+c1*vs[0][b]+c2*vs[1][b]for b in range(3)]
            for b in range(3):sub.append(Assignment(sp.Symbol(f'mc_G{a}_{b}'),du[b]))
            rm1=sum(n*v for n,v in zip(normal,vs[0]))-cs*sp.log(rs[0]/self.density)
            rm2=sum(n*v for n,v in zip(normal,vs[1]))-cs*sp.log(rs[1]/self.density)
            terms.append(uninf*sum(projection[a,b]*du[b]for b in range(3))-utinf[a]*(c0*rm+c1*rm1+c2*rm2))
        incoming=sp.Symbol('mc_incoming')
        sub.append(Assignment(incoming,sp.Piecewise((self.incoming_field.center(dir_symbol)+sum(terms),uninf>0),
            (self._transport(index_field,normal,time_value),sp.Eq(uninf,0)),(uninf,True))))
        h=sp.symbols('mc_h0:27')
        for q,c in enumerate(lb_method.stencil):
            missing=sp.Or(*[integer_n[a]*c[a]<0 for a in range(2)])
            source=tuple(-c[a]for a in range(3));foot=tuple(-c[a]-integer_n[a]for a in range(3))
            sub.append(Assignment(h[q],sp.Piecewise((f_out[foot](q),missing),(f_out[source](q),True))))
        rh=sp.Symbol('mc_rhohat');mh=sp.symbols('mc_mhx mc_mhy mc_mhz');unh=sp.Symbol('mc_unhat')
        sub+=[Assignment(rh,1+sum(h))]+[Assignment(mh[a],sum(c[a]*v for c,v in zip(lb_method.stencil,h)))for a in range(3)]
        sub.append(Assignment(unh,sum(n*m for n,m in zip(normal,mh))/rh))
        K=sp.Symbol('mc_K');sub.append(Assignment(K,rh*(unh-vn)/self.density))
        previous=sp.Symbol('mc_logrho0');sub.append(Assignment(previous,sp.log(rh/self.density)))
        for j in range(6):
            z=sp.Symbol(f'mc_newton_z{j}');current=sp.Symbol(f'mc_logrho{j+1}')
            sub+=[Assignment(z,K*sp.exp(-previous)),Assignment(current,previous+(vn+z-cs*previous-incoming)/(z+cs))]
            previous=current
        rb=sp.Symbol('mc_rhob');dr=sp.Symbol('mc_drho');dt=sp.Symbol('mc_dmt');dz=sp.Symbol('mc_dmz')
        mt=sum(t*m for t,m in zip(tangent,mh))
        target_t=sp.Piecewise((sum(t*v for t,v in zip(tangent,self.velocity)),uninf<0),(mt/rh,True))
        target_z=sp.Piecewise((self.velocity[2],uninf<0),(mh[2]/rh,True))
        sub.append(Assignment(rb,self.density*sp.exp(previous)))
        unb=sp.Symbol('fd_unb');ub=sp.symbols('fd_ub0:3')
        sub.append(Assignment(unb,vn+self.density*K/rb))
        for a in range(3):sub.append(Assignment(ub[a],normal[a]*unb+tangent[a]*target_t+(target_z if a==2 else 0)))
        omega=TypedSymbol('omega',np.float64)
        divergence=sum(sp.Symbol(f'mc_G{a}_{a}')for a in range(3))
        stress={}
        for a in range(3):
            for b in range(a,3):
                diagonal=1 if a==b else 0
                strain=sp.Symbol(f'mc_G{a}_{b}')+sp.Symbol(f'mc_G{b}_{a}')
                value=-rb/3*((strain-sp.Rational(2,3)*divergence*diagonal)/omega+sp.Rational(2,3)*divergence*diagonal)
                symbol=sp.Symbol(f'fd_stress{a}_{b}');sub.append(Assignment(symbol,value));stress[a,b]=symbol
        def equilibrium(direction):
            return rb*sp.prod(sp.Rational(2,3)-v*v+(sp.Rational(3,2)*v*v-sp.Rational(1,2))*c*c+c*v/2 for v,c in zip(ub,direction))
        guess=sp.symbols('fd_guess0:27')
        for q,cq in enumerate(lb_method.stencil):
            missing=sp.Or(*[integer_n[a]*cq[a]<0 for a in range(2)])
            value=equilibrium(cq)-lb_method.weights[q]+regularized_neq(ub,stress,cq)
            sub.append(Assignment(guess[q],sp.Piecewise((value,missing),(h[q],True))))
        rg=sp.Symbol('fd_rhog');mg=sp.symbols('fd_mg0:3')
        sub.append(Assignment(rg,1+sum(guess)))
        for a in range(3):sub.append(Assignment(mg[a],sum(cq[a]*v for cq,v in zip(lb_method.stencil,guess))))
        beta=sp.Symbol('fd_beta');alpha=sp.Symbol('fd_alpha')
        sub += [Assignment(dr,rb-rg),Assignment(dt,rb*target_t-sum(t*m for t,m in zip(tangent,mg))),Assignment(dz,rb*target_z-mg[2])]
        dmn=rb*unb-sum(n*m for n,m in zip(normal,mg))
        sub.append(Assignment(beta,sp.Piecewise((0,isface),((dmn-vn*dr)*sp.Rational(121,16),True))))
        sub.append(Assignment(alpha,dr-vn*beta))
        d=NeighbourOffsetArrays.neighbour_offset(dir_symbol,lb_method.stencil)
        c=[CastFunc(-x,create_type('double'))for x in d]
        phi=[sp.Rational(2,3)-v*v/2 for v in c]
        weight=sp.prod(phi)/weightsum
        foot=tuple(d[a]-integer_n[a]for a in range(3))
        correction=weight*(alpha+beta*sum(n*v for n,v in zip(normal,c))+dt*sum(t*v for t,v in zip(tangent,c))/variance+3*dz*c[2])
        main=[Assignment(f_in(inv_dir[dir_symbol]),equilibrium(c)-sp.prod(phi)+regularized_neq(ub,stress,c)+correction),
              Assignment(self.incoming_field.center(dir_symbol),incoming),Assignment(self.clock_field.center(dir_symbol),self.clock_field.center(dir_symbol)+1)]
        # Capture all old-state reads before writing ordered history updates.
        result=AssignmentCollection(main,subexpressions=sub)
        result.topological_sort(sort_main_assignments=False)
        return result


def install_characteristic_farfield(lbm,shape,periodic,velocity):
    if all(periodic):return
    def exterior(x,y,z):
        outside=False
        for a,(p,v)in enumerate(zip(periodic,(x,y,z))):
            if not p:outside=outside|((v<0)|(v>shape[a]))
        return outside
    boundary=CharacteristicFarfield(None,shape,periodic,velocity,stepper=lbm)
    lbm.boundary_handling.set_boundary(boundary,mask_callback=exterior)
