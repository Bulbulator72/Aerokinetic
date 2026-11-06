"""Live component residuals and editable pre-run stopping conditions."""
from dataclasses import asdict
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from .convergence import CHANNELS,StopCriteria
from .gui_widgets import parameter_panel,parameter_entry

REASONS={'duration_reached':'Достигнуто заданное время','residual_targets_met':'Достигнуты все пороги невязок',
         'max_steps_reached':'Достигнут предел шагов','wall_time_limit':'Достигнут лимит времени запуска',
         'user_stopped':'Остановлено пользователем','failed':'Расчёт завершён с ошибкой'}


class ConvergenceWindow:
    def __init__(self,app):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
        self.app=app;self.window=tk.Toplevel(app.root)
        self.window.title('AeroKinetic — невязки и условия окончания расчёта')
        self.window.bind('<Escape>',lambda event:self.window.destroy())
        self.window.geometry('1200x800');self.window.minsize(1050,680)
        left=parameter_panel(self.window)
        right=ttk.Frame(self.window);right.pack(side='right',fill='both',expand=True)
        self.variables={};self.widgets=[]
        current=app.termination_settings
        self.mode=tk.StringVar(value=current.mode)
        for row,(label,value) in enumerate((('По заданному времени / лимитам','time'),('Также по невязкам стационарности','steady'))):
            widget=ttk.Radiobutton(left,text=label,variable=self.mode,value=value)
            widget.grid(row=row,column=0,columnspan=2,sticky='w',pady=3);self.widgets.append(widget)
        fields=[('Масса: порог L2','mass_tolerance',current.mass_tolerance),
                ('Импульс X: порог L2','momentum_x_tolerance',current.momentum_x_tolerance),
                ('Импульс Y: порог L2','momentum_y_tolerance',current.momentum_y_tolerance),
                ('Импульс Z: порог L2','momentum_z_tolerance',current.momentum_z_tolerance),
                ('Проверять после tU/c','minimum_t_star',current.minimum_t_star),
                ('Последовательных проверок','consecutive_checks',current.consecutive_checks),
                ('Предел крупных шагов (0 — нет)','max_steps',current.max_steps),
                ('Лимит запуска, с (0 — нет)','wall_time_limit_s',current.wall_time_limit_s),
                ('Предельное время tU/c','duration_convective',app.variables['duration_convective'].get()),
                ('Интервал невязок, шагов','check_every',app.variables['check_every'].get())]
        for row,(label,key,value) in enumerate(fields,2):
            var=tk.StringVar(value=str(value));self.variables[key]=var
            _,widget=parameter_entry(left,row,label,var,width=12)
            self.widgets.append(widget)
        apply=ttk.Button(left,text='Применить условия',command=self.apply)
        apply.grid(row=12,column=0,columnspan=2,sticky='ew',pady=7);self.widgets.append(apply)
        ttk.Button(left,text='Закрыть',command=self.window.destroy).grid(row=13,column=0,columnspan=2,sticky='ew',pady=3)
        ttk.Label(left,text='Для автоостанова по невязкам должны одновременно выполняться все четыре порога, '
                  'минимальное время и число проверок. Лимит секунд относится к одному запуску после компиляции.',
                  wraplength=330).grid(row=14,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Label(left,text='Невязки стационарности — изменение консервативных полей за Δt*, с весами по объёму жидкости. '
                  'При физически нестационарном течении они могут не стремиться к нулю. '
                  'По умолчанию остановка задаётся временем. Уравнения энергии в этой изотермической модели нет.',
                  wraplength=330).grid(row=15,column=0,columnspan=2,sticky='w',pady=8)
        self.note=tk.StringVar()
        ttk.Label(left,textvariable=self.note,wraplength=330).grid(row=16,column=0,columnspan=2,sticky='w',pady=7)
        self.view=tk.StringVar(value='Стационарность L2')
        choice=ttk.Combobox(right,textvariable=self.view,state='readonly',values=('Стационарность L2','Стационарность L∞','Интегральный баланс'))
        choice.pack(fill='x',padx=5,pady=5);choice.bind('<<ComboboxSelected>>',lambda event:self.refresh())
        self.figure=Figure(figsize=(8,6),constrained_layout=True)
        self.canvas=FigureCanvasTkAgg(self.figure,master=right);self.canvas.get_tk_widget().pack(fill='both',expand=True)
        toolbar=NavigationToolbar2Tk(self.canvas,right,pack_toolbar=False);toolbar.pack(fill='x')
        self.table=ttk.Treeview(right,columns=('component','l2','linf','limit'),show='headings',height=4)
        for key,label in zip(('component','l2','linf','limit'),('Компонента','L2','L∞','Порог L2')):
            self.table.heading(key,text=label);self.table.column(key,width=110,anchor='center')
        self.table.pack(fill='x',padx=5,pady=5)
        self.set_busy(app.busy);self.refresh()

    def apply(self):
        if self.app.busy:return
        try:
            values={key:(int(self.variables[key].get()) if key in ('consecutive_checks','max_steps')
                         else float(self.variables[key].get().replace(',','.'))) for key in asdict(StopCriteria()) if key!='mode'}
            criteria=StopCriteria(mode=self.mode.get(),**values)
            duration=float(self.variables['duration_convective'].get().replace(',','.'))
            interval=int(self.variables['check_every'].get())
            if not math.isfinite(duration) or duration<=0 or interval<1:raise ValueError('Задайте положительные время и интервал')
            self.app.termination_settings=criteria
            self.app.variables['duration_convective'].set(str(duration));self.app.variables['check_every'].set(str(interval))
            self.note.set('Условия применены к проекту. Сохраните проект для повторного запуска.')
            self.refresh(update_note=False)
        except Exception as error:self.note.set('Условия не применены: '+str(error))

    def set_busy(self,busy):
        for widget in self.widgets:widget.configure(state='disabled' if busy else 'normal')

    def sync_from_project(self):
        self.mode.set(self.app.termination_settings.mode)
        for key,value in asdict(self.app.termination_settings).items():
            if key in self.variables:self.variables[key].set(str(value))
        for key in ('duration_convective','check_every'):
            self.variables[key].set(self.app.variables[key].get())
        self.refresh()

    def refresh(self,update_note=True):
        data=self.app.residual_records
        self.figure.clear();self.table.delete(*self.table.get_children())
        criteria=self.app.termination_settings
        if self.app.busy and self.app.result_project:criteria=self.app.result_project.run.termination
        if not data:
            ax=self.figure.add_subplot();ax.axis('off')
            ax.text(.5,.5,'Невязки появятся после первой проверки поля.\nСтарые результаты без residuals.csv их не содержат.',ha='center',va='center',transform=ax.transAxes)
            self.canvas.draw_idle();return
        prefix={'Стационарность L2':'l2','Стационарность L∞':'linf','Интегральный баланс':'balance'}[self.view.get()]
        labels=('Масса ρ','Импульс ρuₓ','Импульс ρuᵧ','Импульс ρu_z')
        for ax,channel,label,limit in zip(self.figure.subplots(2,2).flat,CHANNELS,labels,criteria.thresholds):
            chosen=[r for r in data if prefix+'_'+channel in r]
            if chosen:
                ax.semilogy([r['t_star'] for r in chosen],np.maximum([r[prefix+'_'+channel] for r in chosen],1e-18),linewidth=1)
                if prefix=='l2':ax.axhline(limit,color='#bb7132',linestyle='--',linewidth=.8)
            else:ax.text(.5,.5,'Нет данных баланса\nдля этого режима',ha='center',va='center',transform=ax.transAxes)
            ax.set(title=label,xlabel='tU/c',ylabel=prefix);ax.grid(alpha=.25)
        last=data[-1]
        for channel,label,limit in zip(CHANNELS,labels,criteria.thresholds):
            self.table.insert('', 'end',values=(label,f"{last['l2_'+channel]:.5g}",f"{last['linf_'+channel]:.5g}",f'{limit:.5g}'))
        self.canvas.draw_idle()
        if update_note:
            self.note.set(f"Последняя проверка: шаг {int(last['step'])}, tU/c={last['t_star']:.6g}.\n"
                          f"Успешных подряд: {int(last['streak'])}/{criteria.consecutive_checks}.\n"
                          'Значения 0 на логарифмическом графике показаны на уровне 10⁻¹⁸. '
                          'Баланс не используется для автоостанова.')
