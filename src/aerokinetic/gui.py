"""Offline desktop application. Numerical work runs outside the Tk event loop."""
from dataclasses import asdict,replace
from datetime import datetime
import csv
import json
import math
from pathlib import Path
import queue
import threading
import traceback
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import numpy as np

from . import __version__
from ._io import write_json
from .config import Flow, Grid
from .geometry import Contour, Section, naca4
from .project import Project, RunSettings
from .convergence import StopCriteria
from .gui_style import FormInputError


class Application:
    def __init__(self, root):
        self.root = root
        self.variables = {}
        self.imported = None
        self.output = None
        self.result_project = None
        self.result_refinement = None
        self.worker = None
        self.busy = False
        self.operation = None
        self.stopping = threading.Event()
        self.messages = queue.Queue()
        self.closing = False
        self.records = []
        self.residual_records = []
        self.project_template = None
        self.termination_settings = StopCriteria()
        self.mesh_window = None
        self.residual_window = None
        self.refinement_template = None
        self.history_average_after = 0.0
        self.frame_files = []
        self.target_steps = 1
        self.temporal_report = None
        self.busy_widgets = []
        self.current_section = None
        self.document_path = None
        self.document_readonly = False
        self.dirty = False
        self._tracking = False
        self._refresh_pending = None
        self._error_key = None
        self.mesh_preview = None
        self.mesh_zoom = False
        self.status = tk.StringVar(value="Подготовьте проект и проверьте оценку памяти.")
        from .gui_layout import build
        build(self)
        self.install_menu()
        self._baseline = self.draft_signature()
        for var in (*self.variables.values(), self.source, self.closed_te, self.sgs,
                    self.grid_mode, self.project_title):
            var.trace_add("write", self.draft_changed)
        self._tracking = True
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self.poll)
        self.show_section("geometry")
        self.preview()
        self.draw_history()
        self.empty_plot("field", "Поле появится после записи первого кадра.\nОткройте результат или начните расчёт.")
        self.empty_plot("statistics", "Средние появятся после расчёта или загрузки результата.")
        self.draw_experiment()
        self.mark_clean()

    def install_menu(self):
        menu = tk.Menu(self.root)
        self.root.configure(menu=menu)
        file = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="Файл", menu=file)
        self.file_menu = file
        for label, command, accelerator in (
            ("Новый проект", self.new, "Ctrl+N"), ("Открыть проект…", self.open_project, "Ctrl+O"),
            ("Сохранить", self.save_project, "Ctrl+S"), ("Сохранить как…", lambda: self.save_project(save_as=True), "Ctrl+Shift+S"),
            ("Открыть результат…", self.open_output, ""), ("Импортировать DAT…", self.import_dat, "")):
            file.add_command(label=label, command=command, accelerator=accelerator)
        file.add_separator()
        file.add_command(label="Выход", command=self.close)
        view = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="Разделы", menu=view)
        from .gui_layout import SECTIONS
        for key, (title, _) in SECTIONS.items():
            view.add_command(label=title, command=lambda k=key: self.show_section(k))
        view.add_separator()
        view.add_command(label="Сеткопостроитель…", command=self.open_mesh)
        view.add_command(label="Невязки и условия окончания…", command=self.open_convergence)
        self.bindings = {}
        for sequence, command in (("<Control-n>", self.new), ("<Control-o>", self.open_project),
                                  ("<Control-s>", self.save_project), ("<Control-Shift-S>", lambda: self.save_project(save_as=True)),
                                  ("<F5>", self.refresh_view)):
            def invoke(event, action=command):
                if not self.busy:
                    action()
                return "break"
            self.root.bind(sequence, invoke)
            self.bindings[sequence] = invoke

    def show_section(self, section, view=None, build_mesh=True):
        from .gui_layout import SECTIONS
        self.current_section = section
        self.workspace_title.set(SECTIONS[section][0])
        self.workspace_description.set(SECTIONS[section][1])
        for key, button in self.nav_buttons.items():
            button.configure(style="Selected.Nav.TButton" if key == section else "Nav.TButton")
        for page in self.form_pages.values():
            page.pack_forget()
        panes = tuple(str(p) for p in self.panes.panes())
        if section == "results":
            if str(self.form_host) in panes:
                self._form_width = self.panes.sashpos(0)
                self.panes.forget(self.form_host)
        else:
            if str(self.form_host) not in panes:
                self.panes.insert(0, self.form_host, weight=0)
                self.panes.sashpos(0, getattr(self, '_form_width', 356))
            self.form_pages[section].pack(fill="both", expand=True)
        visible = ("geometry",) if section == "geometry" else ("mesh",) if section == "mesh" else (
            "history", "statistics", "field", "experiment", "log")
        for key, frame in self.tab_frames.items():
            if key in visible:
                self.tabs.add(frame)
            else:
                self.tabs.hide(frame)
        self.tabs.select(self.tab_frames[view or visible[0]])
        self.output_label.configure(text=(f"Результаты: {self.output}" if self.output else 'Результаты: папка ещё не выбрана')
                                    if section in ('calculation', 'results') else
                                    'Неподвижная геометрия · координаты графика — от точки момента')
        self.refresh_context()
        if section == "mesh" and self.mesh_preview is None and build_mesh:
            self.preview_mesh(select=False)

    def show_view(self, key):
        if key in ('geometry', 'mesh'):
            self.show_section(key)
        else:
            section = 'calculation' if self.current_section == 'calculation' else 'results'
            self.show_section(section, key)

    def refresh_view(self):
        if self.current_section == 'mesh':
            self.preview_mesh()
        elif self.current_section == 'geometry':
            self.preview()
        else:
            self.refresh_statistics(quiet=True)

    def empty_plot(self, key, text):
        figure = self.figures[key]
        figure.clear()
        ax = figure.add_subplot()
        ax.axis('off')
        ax.text(.5, .5, text, ha='center', va='center', transform=ax.transAxes, color='#51647a', fontsize=11)
        self.canvases[key].draw_idle()

    def preview_mesh(self, select=True):
        try:
            from .mesh import preview
            project = self.project_from_form()
            refinement = self.refinement_from_form()
            self.mesh_preview = preview(project, refinement)
            plan = self.mesh_preview.plan
            n = project.grid.cells_per_chord
            self.resource_note.set(f"Ячеек на хорду: {n}" + (f" → {2*n}" if refinement else '') +
                f"\nПамять ≈ {plan.estimated_memory_gib:.2f} ГиБ / {project.grid.memory_limit_gib:g} ГиБ\n" + self.mesh_preview.note)
            self.clear_form_error()
            self.status.set(f"Сетка проверена. Память ≈ {plan.estimated_memory_gib:.2f} ГиБ при пределе {project.grid.memory_limit_gib:g} ГиБ. " + self.mesh_preview.note)
            self.redraw_mesh()
            if select:
                self.show_section('mesh')
        except Exception as error:
            self.mesh_preview = None
            self.empty_plot('mesh', 'Предпросмотр не построен.\nПроверьте параметры сетки и режима.')
            self.error(error)

    def redraw_mesh(self, zoom=None):
        if zoom is not None:
            self.mesh_zoom = zoom
        if self.mesh_preview is not None:
            from .mesh import draw
            draw(self.figures['mesh'], self.mesh_preview, body_zoom=self.mesh_zoom)
            self.toolbars['mesh'].update()
            self.canvases['mesh'].draw_idle()

    def draft_signature(self):
        # Presentation-only baseline. Never replace checkpoint's numerical identity.
        return (tuple((key, var.get()) for key, var in self.variables.items()),
                self.source.get(), self.closed_te.get(), self.sgs.get(), self.grid_mode.get(),
                self.project_title.get(), id(self.imported), repr(self.termination_settings), repr(self.refinement_template))

    def mark_clean(self, path=None):
        self.document_path = Path(path) if path else None
        self.document_readonly = bool(self.document_path and self.document_path.name == 'project.json' and
                                      (self.document_path.parent / 'history.csv').is_file())
        self._baseline = self.draft_signature()
        self.dirty = False
        self.refresh_context()

    def draft_changed(self, *args):
        if not self._tracking:
            return
        self.mesh_preview = None
        if self._error_key:
            self.clear_form_error()
        if self._refresh_pending is None:
            self._refresh_pending = self.root.after(100, self.refresh_context)

    def refresh_context(self):
        if self._refresh_pending is not None:
            self.root.after_cancel(self._refresh_pending)
            self._refresh_pending = None
        self.dirty = self.draft_signature() != self._baseline
        title = self.project_title.get().strip() or 'Новый проект'
        name = self.document_path.name if self.document_path else title
        self.document_label.set(('● ' if self.dirty else '') + name[:70])
        self.root.title(f"{'* ' if self.dirty else ''}{title} — AeroKinetic {__version__}")
        if self.busy and self.operation == 'calculation' and self.result_project:
            p = self.result_project
            note = f"Активный расчёт · Re {p.flow.reynolds:g} · α {p.flow.alpha_deg:g}° · CPU {p.grid.openmp_threads} пот."
        elif self.current_section == 'results' and self.result_project and self.output:
            p = self.result_project
            note = f"Результат · Re {p.flow.reynolds:g} · α {p.flow.alpha_deg:g}° · CPU {p.grid.openmp_threads} пот."
        else:
            note = f"Проект · Re {self.variables['reynolds'].get()} · α {self.variables['alpha_deg'].get()}° · c {self.variables['physical_chord_m'].get()} м"
        self.point_note.set(note)

    def confirm_replace(self):
        if self.busy:
            return False
        self.refresh_context()
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel('Несохранённый проект',
            'Сохранить изменения проекта перед продолжением?', parent=self.root)
        return self.save_project() if choice is True else choice is False

    def clear_form_error(self):
        if self._error_key in self.entry_widgets:
            self.entry_widgets[self._error_key].state(['!invalid'])
        self._error_key = None
        self.form_error.set('')

    def reveal_entry(self, key):
        widget = self.entry_widgets[key]
        for page in self.form_pages.values():
            if str(widget).startswith(str(page.body) + '.'):
                page.reveal(widget)
                break

    def set_phase(self, text, waiting=False):
        self.phase.set(text)
        self.progress.stop()
        self.progress.configure(mode='indeterminate' if waiting else 'determinate')
        if waiting:
            self.progress.start(18)

    def entry(self, frame, row, label, key, value):
        from .gui_widgets import parameter_entry
        var = tk.StringVar(value=value)
        self.variables[key] = var
        widget, entry = parameter_entry(frame, row, label, var)
        if key == "cells_per_chord":
            self.resolution_label = widget
        self.entry_widgets[key] = entry
        self.field_labels[key] = label
        entry.bind('<FocusIn>', lambda _: self.reveal_entry(key))
        frame.columnconfigure(1, weight=1)

    def number(self, name):
        try:
            value = float(self.variables[name].get().strip().replace(",", "."))
            if not math.isfinite(value):
                raise ValueError()
            return value
        except ValueError:
            raise FormInputError(name, f"{self.field_labels[name]}: введите конечное число; допустимы запятая, точка и запись 1e4.") from None

    def integer(self, name):
        value = self.number(name)
        if value != int(value):
            raise FormInputError(name, f"{self.field_labels[name]}: требуется целое число.")
        return int(value)

    def project_from_form(self):
        if self.source.get() == "naca":
            try:
                elements = (naca4(self.variables["naca"].get(), n_side=81, closed_te=self.closed_te.get()),)
            except ValueError as error:
                raise FormInputError('naca', str(error)) from error
        else:
            elements = self.imported
        if not elements:
            raise ValueError("Загрузите контуры DAT или выберите генератор NACA")
        template=self.project_template
        chord=template.section.chord if template and elements is template.section.elements else None
        base = Section(elements,chord=chord)
        chord_m = self.number("physical_chord_m")
        if chord_m <= 0:
            raise FormInputError('physical_chord_m', "Полная хорда в метрах должна быть положительной")
        if not 1e4 <= self.number("reynolds") <= 1e5:
            raise FormInputError('reynolds', "Для текущего решателя доступен Re от 10000 до 100000. Расширение диапазона ещё разрабатывается.")
        if not -10 <= self.number("alpha_deg") <= 30:
            raise FormInputError('alpha_deg', "Задайте угол в согласованном диапазоне от −10 до +30°")
        points = np.concatenate([e.xy for e in elements])
        leading = points[np.argmin(points[:, 0])]
        reference = leading + np.array([self.number("moment_x_m"), self.number("moment_y_m")]) * base.chord / chord_m
        section = Section(elements, base.chord, tuple(reference))
        flow = replace(template.flow if template else Flow(),reynolds=self.number("reynolds"), alpha_deg=self.number("alpha_deg"),
                    physical_chord_m=chord_m, lattice_speed=self.number("lattice_speed"),
                    sgs="smagorinsky" if self.sgs.get() == "Smagorinsky" else "none")
        ints = ("cells_per_chord", "openmp_threads")
        grid_keys = ("cells_per_chord", "upstream_chords", "downstream_chords", "transverse_chords",
                     "span_chords", "memory_limit_gib", "openmp_threads")
        grid = replace(template.grid if template else Grid(),**{k: self.integer(k) if k in ints else self.number(k) for k in grid_keys})
        run = RunSettings(**{k: self.number(k) if k in ("duration_convective", "average_after") else self.integer(k)
                             for k in asdict(RunSettings()) if k!='termination'},termination=self.termination_settings)
        return Project(section, flow, grid, run, self.project_title.get().strip() or "+".join(e.name for e in elements))

    def refinement_from_form(self):
        if self.grid_mode.get() != "refined":
            return None
        from .refined_solver import RefinementSettings
        values=asdict(self.refinement_template or RefinementSettings())
        values.update({key:self.number("refine_"+key) for key in
                       ("upstream_chords","downstream_chords","transverse_chords")})
        return RefinementSettings(**values)

    def grid_mode_changed(self):
        self.resolution_label.configure(text="Ячеек на хорду: крупная сетка" if self.grid_mode.get()=="refined" else "Ячеек на полную хорду")
        self.preview_mesh()

    @staticmethod
    def read_project_file(path):
        data=json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if data.get("format")=="aerokinetic_refined_project":
            from .refined_solver import load_refined_project
            return load_refined_project(path)
        return Project.load(path),None

    def selected_plan(self,project,refinement=None):
        if refinement is None:
            return project.plan()
        from .refined_solver import RefinedPlan
        return RefinedPlan.create(project,refinement)

    def populate(self, project, refinement=None):
        self.project_template=project
        self.termination_settings=project.run.termination
        self.refinement_template=refinement
        self.project_title.set(project.title)
        self.grid_mode.set("refined" if refinement is not None else "uniform")
        self.resolution_label.configure(text="Ячеек на хорду: крупная сетка" if refinement is not None else "Ячеек на полную хорду")
        if refinement is not None:
            for key in ("upstream_chords","downstream_chords","transverse_chords"):
                self.variables["refine_"+key].set(str(getattr(refinement,key)))
        self.imported = project.section.elements
        self.source.set("import")
        self.geometry_text.set("; ".join(e.name for e in self.imported))
        for values in (asdict(project.flow), asdict(project.grid), asdict(project.run)):
            for key, value in values.items():
                if key in self.variables:
                    self.variables[key].set("" if value is None else str(value))
        self.sgs.set("Smagorinsky" if project.flow.sgs == "smagorinsky" else "Без явной SGS")
        points = np.concatenate([e.xy for e in project.section.elements])
        leading = points[np.argmin(points[:, 0])]
        if project.flow.physical_chord_m is not None:
            position = (np.array(project.section.moment_reference) - leading) / project.section.chord * project.flow.physical_chord_m
            for key, value in zip(("moment_x_m", "moment_y_m"), position):
                self.variables[key].set(str(value))
        if self.residual_window and self.residual_window.window.winfo_exists():self.residual_window.sync_from_project()
        self.clear_form_error()
        self.draft_changed()

    def new(self):
        if not self.confirm_replace():return
        self.populate(Project.from_dict({"geometry": {"naca0012": {"n_side": 81}},
                                        "flow": {"physical_chord_m": 0.15, "alpha_deg": 5},
                                        "grid": {"cells_per_chord": 32, "upstream_chords": 1.5,
                                                 "downstream_chords": 3, "transverse_chords": 1.5}}))
        self.source.set("naca")
        self.variables["naca"].set("0012")
        self.closed_te.set(False)
        self.document_path = None
        self.output = self.result_project = self.result_refinement = None
        self.records = [];self.residual_records = [];self.frame_files = []
        self.temporal_report = None
        self.statistics_table.delete(*self.statistics_table.get_children())
        self.statistics_note.set('Откройте завершённый или остановленный расчёт.')
        self.analysis_cutoff.set('')
        self.output_label.configure(text='Результаты: папка ещё не выбрана')
        self.slider.configure(to=0);self.frame_index.set(0)
        self.frame_note.set('Кадров ещё нет')
        self.draw_history()
        self.empty_plot('field', 'Поле появится после записи первого кадра.')
        self.empty_plot('statistics', 'Средние появятся после расчёта или загрузки результата.')
        if self.residual_window and self.residual_window.window.winfo_exists():self.residual_window.refresh()
        self.preview()
        self.set_phase('Готов к работе')
        self.progress['value'] = 0
        self.mark_clean()

    def open_mesh(self):
        try:
            if self.mesh_window and self.mesh_window.window.winfo_exists():
                self.mesh_window.window.lift();return
            from .gui_mesh import MeshWindow
            self.mesh_window=MeshWindow(self)
        except Exception as error:self.error(error)

    def open_convergence(self):
        if self.residual_window and self.residual_window.window.winfo_exists():
            self.residual_window.window.lift();return
        from .gui_convergence import ConvergenceWindow
        self.residual_window=ConvergenceWindow(self)

    def import_dat(self):
        if self.busy:return
        paths = filedialog.askopenfilenames(title="Контуры в общей системе координат", filetypes=[("Контуры", "*.dat *.txt"), ("Все файлы", "*")])
        if paths:
            try:
                elements = tuple(Contour.from_dat(path) for path in paths)
                Section(elements)
                self.imported = elements
                self.source.set("import")
                self.geometry_text.set("; ".join(e.name for e in elements))
                self.preview()
            except Exception as error:
                self.error(error)

    def open_project(self):
        if not self.confirm_replace():return
        path = filedialog.askopenfilename(filetypes=[("Проект JSON", "*.json")])
        if path:
            try:
                project, refinement = self.read_project_file(path)
                if project.grid.target != "cpu":
                    raise ValueError("Оконное приложение поддерживает CPU; GPU требует отдельной проверки")
                self.populate(project,refinement)
                self.preview()
                self.mark_clean(path)
            except Exception as error:
                self.error(error)

    def save_project(self, save_as=False):
        if self.busy:return False
        try:
            project = self.project_from_form()
            refinement = self.refinement_from_form()
            self.selected_plan(project,refinement)
            path = self.document_path if self.document_path and not self.document_readonly and not save_as else filedialog.asksaveasfilename(
                defaultextension=".json", filetypes=[("Проект JSON", "*.json")],
                initialfile=self.document_path.name if self.document_path else "project.json")
            if path:
                target = Path(path)
                if target.name == 'project.json' and (target.parent/'history.csv').is_file():
                    raise ValueError('Сохраните проект в отдельный файл: project.json внутри результатов нужен для их воспроизводимости и продолжения.')
                if refinement is None:
                    project.save(path)
                else:
                    from .refined_solver import refined_project_dict
                    write_json(path,refined_project_dict(project,refinement))
                self.status.set(f"Проект сохранён: {path}")
                self.mark_clean(path)
                return True
        except Exception as error:
            self.error(error)
        return False

    def preview(self):
        try:
            project = self.project_from_form()
            refinement = self.refinement_from_form()
            plan = self.selected_plan(project,refinement)
            fig = self.figures["geometry"]
            fig.clear()
            ax = fig.add_subplot()
            for e in project.section.normalized(project.flow.alpha_deg):
                xy = np.vstack((e.xy, e.xy[0]))
                ax.plot(*xy.T, label=e.name)
            ax.plot(0, 0, "o", label="Точка момента")
            if refinement is not None:
                from matplotlib.patches import Rectangle
                lower=np.array(plan.origin)+np.array(plan.box.lower[:2])*plan.coarse_level.dx
                size=np.array(plan.box.shape[:2])*plan.coarse_level.dx
                ax.add_patch(Rectangle(lower,*size,fill=False,linestyle="--",edgecolor="#267a6c",label="Мелкая область"))
            ax.set(xlabel="x/c от точки момента", ylabel="y/c", aspect="equal", title=f"α={project.flow.alpha_deg:g}°, полная хорда {project.flow.physical_chord_m:g} м")
            ax.legend()
            ax.grid(alpha=0.25)
            self.canvases["geometry"].draw_idle()
            note = f"Сетка {plan.shape}: {math.prod(plan.shape):,} ячеек; память ≈{plan.estimated_memory_gib:.2f} GiB "
            if refinement is not None:
                note = f"Крупная сетка {plan.shape}; у профиля {2*project.grid.cells_per_chord} ячеек/хорду; память ≈{plan.estimated_memory_gib:.2f} GiB "
            note += f"при пределе {project.grid.memory_limit_gib:g}. U∞={project.flow.physical_speed:.4g} м/с, M={project.flow.physical_mach:.4g}."
            if plan.estimated_memory_gib > project.grid.memory_limit_gib:
                note += " Расчёт с этой сеткой превышает заданный предел памяти."
            self.status.set(note)
            self.append_log(note)
            self.clear_form_error()
            self.toolbars['geometry'].update()
            self.show_view('geometry')
            self.refresh_context()
        except Exception as error:
            self.error(error)

    def set_busy(self, busy):
        if self.busy == busy:
            return
        self.busy = busy
        if busy:
            self.stopping.clear()
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)
        for frame in self.form_frames:
            for widget in descendants(frame):
                try:
                    if busy:
                        widget._previous_state = widget.cget("state")
                        widget.configure(state="disabled")
                    elif hasattr(widget, "_previous_state"):
                        widget.configure(state=widget._previous_state)
                except tk.TclError:
                    pass
        for button in self.busy_widgets:
            button.configure(state="disabled" if busy else "normal")
        for index in range(6):
            self.file_menu.entryconfigure(index, state='disabled' if busy else 'normal')
        self.stop_button.configure(state="normal" if busy and self.operation == 'calculation' else "disabled")
        self.gif_button.configure(state="disabled" if busy else "normal")
        for window in (self.mesh_window,self.residual_window):
            if window and window.window.winfo_exists():window.set_busy(busy)
        self.refresh_context()

    def start(self):
        if self.busy:return
        try:
            project = self.project_from_form()
            refinement = self.refinement_from_form()
            plan = self.selected_plan(project,refinement)
            if plan.estimated_memory_gib > project.grid.memory_limit_gib:
                raise ValueError(f"Оценка памяти {plan.estimated_memory_gib:.2f} GiB превышает заданный предел")
            parent = filedialog.askdirectory(title="Где создать отдельную папку результатов?")
            if not parent:
                return
            output = Path(parent) / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
            self.begin(project, output, resume=False,refinement=refinement)
        except Exception as error:
            self.error(error)

    def resume(self):
        if not self.confirm_replace():return
        path = filedialog.askdirectory(title="Папка остановленного расчёта с checkpoint.npz")
        if path:
            try:
                output = Path(path)
                project,refinement = self.read_project_file(output / "project.json")
                if not (output / "checkpoint.npz").is_file():
                    raise ValueError("В папке нет сохранённого состояния checkpoint.npz")
                self.populate(project,refinement)
                self.mark_clean(output / 'project.json')
                self.begin(project, output, resume=True,refinement=refinement)
            except Exception as error:
                self.error(error)

    def begin(self, project, output, resume, refinement=None):
        if self.busy:
            raise RuntimeError("Дождитесь завершения текущей операции")
        self.output, self.result_project = output, project
        self.operation = 'calculation'
        self.set_busy(True)
        self.set_phase('Восстановление' if resume else 'Подготовка / компиляция', waiting=True)
        self.result_refinement = refinement
        self.history_average_after = project.run.average_after
        self.temporal_report = None
        self.statistics_table.delete(*self.statistics_table.get_children())
        self.figures["statistics"].clear()
        self.canvases["statistics"].draw_idle()
        self.statistics_note.set("Расчёт выполняется. Анализ сохранённой истории будет доступен после завершения или остановки.")
        self.output_label.configure(text=f"Результаты: {output}")
        self.records = []
        self.residual_records=[]
        self.frame_files=[]
        self.frame_index.set(0);self.slider.configure(to=0)
        self.frame_note.set('Кадры появятся после первой записи')
        self.empty_plot('field', 'Кадры появятся после первой записи.')
        self.draw_history()
        if self.residual_window and self.residual_window.window.winfo_exists():self.residual_window.refresh()
        self.target_steps = math.ceil(project.run.duration_convective / project.plan().dt_convective)
        if project.run.termination.max_steps:self.target_steps=min(self.target_steps,project.run.termination.max_steps)
        self.status.set("Восстановление состояния…" if resume else "Подготовка и компиляция расчёта…")
        self.progress["value"] = 0
        if resume:
            try:
                self.read_history();self.read_residuals()
            except (ValueError,TypeError):
                self.append_log('Предварительное чтение истории отложено до восстановления контрольного состояния.')
        self.show_section('calculation', 'history')
        def job():
            try:
                from .solver import Solver
                def sample(solver, record):
                    self.messages.put(("sample", {k: record[k] for k in ("step", "t_star", "cl", "cd", "cm")}))
                    return not self.stopping.is_set()
                def progress(message):
                    self.messages.put(("log", message))
                def residual(solver,record):
                    self.messages.put(('residual',record))
                if refinement is not None:
                    from .refined_solver import RefinedSolver
                    if resume:
                        result=RefinedSolver.resume_run(output,callback=sample,stop_event=self.stopping,progress=progress,residual_callback=residual)
                    else:
                        solver=RefinedSolver(project,refinement,progress=progress)
                        result=solver.run(output,callback=sample,stop_event=self.stopping,residual_callback=residual)
                elif resume:
                    result = Solver.resume_run(output, callback=sample, stop_event=self.stopping, progress=progress,residual_callback=residual)
                else:
                    solver = Solver(project.section, project.flow, project.grid, progress=progress)
                    result = solver.run(output=output, callback=sample, stop_event=self.stopping,residual_callback=residual, **asdict(project.run))
                self.messages.put(("done", result))
            except Exception:
                self.messages.put(("failed", traceback.format_exc()))
        self.worker = threading.Thread(target=job, name="aerokinetic-calculation", daemon=False)
        self.worker.start()

    def stop(self):
        if not self.busy or self.operation != 'calculation':return
        self.stopping.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Остановка после текущего шага и сохранение состояния…")
        self.set_phase('Остановка / сохранение', waiting=True)

    def poll(self):
        redraw = False
        residual_redraw=False
        for _ in range(250):
            try:
                event, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if event == "sample":
                if self.records and self.records[-1]["step"] >= value["step"]:
                    self.records = [r for r in self.records if r["step"] < value["step"]]
                self.records.append(value)
                if not self.stopping.is_set() and self.phase.get() != 'Расчёт':self.set_phase('Расчёт')
                self.progress["value"] = min(100, 100 * value["step"] / self.target_steps)
                self.status.set(f"tU/c={value['t_star']:.4f}; Cl={value['cl']:.6g}, Cd={value['cd']:.6g}, Cm={value['cm']:.6g}")
                redraw = True
            elif event=='residual':
                if self.residual_records and self.residual_records[-1]['step']>=value['step']:
                    self.residual_records=[r for r in self.residual_records if r['step']<value['step']]
                self.residual_records.append(value);residual_redraw=True
            elif event == "log":
                self.append_log(value)
            elif event == "animation_done":
                self.set_busy(False)
                self.set_phase('GIF сохранён')
                self.status.set(f"Анимация сохранена: {value}")
            elif event == "analysis_done":
                self.set_busy(False)
                self.set_phase('Отчёт сохранён')
                folder, report = value
                self.show_statistics(report)
                self.status.set(f"Отчёт и FOR_REVIEW.zip сохранены: {folder}")
                self.append_log(f"Анализ сохранён: {folder}; исходные данные сохранены.")
                if not self.closing:
                    messagebox.showinfo("Отчёт готов", f"Отчёт: {folder / 'REPORT_RU.txt'}\n\n"
                                        f"Для проверки пришлите файл:\n{folder / 'FOR_REVIEW.zip'}")
            elif event == "operation_failed":
                self.set_busy(False)
                self.set_phase('Ошибка экспорта')
                self.append_log(value)
                self.status.set("Не удалось подготовить файл. Подробности в журнале.")
                if not self.closing:
                    messagebox.showerror("Ошибка экспорта", value.splitlines()[-1])
            elif event in ("done", "failed"):
                self.set_busy(False)
                self.read_history()
                self.read_residuals();residual_redraw=True
                self.update_frames()
                redraw = True
                if event == "done":
                    from .gui_convergence import REASONS
                    state=REASONS.get(value.get('termination_reason'),'Заданное время рассчитано' if value['status']=='completed' else 'Остановлен, состояние сохранено')
                    self.status.set(f"{state}: {value['steps']} шагов, {value['samples']} выборок. Аэродинамическая точность не подтверждена. {self.output}")
                    self.set_phase('Завершён' if value['status']=='completed' else 'Остановлен · сохранён')
                    if value['status']=='completed':self.progress['value']=100
                    self.append_log(json.dumps(value, ensure_ascii=False, indent=2))
                    self.refresh_statistics(reset_cutoff=True, quiet=True)
                    self.show_section('results', 'history')
                else:
                    self.append_log(value)
                    self.set_phase('Ошибка расчёта')
                    self.status.set("Расчёт остановлен из-за ошибки. Подробности в журнале; успешная валидация не заявляется.")
                    if not self.closing:
                        messagebox.showerror("Ошибка расчёта", value.splitlines()[-1])
        if redraw:
            self.draw_history()
        if residual_redraw and self.residual_window and self.residual_window.window.winfo_exists():self.residual_window.refresh()
        if self.worker is not None and self.worker.is_alive():
            self.update_frames()
        if self.closing and (self.worker is None or not self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(300, self.poll)

    def read_history(self):
        if self.output and (self.output / "history.csv").exists():
            with (self.output / "history.csv").open(encoding="utf-8", newline="") as f:
                self.records = [{k: float(row[k]) for k in ("step", "t_star", "cl", "cd", "cm")} for row in csv.DictReader(f)]
            summary = self.output / "summary.json"
            if summary.is_file():
                self.history_average_after = float(json.loads(summary.read_text(encoding="utf-8"))["average_after_t_star"])

    def read_residuals(self):
        self.residual_records=[]
        if self.output and (self.output/'residuals.csv').is_file():
            with (self.output/'residuals.csv').open(encoding='utf-8',newline='') as handle:
                self.residual_records=[{k:(int(v) if k in ('step','streak') else float(v)) for k,v in row.items() if v not in ('',None)} for row in csv.DictReader(handle)]

    def draw_history(self):
        if not self.records:
            self.sample_note.set('Выборок коэффициентов ещё нет')
            self.empty_plot('history', 'История Cl, Cd и Cm появится после первой выборки.\nНачните расчёт или откройте папку результатов.')
            return
        last = self.records[-1]
        self.sample_note.set(f"Последняя выборка · tU/c {last['t_star']:.5g} · Cl {last['cl']:.6g} · Cd {last['cd']:.6g} · Cm {last['cm']:.6g}")
        fig = self.figures["history"]
        fig.clear()
        for ax, key in zip(fig.subplots(3, 1, sharex=True), ("cl", "cd", "cm")):
            if self.records:
                # Only decimate the display; all samples remain in history.csv.
                stride = max(1, len(self.records) // 2000)
                rows = self.records[::stride]
                ax.plot([r["t_star"] for r in rows], [r[key] for r in rows], linewidth=1, label="Выборки коэффициентов")
                cutoff = self.history_average_after
                chosen = [r for r in self.records if r["t_star"] >= cutoff]
                if chosen:
                    mean = np.cumsum([r[key] for r in chosen]) / np.arange(1, len(chosen) + 1)
                    ax.plot([r["t_star"] for r in chosen][::stride], mean[::stride], linewidth=1.4,
                            color="#ca7220", label=f"Среднее после tU/c={cutoff:g}")
                    ax.axvline(cutoff, color="0.5", linestyle=":", linewidth=0.8)
            ax.set_ylabel(key.capitalize())
            ax.grid(alpha=0.25)
        ax.set_xlabel("tU/c")
        if self.records:
            fig.axes[0].legend(fontsize=8, loc="upper right")
        self.canvases["history"].draw_idle()

    def refresh_statistics(self, reset_cutoff=False, quiet=False):
        if self.busy:
            return
        if not self.output:
            if not quiet:
                messagebox.showinfo("Средние", "Сначала выполните или откройте расчёт.")
            return
        try:
            from .temporal import read_run
            cutoff = None
            if not reset_cutoff and self.analysis_cutoff.get().strip():
                cutoff = float(self.analysis_cutoff.get().strip().replace(",", "."))
            report, _, _ = read_run(self.output, cutoff)
            # Keep a lossless numeric roundtrip when export refreshes the form.
            self.analysis_cutoff.set(str(report['requested_average_after_t_star']))
            self.show_statistics(report)
        except Exception as error:
            self.temporal_report = None
            self.statistics_table.delete(*self.statistics_table.get_children())
            self.figures["statistics"].clear()
            self.canvases["statistics"].draw_idle()
            self.statistics_note.set(f"Статистика недоступна: {error}")
            if not quiet:
                self.error(error)

    def show_statistics(self, report):
        self.temporal_report = report
        self.statistics_table.delete(*self.statistics_table.get_children())
        series = report["series"]
        for key in ("cl", "cd", "cm"):
            values = series[key]
            self.statistics_table.insert("", "end", values=(key.capitalize(), *(
                f"{values[k]:.6g}" for k in ("mean", "rms_fluctuation", "first_half_mean", "last_half_mean", "last_minus_first_half_mean"))))
        window = series["cl"]
        self.statistics_note.set(f"Сохранённый расчёт: tU/c={report['calculated_final_time_t_star']}; "
                                 f"окно {window['selected_t_first']:.5g}…{window['selected_t_last']:.5g}; "
                                 f"{window['samples']} выборок. Средние по блокам показаны ниже; блоки могут быть зависимы.")
        fig = self.figures["statistics"]
        fig.clear()
        for ax, key in zip(fig.subplots(3, 1, sharex=True), ("cl", "cd", "cm")):
            blocks = series[key]["blocks"]
            ax.plot([(b["t_first"] + b["t_last"]) / 2 for b in blocks], [b["mean"] for b in blocks], "o-", markersize=3)
            for block in blocks:
                ax.plot([block["t_first"], block["t_last"]], [block["mean"]] * 2, color="#248646", linewidth=2)
            ax.set_ylabel(key.capitalize())
            ax.grid(alpha=0.25)
        ax.set_xlabel("tU/c — средние по временным блокам")
        self.canvases["statistics"].draw_idle()

    def export_analysis(self):
        if self.busy:
            return
        self.refresh_statistics()
        if self.temporal_report is None:
            return
        from .temporal import write_analysis
        output = self.output
        cutoff = self.temporal_report["requested_average_after_t_star"]
        self.operation='export'
        self.set_busy(True)
        self.set_phase('Сохранение отчёта', waiting=True)
        self.stop_button.configure(state="disabled")
        self.status.set("Создание отчёта и компактного архива для проверки…")
        def job():
            try:
                self.messages.put(("analysis_done", write_analysis(output, average_after=cutoff)))
            except Exception:
                self.messages.put(("operation_failed", traceback.format_exc()))
        self.worker = threading.Thread(target=job, name="aerokinetic-analysis", daemon=False)
        self.worker.start()

    def update_frames(self):
        if not self.output:
            return
        paths = sorted((self.output / "frames").glob("frame_*.npz"))
        if paths != self.frame_files:
            self.frame_files = paths
            self.slider.configure(to=max(0, len(paths) - 1))
            self.frame_index.set(max(0, len(paths) - 1))
            self.draw_frame()

    def draw_frame(self):
        if not self.frame_files:
            return
        index = max(0, min(len(self.frame_files) - 1, int(self.frame_index.get())))
        try:
            choice = self.field_choices[self.field_choice.get()]
            key = {"speed_m_s": "speed", "pressure_pa": "cp"}.get(choice, choice)
            with np.load(self.frame_files[index], allow_pickle=False) as data:
                timestamp = float(data['t_star'])
                self.frame_note.set(f"Кадр {index+1} / {len(self.frame_files)} · tU/c {timestamp:.5g} · автоматическая шкала каждого кадра · без сглаживания")
                if "format" in data and str(data["format"])=="aerokinetic_refined_slice":
                    from .refined_visualization import draw_frame
                    draw_frame(self.figures["field"],self.frame_files[index],choice)
                    self.canvases["field"].draw_idle()
                    return
                values = data[key].copy()
                origin, dx, timestamp = data["origin"], float(data["dx"]), float(data["t_star"])
            if choice in ("speed_m_s", "pressure_pa"):
                flow = self.result_project.flow
                if flow.physical_speed is None:
                    raise ValueError("В этом результате нет физической хорды для перевода единиц")
                values *= flow.physical_speed if choice == "speed_m_s" else 0.5 * flow.atmosphere.rho * flow.physical_speed**2
            fig = self.figures["field"]
            fig.clear()
            ax = fig.add_subplot()
            finite = values[np.isfinite(values)]
            limit = max(float(np.max(np.abs(finite))) if finite.size else 1, 1e-10)
            speed = key == "speed"
            image = ax.imshow(values.T, origin="lower", extent=[origin[0], origin[0] + values.shape[0] * dx,
                              origin[1], origin[1] + values.shape[1] * dx], cmap="viridis" if speed else "RdBu_r",
                              vmin=0 if speed else -limit, vmax=limit, interpolation="nearest")
            fig.colorbar(image, ax=ax, label=self.field_choice.get(), shrink=0.8)
            ax.set(xlabel="x/c от точки момента", ylabel="y/c", aspect="equal", facecolor="0.35",
                   title=f"Среднее сечение размаха, tU/c={timestamp:.4f}")
            self.canvases["field"].draw_idle()
        except Exception as error:
            self.append_log(f"Отображение кадра: {error}")

    def draw_experiment(self):
        from .experiments import load_series
        entry = next(s for s in self.references if s["label"] == self.reference_choice.get())
        meta, rows = load_series(entry["id"])
        self.reference_note.configure(text=meta["source"] + "\n" + meta["conditions"] + "\n" + meta["uncertainty_note"] + "\n" + meta["url"])
        fig = self.figures["experiment"]
        fig.clear()
        for ax, key in zip(fig.subplots(3, 1, sharex=True), ("cl", "cd", "cm_c4")):
            if key in meta["coefficients"]:
                ax.plot([r["alpha_deg"] for r in rows], [r[key] for r in rows], "o-", markersize=3, linewidth=0.8)
            else:
                ax.text(0.5, 0.5, "Данные пока не извлечены", transform=ax.transAxes, ha="center")
            ax.set_ylabel({"cl": "Cl", "cd": "Cd", "cm_c4": "Cm(c/4)"}[key])
            ax.grid(alpha=0.25)
        ax.set_xlabel("α, °")
        self.canvases["experiment"].draw_idle()

    def compare(self):
        if not self.output:
            messagebox.showinfo("Сравнение", "Сначала выполните или откройте расчёт.")
            return
        try:
            from .experiments import compare_run
            entry = next(s for s in self.references if s["label"] == self.reference_choice.get())
            result = compare_run(self.output, entry["id"])
            path = self.output / f"experiment_{entry['id']}.json"
            write_json(path, result)
            lines = [f"{r['coefficient']}: расчёт {r['calculated']:.6g}; опыт* {r['experimental_interpolated']:.6g}; "
                     f"разность {r['difference']:+.6g}" for r in result["differences"]]
            messagebox.showinfo("Предварительное сравнение с продувкой", "\n".join(lines) +
                "\n\n* Линейная интерполяция в пределах экспериментальной серии.\n"
                "Это сопоставление не подтверждает численную сходимость или совпадение всех условий трубы.\n"
                f"Сохранено: {path}")
        except Exception as error:
            self.error(error)

    def open_output(self):
        if not self.confirm_replace():return
        path = filedialog.askdirectory(title="Папка результатов")
        if path:
            try:
                output = Path(path)
                project,refinement = self.read_project_file(output / "project.json")
                self.populate(project,refinement)
                self.preview()
                self.mark_clean(output / 'project.json')
                self.output, self.result_project = output, project
                self.result_refinement = refinement
                self.output_label.configure(text=f"Результаты: {output}")
                self.frame_files = []
                self.frame_index.set(0);self.slider.configure(to=0)
                self.frame_note.set('В этом результате нет сохранённых кадров')
                self.empty_plot('field', 'В этом результате нет сохранённых кадров.')
                self.read_history()
                self.read_residuals()
                if self.residual_window and self.residual_window.window.winfo_exists():self.residual_window.refresh()
                self.draw_history()
                self.refresh_statistics(reset_cutoff=True, quiet=True)
                self.update_frames()
                self.show_section('results', 'history')
                self.set_phase('Результат открыт')
                self.status.set(f"Результаты открыты: {output}")
            except Exception as error:
                self.error(error)

    def export_animation(self):
        if self.busy:return
        if not self.output or not self.frame_files:
            messagebox.showinfo("Анимация", "Сначала выполните или откройте расчёт с кадрами.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".gif", initialfile="flow.gif", filetypes=[("Анимация GIF", "*.gif")])
        if not path:
            return
        from .animation import animate
        self.operation='export'
        self.set_busy(True)
        self.set_phase('Сохранение GIF', waiting=True)
        self.stop_button.configure(state="disabled")
        self.status.set("Создание GIF без интернета…")
        output, field = self.output, self.field_choices[self.field_choice.get()]
        def job():
            try:
                animate(output, path, field)
                self.messages.put(("log", f"GIF сохранён: {path}; поле {field}."))
                self.messages.put(("animation_done", path))
            except Exception:
                self.messages.put(("operation_failed", traceback.format_exc()))
        self.worker = threading.Thread(target=job, name="aerokinetic-animation", daemon=False)
        self.worker.start()

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def error(self, error):
        self.append_log(str(error))
        self.form_error.set(str(error))
        self.status.set(str(error))
        if isinstance(error, FormInputError):
            self._error_key = error.key
            widget = self.entry_widgets.get(error.key)
            if widget:
                for key, page in self.form_pages.items():
                    if str(widget).startswith(str(page.body) + '.'):
                        # Avoid mesh preview recursion when reporting an input error.
                        self.show_section(key, build_mesh=False)
                        break
                widget.state(['invalid'])
                widget.focus_set()
                self.reveal_entry(error.key)
            return
        messagebox.showerror("Проверьте параметры", str(error))

    def close(self):
        if self.worker is not None and self.worker.is_alive():
            self.closing = True
            self.stopping.set()
            self.status.set("Завершаю операцию и сохраняю данные перед закрытием…")
        else:
            if not self.confirm_replace():return
            self.root.destroy()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="AeroKinetic desktop application")
    parser.add_argument("project", nargs="?", type=Path)
    args = parser.parse_args(argv)
    from .gui_style import enable_windows_dpi
    enable_windows_dpi()
    root = tk.Tk()
    app = Application(root)
    if args.project:
        try:
            app.populate(*app.read_project_file(args.project))
            app.preview()
            app.mark_clean(args.project)
        except Exception as error:
            app.error(error)
    root.mainloop()


if __name__ == "__main__":
    main()
