from pathlib import Path
import json
import sympy as sp
import numpy as np
from boundary_regularized import regularized_neq
from lbmpy.stencils import LBStencil
from lbmpy import Stencil
w=Path(__file__).resolve().parent
stencil=list(LBStencil(Stencil.D3Q27));u=sp.symbols('ux uy uz');s=sp.symbols('Sxx Sxy Sxz Syy Syz Szz')
S={(0,0):s[0],(0,1):s[1],(0,2):s[2],(1,1):s[3],(1,2):s[4],(2,2):s[5]}
reg=[regularized_neq(u,S,tuple(sp.Integer(x)for x in c))for c in stencil]
assert sp.expand(sum(reg))==0
for a in range(3):assert sp.expand(sum(c[a]*f for c,f in zip(stencil,reg)))==0
for a in range(3):
 for b in range(a,3):assert sp.expand(sum(c[a]*c[b]*f for c,f in zip(stencil,reg))-S[a,b])==0
# Numeric arbitrary distributions, comparing actual raw moments, not implementation intermediates.
function=sp.lambdify((*u,*s),reg,'numpy');c=np.asarray(stencil,dtype=float);rng=np.random.default_rng(7319)
weights=np.array([np.prod([2/3 if x==0 else 1/6 for x in row])for row in c]);errors=[];filtered=[]
def eq(r,v):
 return r*np.prod(np.array([2/3-v[a]*v[a]+(1.5*v[a]*v[a]-.5)*c[:,a]**2+.5*c[:,a]*v[a]for a in range(3)]),axis=0)
for _ in range(30):
 g=eq(1.02,np.array((.04,.01,.005)))-weights+rng.normal(0,2e-5,27)
 rho=1+g.sum();vel=g@c/rho
 raw=np.einsum('q,qa,qb->ab',g+weights,c,c)
 stress=raw-rho*np.outer(vel,vel)-rho*np.eye(3)/3
 values=[stress[a,b]for a,b in S]
 reconstructed=eq(rho,vel)+np.asarray(function(*vel,*values),dtype=float)
 moments=lambda f:np.r_[f.sum(),f@c,np.einsum('q,qa,qb->ab',f,c,c).ravel()]
 errors.append(float(abs(moments(reconstructed)-moments(g+weights)).max()))
 filtered.append(float(np.linalg.norm(reconstructed-(g+weights))))
assert max(errors)<=2e-13
result={'symbolic_mass_momentum_second_tensor':'passed','numeric_trials':30,'max_raw_moment_error':max(errors),'tolerance':2e-13,'minimum_changed_distribution_l2':min(filtered),'scope':'Conservation/regularization property only; not boundary stability or airfoil validation','passed':True}
(w/'REGULARIZATION_MOMENTS.json').write_text(json.dumps(result,indent=2)+'\n');print(result,flush=True)
