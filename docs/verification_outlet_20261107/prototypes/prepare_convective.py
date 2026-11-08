"""Make a separate reviewed prototype; never modify installed research code."""
from pathlib import Path
import ast
src=Path('/workspace/aerokinetic/src/aerokinetic/farfield.py').read_text()
src=src.replace("'CharacteristicTransportFarfield_'", "'ConvectiveCharacteristicDiagnostic_'")
src=src.replace("[('incoming',create_type('double')),('derivative',create_type('double'))]", "[('incoming',create_type('double'))]")
src=src.replace("data['incoming']=un_inf;data['derivative']=0.;return", "data['incoming']=un_inf;return")
start=src.index('            projection=np.eye(3)-normal[:,None]*normal[None,:]')
end=src.index('\n        return callback', start)
src=src[:start]+"            data['incoming']=rm\n"+src[end:]
start=src.index("        F=sp.Symbol('ff_derivative');terms=[]")
end=src.index('        elif self.glancing:', start)
src=src[:start]+"""        if self.evolve and not self.glancing:
            a=un_inf/sp.sqrt(sum(n*n for n in self.normal))
            value=(index_field('incoming')+a*rm)/(1+a)
"""+src[end:]
src=src.replace("assignments += [Assignment(index_field('incoming'),incoming),Assignment(index_field('derivative'),F)]", "assignments.append(Assignment(index_field('incoming'),incoming))")
ast.parse(src)
assert "index_field('derivative')" not in src
assert "data['derivative']" not in src
Path('/workspace/aerokinetic_outlet_20261107/boundary_convective.py').write_text(src)
