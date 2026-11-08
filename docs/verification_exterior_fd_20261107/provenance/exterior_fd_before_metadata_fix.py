"""Research full-PDF regularized characteristic exterior, outside a fixed core.

Bulk collision/streaming are the original lbmpy D3Q27 cumulants. One added
exterior layer carries an explicit open-boundary source; core PDFs are
never overwritten. This is an unvalidated research prototype.
"""
import itertools
import numpy as np
import sympy as sp
from lbmpy import LBMConfig,Method,Stencil
from lbmpy.stencils import LBStencil
from lbmpy.lbstep import LatticeBoltzmannStep
from lbmpy.boundaries import UBB
from pystencils import CreateKernelConfig

CS=np.sqrt(1/3)
NU=.04*32/30000


def lattice(shape,periodic,order='collide_stream'):
    return LatticeBoltzmannStep(domain_size=shape,periodicity=periodic,time_step_order=order,
        lbm_config=LBMConfig(stencil=LBStencil(Stencil.D3Q27),method=Method.CUMULANT,
            relaxation_rate=sp.Symbol('omega'),compressible=True,zero_centered=True),
        kernel_params={'omega':1/(.5+3*NU)},config=CreateKernelConfig(data_type='float64'))


class ExteriorFD:
    def __init__(self,core_shape,periodic,velocity=(.04,0,0)):
        self.core_shape=tuple(core_shape);self.periodic=tuple(periodic);self.base=np.asarray(velocity,dtype=float)
        assert periodic[2]and min(core_shape)>=4
        self.pad=np.array([0 if p else 1 for p in periodic])
        self.shape=tuple(np.asarray(core_shape)+2*self.pad)
        self.s=lattice(self.shape,periodic)
        assert len(self.s._lbmKernels)==2
        self.c=np.asarray(list(self.s.method.stencil));self.weights=np.asarray(list(map(float,self.s.method.weights)))
        def mask(x,y,z):
            outside=False
            for a,(p,v)in enumerate(zip(periodic,(x,y,z))):
                if not p:outside=outside|((v<0)|(v>self.shape[a]))
            return outside
        self.s.boundary_handling.set_boundary(UBB(tuple(self.base),name='exterior_reservoir_ubb'),mask_callback=mask)
        self.core=tuple(slice(int(p),int(p+n))for p,n in zip(self.pad,core_shape))
        self.core_pdf=tuple(slice(int(1+p),int(1+p+n))for p,n in zip(self.pad,core_shape))
        outer=np.zeros(self.shape,dtype=bool)
        for a,p in enumerate(periodic):
            if not p:
                sel=[slice(None)]*3;sel[a]=0;outer[tuple(sel)]=True;sel[a]=-1;outer[tuple(sel)]=True
        self.coords=np.column_stack(np.nonzero(outer));self.index=tuple(self.coords.T)
        self.pdf_index=tuple((self.coords+1).T)
        nv=np.zeros_like(self.coords)
        for a,p in enumerate(periodic):
            if not p:nv[:,a]=np.where(self.coords[:,a]==0,-1,np.where(self.coords[:,a]==self.shape[a]-1,1,0))
        self.integer_normal=nv;self.ell=np.linalg.norm(nv,axis=-1);self.n=nv/self.ell[:,None]
        self.uninf=self.n@self.base;self.utinf=self.base-self.uninf[:,None]*self.n
        self.inner=tuple((self.coords-nv).T);self.step_count=0;self.initial=None
        for name in (self.s.pdf_array_name,self.s._tmp_arr_name):self.s.data_handling.cpu_arrays[name].fill(0)

    def initialize(self,pulse=None):
        # pulse receives actual physical coordinates, including exterior cells.
        for block in self.s.data_handling.iterate(ghost_layers=True):
            block[self.s.density_data_name][...]=1
            block[self.s.velocity_data_name][...]=self.base
            if pulse:
                coords=tuple(x-p for x,p in zip(block.midpoint_arrays,self.pad))
                pulse(block,coords)
        self.s.set_pdf_fields_from_macroscopic_values();self.s.pre_run()

    def state(self):
        self.s.post_run()
        rho=self.s.data_handling.gather_array(self.s.density_data_name)
        u=self.s.data_handling.gather_array(self.s.velocity_data_name)
        return np.concatenate((np.log(rho)[...,None],u),axis=-1)

    def macros(self,full=False):
        state=self.state()
        if not full:state=state[self.core]
        return np.exp(state[...,0]),state[...,1:]

    def gradients(self,state):
        p=self.coords;result=np.empty((len(p),3,4));center=state[self.index]
        for a,periodic in enumerate(self.periodic):
            if periodic:
                one=p.copy();two=p.copy();one[:,a]=(one[:,a]-1)%self.shape[a];two[:,a]=(two[:,a]+1)%self.shape[a]
                result[:,a]=(state[tuple(two.T)]-state[tuple(one.T)])/2
            else:
                edge=np.where(p[:,a]==0,-1,np.where(p[:,a]==self.shape[a]-1,1,0))
                one=p.copy();two=p.copy();one[:,a]+=np.where(edge==-1,1,-1);two[:,a]+=np.where(edge==0,1,-2*edge)
                c0=1.5*edge;c1=np.where(edge==0,-.5,-2*edge);c2=np.where(edge==0,.5,.5*edge)
                result[:,a]=c0[:,None]*center+c1[:,None]*state[tuple(one.T)]+c2[:,None]*state[tuple(two.T)]
        return result

    def traced_incoming(self,time):
        target=self.coords-self.base*time;lower=np.floor(target).astype(int);fraction=target-lower
        answer=np.zeros(len(target))
        for bit in itertools.product((0,1),repeat=3):
            q=lower+bit;valid=np.ones(len(q),dtype=bool);weight=np.ones(len(q))
            for a,b in enumerate(bit):
                weight*=fraction[:,a]if b else 1-fraction[:,a]
                if self.periodic[a]:q[:,a]%=self.shape[a]
                else:
                    valid&=(q[:,a]>=0)&(q[:,a]<self.shape[a]);q[:,a]=np.clip(q[:,a],0,self.shape[a]-1)
            v=self.initial[tuple(q.T)]
            incoming=np.sum(v[:,1:]*self.n,axis=-1)-CS*v[:,0]
            answer+=weight*np.where(valid,incoming,self.uninf)
        return answer

    def enforce(self,y,time):
        y=y.copy();un=np.sum(y[:,1:]*self.n,axis=-1);rp=un+CS*y[:,0]
        inflow=self.uninf<0;glancing=self.uninf==0;selected=inflow|glancing
        rm=np.where(inflow,self.uninf,self.traced_incoming(time))
        y[selected,0]=(rp[selected]-rm[selected])/(2*CS)
        newun=(rp+rm)/2
        tangent=y[:,1:]-un[:,None]*self.n
        tangent[inflow]=self.utinf[inflow]
        y[selected,1:]=newun[selected,None]*self.n[selected]+tangent[selected]
        return y

    def rhs(self,y,current):
        stage=current.copy();stage[self.index]=y;g=self.gradients(stage);G=g[:,:,1:];dq=g[:,:,0]
        un=np.sum(y[:,1:]*self.n,axis=-1);rp=un+CS*y[:,0]
        inside=stage[self.inner];uin=inside[:,1:];unin=np.sum(uin*self.n,axis=-1)
        rpin=unin+CS*inside[:,0]
        div=np.trace(G,axis1=1,axis2=2)-np.einsum('ia,iab,ib->i',self.n,G,self.n)
        gradun=np.einsum('iab,ib->ia',G,self.n)
        rminus=self.uninf*div-np.sum(self.utinf*(gradun-CS*dq),axis=-1)
        rplus=-(self.uninf+CS)*(rp-rpin)/self.ell-np.sum(self.utinf*(gradun+CS*dq),axis=-1)-CS*div
        tangent=y[:,1:]-un[:,None]*self.n;tangentin=uin-unin[:,None]*self.n
        adv=np.einsum('ia,iab->ib',self.utinf,G);adv-=np.sum(adv*self.n,axis=-1)[:,None]*self.n
        pressure=dq-np.sum(dq*self.n,axis=-1)[:,None]*self.n
        dt=-self.uninf[:,None]*(tangent-tangentin)/self.ell[:,None]-adv-CS**2*pressure
        return np.concatenate((((rplus-rminus)/(2*CS))[:,None],self.n*((rplus+rminus)/2)[:,None]+dt),axis=-1)

    def predict(self,current):
        y0=current[self.index];time=self.step_count
        y1=self.enforce(y0+self.rhs(y0,current),time+1)
        y2=self.enforce(.75*y0+.25*(y1+self.rhs(y1,current)),time+.5)
        y3=self.enforce((y0+2*(y2+self.rhs(y2,current)))/3,time+1)
        if not np.isfinite(y3).all():raise RuntimeError('Non-finite exterior macro predictor')
        return y3

    def regularized(self,state):
        y=state[self.index];rho=np.exp(y[:,0]);u=y[:,1:];G=self.gradients(state)[:,:,1:]
        divergence=np.trace(G,axis1=1,axis2=2);omega=self.s.kernel_params['omega']
        stress=-rho[:,None,None]/3*((G+np.swapaxes(G,1,2))/omega
            +np.eye(3)[None,:,:]*(2/3*divergence*(1-1/omega))[:,None,None])
        c=self.c[None,:,:];v=u[:,None,:]
        phi=2/3-v*v+(1.5*v*v-.5)*c*c+c*v/2
        first=-2*v+3*v*c*c+c/2;second=1.5*c*c-1
        pdf=rho[:,None]*np.prod(phi,axis=-1)-self.weights
        for a in range(3):pdf+=stress[:,a,a,None]*second[:,:,a]*np.prod(phi[:,:,[j for j in range(3)if j!=a]],axis=-1)
        for a in range(3):
            for b in range(a+1,3):
                j=3-a-b
                pdf+=stress[:,a,b,None]*first[:,:,a]*first[:,:,b]*phi[:,:,j]
        mass_error=float(np.max(abs(1+pdf.sum(axis=-1)-rho)))
        momentum_error=float(np.max(abs(pdf@self.c-rho[:,None]*u)))
        return pdf,max(mass_error,momentum_error)

    def totals(self):
        g=self.s.data_handling.cpu_arrays[self.s.pdf_array_name][self.core_pdf]
        return np.r_[np.prod(self.core_shape)+g.sum(),np.einsum('...q,qa->a',g,self.c,optimize=True)]

    def core_flux(self):
        pdf=self.s.data_handling.cpu_arrays[self.s.pdf_array_name]
        owned=pdf[self.core_pdf];coordinates=np.ogrid[tuple(slice(0,n)for n in self.core_shape)]
        result=np.zeros(4)
        for q,c in enumerate(self.c):
            leaving=np.zeros(self.core_shape,dtype=bool);entering=leaving.copy()
            for a,p in enumerate(self.periodic):
                if not p:
                    leaving|=(coordinates[a]+c[a]<0)|(coordinates[a]+c[a]>=self.core_shape[a])
                    entering|=(coordinates[a]-c[a]<0)|(coordinates[a]-c[a]>=self.core_shape[a])
            shifted=tuple(slice(int(1+self.pad[a]-c[a]),int(1+self.pad[a]+self.core_shape[a]-c[a]))for a in range(3))
            incoming=pdf[shifted+(q,)]+self.weights[q];outgoing=owned[...,q]+self.weights[q]
            dm=incoming[entering].sum()-outgoing[leaving].sum()
            result+=dm*np.r_[1.,c]
        return result

    def step(self,audit=False):
        s=self.s;current=self.state()
        if self.initial is None:self.initial=current.copy()
        predicted=self.predict(current)
        before=self.totals()if audit else None
        s.data_handling.run_kernel(s._lbmKernels[0],**s.kernel_params)
        s._sync_src();s.boundary_handling(**s.kernel_params)
        flux=self.core_flux()if audit else None
        s.data_handling.run_kernel(s._lbmKernels[1],**s.kernel_params)
        s.data_handling.swap(s._pdf_arr_name,s._tmp_arr_name,False)
        after_stream=self.totals()if audit else None
        nextstate=self.state();nextstate[self.index]=predicted
        rebuilt,moment_error=self.regularized(nextstate)
        pdf=s.data_handling.cpu_arrays[s.pdf_array_name]
        old=pdf[self.pdf_index].copy()
        core_before=pdf[self.core_pdf].copy()if audit else None
        pdf[self.pdf_index]=rebuilt
        self.step_count+=1
        dm=rebuilt-old
        report={'step':self.step_count,'reconstruction_moment_error':moment_error,
                'exterior_source_mass_momentum':np.r_[dm.sum(),np.einsum('nq,qa->a',dm,self.c,optimize=True)].tolist()}
        if audit:
            report.update(core_budget_per_cell=((after_stream-before-flux)/np.prod(self.core_shape)).tolist(),
                core_pdf_overwrite_max=float(np.max(abs(pdf[self.core_pdf]-core_before))),core_flux=flux.tolist())
        return report
