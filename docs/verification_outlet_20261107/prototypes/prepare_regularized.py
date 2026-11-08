from pathlib import Path
w=Path(__file__).resolve().parent
s=Path('/workspace/aerokinetic/src/aerokinetic/farfield.py').read_text().replace('CharacteristicTransport_','CharacteristicRegularized_')
helper='''
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

'''
pos=s.index('class _ReservoirFarfield');s=s[:pos]+helper+s[pos:]
needle="        assignments=[Assignment(f_in(inv_dir[dir_symbol]),f_out[foot](inv_dir[dir_symbol])+eq(rhob,ub)-eq(rho,u))]"
assert needle in s
s=s.replace(needle,'''        stress={}
        g=[f_out[foot](i)for i in range(27)]
        for a in range(3):
            for b in range(a,3):
                value=sum(c[a]*c[b]*p for c,p in zip(lb_method.stencil,g))-rho*u[a]*u[b]
                if a==b:value-=sp.Rational(1,3)*(rho-1)
                symbol=sp.Symbol(f'ff_stress_{a}_{b}');sub.append(Assignment(symbol,value));stress[a,b]=symbol
        direction=[CastFunc(-d[a],create_type('double'))for a in range(3)]
        neq=regularized_neq(u,stress,direction)
        assignments=[Assignment(f_in(inv_dir[dir_symbol]),eq(rhob,ub)-eq(1,(0,0,0))+neq)]''')
(w/'boundary_regularized.py').write_text(s)
p=w/'time_probe.py';s=p.read_text().replace("('original','euler','ab2','constant')","('original','euler','ab2','constant','regularized')")
(w/'reconstruction_probe.py').write_text(s)
