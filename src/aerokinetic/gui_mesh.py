"""Independent mesh draft/preview window for the desktop application."""
from dataclasses import asdict,replace
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,ttk

from ._io import write_json
from .mesh import preview,draw
from .refined_solver import RefinementSettings,refined_project_dict
from .gui_widgets import parameter_panel,parameter_entry


class MeshWindow:
    def __init__(self,app):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
        self.app=app;self.window=tk.Toplevel(app.root)
        self.window.title('AeroKinetic — сеткопостроитель')
        self.window.geometry('1220x800');self.window.minsize(1000,650)
        self.window.bind('<Escape>',lambda event:self.window.destroy())
        panel=parameter_panel(self.window)
        right=ttk.Frame(self.window);right.pack(side='right',fill='both',expand=True)
        self.variables={};self.mesh=None;self.project=None;self.refinement=None
        base=app.result_project if app.busy and app.result_project else app.project_from_form()
        options=app.refinement_from_form() or RefinementSettings()
        self.mode=tk.StringVar(value=app.grid_mode.get())
        ttk.Radiobutton(panel,text='Равномерная сетка',variable=self.mode,value='uniform').grid(row=0,column=0,columnspan=2,sticky='w')
        ttk.Radiobutton(panel,text='Два уровня 2:1',variable=self.mode,value='refined').grid(row=1,column=0,columnspan=2,sticky='w')
        fields=[('Ячеек/хорду крупной сетки','cells_per_chord',base.grid.cells_per_chord),
                ('Перед профилем, c','upstream_chords',base.grid.upstream_chords),
                ('За профилем, c','downstream_chords',base.grid.downstream_chords),
                ('Сверху/снизу, c','transverse_chords',base.grid.transverse_chords),
                ('Периодический размах, c','span_chords',base.grid.span_chords),
                ('Предел памяти, ГиБ','memory_limit_gib',base.grid.memory_limit_gib),
                ('Потоков CPU','openmp_threads',base.grid.openmp_threads),
                ('Мелкая: перед профилем, c','refine_upstream_chords',options.upstream_chords),
                ('Мелкая: за профилем, c','refine_downstream_chords',options.downstream_chords),
                ('Мелкая: сверху/снизу, c','refine_transverse_chords',options.transverse_chords),
                ('Отступ стенки, крупных ячеек','guard_coarse_cells',options.guard_coarse_cells),
                ('τb крупной сетки','coarse_bulk_relaxation_time',options.coarse_bulk_relaxation_time)]
        for row,(label,key,value) in enumerate(fields,2):
            var=tk.StringVar(value=str(value));self.variables[key]=var
            parameter_entry(panel,row,label,var,width=11)
        self.centres=tk.BooleanVar(value=True)
        ttk.Checkbutton(panel,text='Показать центры твёрдых ячеек',variable=self.centres,command=self.redraw).grid(row=14,column=0,columnspan=2,sticky='w',pady=5)
        ttk.Button(panel,text='Построить / проверить',command=self.build).grid(row=15,column=0,columnspan=2,sticky='ew',pady=3)
        self.apply_button=ttk.Button(panel,text='Применить сетку к проекту',command=self.apply)
        self.apply_button.grid(row=16,column=0,columnspan=2,sticky='ew',pady=3)
        ttk.Button(panel,text='Сохранить проект с этой сеткой…',command=self.save).grid(row=17,column=0,columnspan=2,sticky='ew',pady=3)
        ttk.Button(panel,text='Закрыть',command=self.window.destroy).grid(row=18,column=0,columnspan=2,sticky='ew',pady=3)
        self.info=tk.StringVar()
        ttk.Label(panel,textvariable=self.info,wraplength=330,justify='left').grid(row=19,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Label(panel,text='Мелкие ячейки вдвое меньше по всем трём осям.\n'
                  'Параметры сначала проверяются без выделения 3D-полей.\n'
                  'τb — численная объёмная релаксация; это не размер ячейки.',wraplength=330).grid(row=20,column=0,columnspan=2,sticky='w',pady=6)
        bar=ttk.Frame(right);bar.pack(fill='x')
        ttk.Button(bar,text='Вся область',command=lambda:self.redraw(False)).pack(side='left',padx=4,pady=4)
        ttk.Button(bar,text='К профилю',command=lambda:self.redraw(True)).pack(side='left',padx=4,pady=4)
        self.figure=Figure(figsize=(8,6),constrained_layout=True)
        self.canvas=FigureCanvasTkAgg(self.figure,master=right)
        self.canvas.get_tk_widget().pack(fill='both',expand=True)
        self.toolbar=NavigationToolbar2Tk(self.canvas,right,pack_toolbar=False);self.toolbar.pack(fill='x')
        self.zoom=False;self.build();self.set_busy(app.busy)

    def candidate(self):
        app=self.app
        base=app.result_project if app.busy and app.result_project else app.project_from_form()
        values={}
        for key,var in self.variables.items():
            values[key]=int(var.get()) if key in ('cells_per_chord','openmp_threads','guard_coarse_cells') else float(var.get().replace(',','.'))
        grid={key:values[key] for key in asdict(base.grid) if key in values}
        project=replace(base,grid=replace(base.grid,**grid))
        refinement=None
        if self.mode.get()=='refined':
            refinement=RefinementSettings(**{key:values.get('refine_'+key,values.get(key)) for key in asdict(RefinementSettings())})
        return project,refinement

    def build(self):
        try:
            project,refinement=self.candidate()
            mesh=preview(project,refinement)
            self.project,self.refinement,self.mesh=project,refinement,mesh
            plan=mesh.plan;n=project.grid.cells_per_chord
            dx=1/n/(2 if refinement else 1)
            text=[f'Крупная сетка: {plan.shape}',f'Ячеек/хорду: {n}'+(f' → {2*n}' if refinement else ''),
                  f'Мелкая сетка: {mesh.fine_shape}' if refinement else 'Один уровень',
                  f'Минимальный Δx/c: {dx:.7g}',f'Фактический размах: {plan.span:.7g} c',
                  f'Память ≈ {plan.estimated_memory_gib:.2f} ГиБ; предел {project.grid.memory_limit_gib:g}']
            if project.flow.physical_chord_m:text.append(f'Минимальный Δx: {dx*project.flow.physical_chord_m:.7g} м')
            if mesh.fluid_cells is not None:text.append(f'Физических жидких ячеек: {mesh.fluid_cells:,}')
            if mesh.solid_counts:text += [f'{name}: {count} твёрдых ячеек в сечении' for name,count in mesh.solid_counts.items()]
            text.append(mesh.note)
            if plan.estimated_memory_gib>project.grid.memory_limit_gib:text.append('Расчёт превышает заданный предел памяти.')
            self.info.set('\n'.join(text));self.redraw();return True
        except Exception as error:
            self.mesh=None;self.figure.clear();self.canvas.draw_idle()
            self.info.set('Сетка не построена: '+str(error));return False

    def redraw(self,zoom=None):
        if zoom is not None:self.zoom=zoom
        if self.mesh is None:return
        draw(self.figure,self.mesh,body_zoom=self.zoom,show_centres=self.centres.get())
        self.toolbar.update();self.canvas.draw_idle()

    def valid(self):
        return self.build() and not any(count==0 for count in self.mesh.solid_counts.values())

    def apply(self):
        if self.app.busy:
            self.info.set('Расчёт выполняется. Применение сетки доступно после его остановки.');return
        if not self.valid():return
        self.app.populate(self.project,self.refinement)
        self.app.preview_mesh()
        self.info.set(self.info.get()+'\nСетка применена к проекту.')
        self.window.lift()

    def save(self):
        if not self.valid():return
        path=filedialog.asksaveasfilename(parent=self.window,defaultextension='.json',initialfile='project_mesh.json',filetypes=[('Проект','*.json')])
        if path:
            data=self.project.as_dict() if self.refinement is None else refined_project_dict(self.project,self.refinement)
            write_json(Path(path),data)
            self.info.set(self.info.get()+'\nПроект сохранён: '+str(path))

    def set_busy(self,busy):self.apply_button.configure(state='disabled' if busy else 'normal')
