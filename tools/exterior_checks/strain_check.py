"""Recover prescribed polynomial strain stress from all27 reconstructed PDFs."""
import hashlib,json
from pathlib import Path
import numpy as np
from exterior_fd import ExteriorFD

m=ExteriorFD((16,20,8),(False,False,True))
x,y,z=np.meshgrid(*(np.arange(n)-p+.5 for n,p in zip(m.shape,m.pad)),indexing='ij')
a,b,c,d,e=1e-6,2e-7,-3e-7,4e-8,5e-7
state=np.zeros(m.shape+(4,));state[...,0]=1e-6*x-2e-6*y
state[...,1]=.04+a*x+b*y*y;state[...,2]=c*x*y;state[...,3]=d*x*x+e*y
px=x[m.index];py=y[m.index];G=np.zeros((len(px),3,3))
G[:,0,0]=a;G[:,1,0]=2*b*py;G[:,0,1]=c*py;G[:,1,1]=c*px;G[:,0,2]=2*d*px;G[:,1,2]=e
gradient_error=float(np.max(abs(m.gradients(state)[:,:,1:]-G)))
pdf,moment_error=m.regularized(state);rho=np.exp(state[m.index][:,0]);u=state[m.index][:,1:]
measured=np.einsum('nq,qa,qb->nab',pdf,m.c,m.c)-rho[:,None,None]*u[:,:,None]*u[:,None,:]-(rho-1)[:,None,None]*np.eye(3)/3
div=np.trace(G,axis1=1,axis2=2);strain=G+np.swapaxes(G,1,2)
tau=1/m.s.kernel_params['omega'];expected=-rho[:,None,None]/3*(tau*(strain-2/3*div[:,None,None]*np.eye(3))+2/3*div[:,None,None]*np.eye(3))
stress_error=float(np.max(abs(measured-expected)))
r={'polynomial_velocity_gradient_max_error':gradient_error,'reconstruction_mass_momentum_max_error':moment_error,
   'recovered_FD_stress_tensor_max_error':stress_error,'tolerance':2e-12,'passed':max(gradient_error,moment_error,stress_error)<=2e-12,
   'source_sha256':hashlib.sha256(Path(__file__).with_name('exterior_fd.py').read_bytes()).hexdigest()}
Path(__file__).with_name('STRAIN_CHECKS.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
