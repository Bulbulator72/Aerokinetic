"""Read-only structured-mesh planning/preview; never allocate a 3D LBM state."""
from dataclasses import dataclass
import math

import numpy as np


@dataclass
class MeshPreview:
    plan: object
    contours: tuple
    fine_origin: tuple | None
    fine_shape: tuple | None
    solid_centres: np.ndarray
    solid_counts: dict
    solid_count: int | None
    fluid_cells: int | None
    solid_marker_stride: int
    note: str


def preview(project,refinement=None,max_body_cells=2_000_000):
    if refinement is None:plan=project.plan()
    else:
        from .refined_solver import RefinedPlan
        plan=RefinedPlan.create(project,refinement)
    contours=project.section.normalized(project.flow.alpha_deg)
    points=np.concatenate([e.xy for e in contours])
    origin=np.array(plan.origin)
    shape=plan.shape
    dx=1/project.grid.cells_per_chord
    fine_origin=fine_shape=None
    if refinement is not None:
        fine_origin=tuple(origin+np.array(plan.box.lower[:2])*dx)
        fine_shape=tuple(2*n for n in plan.box.shape)
        origin=np.array(fine_origin);shape=fine_shape;dx/=2
    lo=np.maximum(0,np.floor((points.min(axis=0)-origin)/dx).astype(int)-1)
    hi=np.minimum(shape[:2],np.ceil((points.max(axis=0)-origin)/dx).astype(int)+1)
    extent=np.maximum(hi-lo,0)
    if math.prod(map(int,extent))>max_body_cells:
        return MeshPreview(plan,contours,fine_origin,fine_shape,np.empty((0,2)),{},None,None,1,
             'Слишком много ячеек тела для предпросмотра; классификация не выполнена. Линии сетки и оценка памяти доступны.')
    x,y=(origin[a]+(np.arange(lo[a],hi[a])+.5)*dx for a in (0,1))
    xx,yy=np.meshgrid(x,y,indexing='ij')
    solid=np.zeros(xx.shape,bool);counts={}
    for index,element in enumerate(contours):
        mask=element.contains(xx,yy)
        counts[f'{index+1}: {element.name}']=int(np.count_nonzero(mask))
        solid|=mask
    total=int(np.count_nonzero(solid))
    centres=np.column_stack((xx[solid],yy[solid]))
    stride=max(1,math.ceil(len(centres)/50_000))
    centres=centres[::stride]
    fluid=math.prod(plan.shape)-total*shape[2]
    if fine_shape is not None:
        fluid=math.prod(plan.shape)-math.prod(plan.box.shape)+math.prod(fine_shape)-total*fine_shape[2]
    missing=[name for name,count in counts.items() if count==0]
    note=('Не представлены твёрдыми ячейками: '+', '.join(missing)) if missing else 'Все элементы имеют твёрдые ячейки. Разрешение пограничного слоя этим не подтверждено.'
    return MeshPreview(plan,contours,fine_origin,fine_shape,centres,counts,total,fluid,stride,note)


def _lines(origin,shape,dx,box=None,max_lines=3000):
    stride=max(1,math.ceil((shape[0]+shape[1]+2)/max_lines))
    lower=np.asarray(origin);upper=lower+np.array(shape[:2])*dx
    segments=[]
    for axis in (0,1):
        other=1-axis
        indices=np.unique(np.r_[np.arange(0,shape[axis]+1,stride),shape[axis]])
        for index in indices:
            value=lower[axis]+index*dx
            ranges=[(lower[other],upper[other])]
            if box is not None and box[0][axis]<value<box[1][axis]:
                ranges=[(lower[other],box[0][other]),(box[1][other],upper[other])]
            for first,last in ranges:
                a=np.empty(2);b=np.empty(2)
                a[axis]=b[axis]=value;a[other]=first;b[other]=last
                segments.append((a,b))
    return segments,stride


def draw(figure,mesh,*,body_zoom=False,show_centres=True):
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Polygon,Rectangle
    figure.clear();ax=figure.add_subplot()
    plan=mesh.plan
    dx=plan.coarse_level.dx if mesh.fine_shape is not None else plan.dx
    box=None
    if mesh.fine_shape is not None:
        lower=np.array(mesh.fine_origin);upper=lower+np.array(mesh.fine_shape[:2])*dx/2
        box=(lower,upper)
    lines,stride=_lines(plan.origin,plan.shape,dx,box)
    ax.add_collection(LineCollection(lines,colors='#8095a8',linewidths=.35,label='Крупная сетка' if box else 'Сетка'))
    fine_stride=1
    if box:
        lines,fine_stride=_lines(mesh.fine_origin,mesh.fine_shape,dx/2)
        ax.add_collection(LineCollection(lines,colors='#2c8378',linewidths=.35,label='Мелкая сетка'))
        ax.add_patch(Rectangle(box[0],*(box[1]-box[0]),fill=False,edgecolor='#176a60',linestyle='--',linewidth=1.2))
    for element in mesh.contours:
        ax.add_patch(Polygon(element.xy,closed=True,facecolor='#dddddd',edgecolor='#202020',linewidth=.9,zorder=3))
    if show_centres and len(mesh.solid_centres):
        ax.scatter(*mesh.solid_centres.T,s=3,color='#b84534',marker='.',zorder=4,label='Центры твёрдых ячеек')
    lower=np.array(plan.origin);upper=lower+np.array(plan.shape[:2])*dx
    if body_zoom:
        xy=np.concatenate([e.xy for e in mesh.contours]);lower=xy.min(axis=0)-.08;upper=xy.max(axis=0)+.08
    ax.set(xlim=(lower[0],upper[0]),ylim=(lower[1],upper[1]),aspect='equal',
           xlabel='x/c от точки момента',ylabel='y/c',title='Сечение расчётной сетки; геометрия неподвижна')
    note=[]
    if stride>1:note.append(f'крупная: каждая {stride}-я линия')
    if fine_stride>1:note.append(f'мелкая: каждая {fine_stride}-я линия')
    if mesh.solid_marker_stride>1:note.append(f'центры: каждый {mesh.solid_marker_stride}-й')
    if note:ax.set_title(ax.get_title()+'\nПредпросмотр: '+', '.join(note),fontsize=10)
    ax.legend(loc='upper right',fontsize=8)
    return ax
