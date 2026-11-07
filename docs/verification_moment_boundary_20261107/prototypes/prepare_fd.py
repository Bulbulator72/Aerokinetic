"""Build a separate FD-stress trial; preserve the rejected exact-moment source."""
from pathlib import Path
import ast
root=Path(__file__).resolve().parent
s=(root/'boundary_moment.py').read_text()
helper=Path('/workspace/aerokinetic_outlet_20261107/boundary_regularized.py').read_text()
helper=helper[helper.index('def regularized_neq('):helper.index('class _ReservoirFarfield')]
s=s.replace('class CharacteristicFarfield(LbBoundary):',helper+'\nclass CharacteristicFarfield(LbBoundary):')
s=s.replace("'OwnedMomentCharacteristic_v1_'","'OwnedMomentFDTrial_'")
old="            du=[c0*u[b]+c1*vs[0][b]+c2*vs[1][b]for b in range(3)]\n"
new=old+"            for b in range(3):sub.append(Assignment(sp.Symbol(f'mc_G{a}_{b}'),du[b]))\n"
assert old in s;s=s.replace(old,new)
start=s.index('        sub += [Assignment(rb,')
end=s.index('        d=NeighbourOffsetArrays',start)
s=s[:start]+'''        sub.append(Assignment(rb,self.density*sp.exp(previous)))
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
''' +s[end:]
s=s.replace("correction=weight*(dr+dt*sum(t*v for t,v in zip(tangent,c))/variance+3*dz*c[2])", "correction=weight*(alpha+beta*sum(n*v for n,v in zip(normal,c))+dt*sum(t*v for t,v in zip(tangent,c))/variance+3*dz*c[2])")
s=s.replace("f_out[foot](inv_dir[dir_symbol])+correction", "equilibrium(c)-sp.prod(phi)+regularized_neq(ub,stress,c)+correction")
ast.parse(s)
(root/'boundary_moment_fd.py').write_text(s)
print('Prepared FD variant; original numerical source unchanged')
