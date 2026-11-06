"""Workspace construction. Commands and data stay in Application/Project."""
import tkinter as tk
from tkinter import ttk

from . import __version__
from .gui_style import COLORS, ScrollForm, install_theme

SECTIONS = {
    "geometry": ("Геометрия и режим", "Профиль, физические условия и точка момента"),
    "mesh": ("Расчётная сетка", "Разрешение, размеры области и оценка ресурсов"),
    "calculation": ("Управление расчётом", "Время, потоки CPU, запись и условия окончания"),
    "results": ("Результаты", "История, средние, поля течения и эксперимент"),
}


def build(app):
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    root = app.root
    install_theme(root)
    screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{min(1380, max(1040, screen_width-64))}x{min(860, max(640, screen_height-100))}")
    root.minsize(min(1040, screen_width-32), min(640, screen_height-80))
    app.form_pages, app.form_frames, app.entry_widgets, app.field_labels = {}, [], {}, {}
    app.nav_buttons, app.toolbars = {}, {}
    app.project_title = tk.StringVar(value="NACA0012")
    app.document_label = tk.StringVar(value="Новый проект")
    app.phase = tk.StringVar(value="Готов к работе")
    app.point_note = tk.StringVar()
    app.resource_note = tk.StringVar(value="Оцените сетку перед запуском")
    app.sample_note = tk.StringVar(value="Выборок коэффициентов ещё нет")
    app.form_error = tk.StringVar()
    app.workspace_title = tk.StringVar()
    app.workspace_description = tk.StringVar()
    app.frame_note = tk.StringVar(value="Кадров ещё нет")
    app.result_note = tk.StringVar(value="Результат не открыт")

    header = ttk.Frame(root, style="Surface.TFrame", padding=(18, 12))
    header.pack(fill="x")
    ttk.Label(header, text="AeroKinetic", style="Brand.TLabel").pack(side="left")
    ttk.Label(header, text=__version__, style="Subtitle.TLabel", padding=(8, 0)).pack(side="left")
    ttk.Label(header, textvariable=app.phase, style="State.TLabel").pack(side="right")
    ttk.Label(header, textvariable=app.document_label, style="Surface.TLabel", padding=(24, 0)).pack(side="left")
    toolbar = ttk.Frame(root, padding=(12, 7))
    toolbar.pack(fill="x")
    for label, command in (("Новый", app.new), ("Открыть…", app.open_project),
                           ("Сохранить", app.save_project), ("Открыть результат…", app.open_output)):
        button = ttk.Button(toolbar, text=label, command=command)
        button.pack(side="left", padx=(0, 5))
        app.busy_widgets.append(button)
    ttk.Label(toolbar, text="3D · CPU · Re 10⁴–10⁵ · M ≤ 0.15", style="Muted.TLabel").pack(side="right")

    # Pack the command strip before the expanding workspace: reachable at small sizes.
    footer = ttk.Frame(root, padding=(12, 7))
    footer.pack(side="bottom", fill="x")
    app.start_button = ttk.Button(footer, text="Начать расчёт…", style="Primary.TButton", command=app.start)
    app.start_button.pack(side="left", padx=(0, 6))
    app.resume_button = ttk.Button(footer, text="Продолжить…", command=app.resume)
    app.resume_button.pack(side="left", padx=(0, 6))
    app.busy_widgets.extend((app.start_button, app.resume_button))
    app.stop_button = ttk.Button(footer, text="Остановить и сохранить", command=app.stop, state="disabled")
    app.stop_button.pack(side="left")
    app.progress = ttk.Progressbar(footer, maximum=100)
    app.progress.pack(side="right", fill="x", expand=True, padx=(18, 0))
    status = ttk.Frame(root, padding=(14, 3))
    status.pack(side="bottom", fill="x")
    app.status_label = ttk.Label(status, textvariable=app.status, wraplength=1100, style="Muted.TLabel")
    app.status_label.pack(fill="x")
    status.bind("<Configure>", lambda event: app.status_label.configure(wraplength=max(200, event.width - 28)))

    body = ttk.Frame(root)
    body.pack(fill="both", expand=True, padx=(8, 12), pady=(0, 5))
    rail = ttk.Frame(body, style="Surface.TFrame", width=166, padding=(6, 12))
    rail.pack(side="left", fill="y", padx=(0, 10))
    for key, text in (("geometry", "01  Геометрия"), ("mesh", "02  Сетка"),
                      ("calculation", "03  Расчёт"), ("results", "04  Результаты")):
        button = ttk.Button(rail, text=text, style="Nav.TButton", command=lambda k=key: app.show_section(k))
        button.pack(fill="x", pady=3)
        app.nav_buttons[key] = button
    ttk.Separator(rail).pack(fill="x", pady=18)
    ttk.Button(rail, text="Сеткопостроитель", command=app.open_mesh).pack(fill="x", pady=3)
    ttk.Button(rail, text="Невязки и стоп", command=app.open_convergence).pack(fill="x", pady=3)
    ttk.Label(rail, text="Исследовательская\nверсия\n\nТочность по продувкам\nпока не подтверждена.",
              style="Subtitle.TLabel", wraplength=154, justify="left").pack(side="bottom", fill="x", pady=10)

    main = ttk.Frame(body)
    main.pack(side="left", fill="both", expand=True)
    ttk.Label(main, textvariable=app.workspace_title, style="Title.TLabel").pack(anchor="w", pady=(7, 2))
    ttk.Label(main, textvariable=app.workspace_description, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
    app.panes = ttk.Panedwindow(main, orient="horizontal")
    app.panes.pack(fill="both", expand=True)
    app.form_host = ttk.Frame(app.panes, width=356)
    app.content_host = ttk.Frame(app.panes)
    app.panes.add(app.form_host, weight=0)
    app.panes.add(app.content_host, weight=1)
    error = ttk.Label(app.form_host, textvariable=app.form_error, style="Error.TLabel", wraplength=324)
    error.pack(side="bottom", fill="x", padx=10, pady=4)
    for key in ("geometry", "mesh", "calculation"):
        page = ScrollForm(app.form_host)
        app.form_pages[key] = page
        app.form_frames.append(page.body)

    def group(page, title):
        card = ttk.Labelframe(app.form_pages[page].body, text=title)
        card.pack(fill="x", pady=(0, 12))
        return card

    case = group("geometry", "Проект и профиль")
    ttk.Label(case, text="Название проекта").grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Entry(case, textvariable=app.project_title).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 10))
    app.source = tk.StringVar(value="naca")
    ttk.Radiobutton(case, text="Генератор NACA, 4 цифры", variable=app.source, value="naca").grid(row=2, column=0, columnspan=2, sticky="w")
    app.entry(case, 3, "Код NACA", "naca", "0012")
    app.closed_te = tk.BooleanVar(value=False)
    ttk.Checkbutton(case, text="Замкнуть заднюю кромку", variable=app.closed_te).grid(row=4, column=0, columnspan=2, sticky="w", pady=4)
    ttk.Radiobutton(case, text="Контуры из DAT / проекта", variable=app.source, value="import").grid(row=5, column=0, columnspan=2, sticky="w")
    ttk.Button(case, text="Загрузить DAT…", command=app.import_dat).grid(row=6, column=0, columnspan=2, sticky="ew", pady=5)
    app.geometry_text = tk.StringVar(value="Каждый элемент механизации — отдельный DAT в общей системе координат.")
    ttk.Label(case, textvariable=app.geometry_text, wraplength=280, style="Muted.TLabel").grid(row=7, column=0, columnspan=2, sticky="w", pady=4)
    regime = group("geometry", "Режим и момент")
    for row, (label, key, value) in enumerate([
        ("Re по полной хорде", "reynolds", "10000"), ("Угол атаки, °", "alpha_deg", "5"),
        ("Полная хорда, м", "physical_chord_m", "0.15"), ("Точка момента x, м", "moment_x_m", "0.0375"),
        ("Точка момента y, м", "moment_y_m", "0")]):
        app.entry(regime, row, label, key, value)
    ttk.Label(regime, text="Координаты момента — от передней кромки.\nСкорость и M вычисляются из Re, хорды и свойств воздуха.",
              wraplength=280, style="Muted.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
    ttk.Button(app.form_pages["geometry"].body, text="Обновить профиль и оценку", command=app.preview).pack(fill="x", pady=3)

    app.grid_mode = tk.StringVar(value="uniform")
    mesh = group("mesh", "Область и разрешение")
    for row, (label, value) in enumerate((("Равномерная сетка", "uniform"), ("Два уровня 2:1", "refined"))):
        ttk.Radiobutton(mesh, text=label, variable=app.grid_mode, value=value, command=app.grid_mode_changed).grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
    for row, (label, key, value) in enumerate([
        ("Ячеек на полную хорду", "cells_per_chord", "32"), ("Перед профилем, c", "upstream_chords", "1.5"),
        ("За профилем, c", "downstream_chords", "3"), ("Сверху / снизу, c", "transverse_chords", "1.5"),
        ("Периодический размах, c", "span_chords", "0.5"), ("Предел памяти, ГиБ", "memory_limit_gib", "4")], 2):
        app.entry(mesh, row, label, key, value)
    refine = group("mesh", "Область мелкой сетки · 2:1")
    for row, (label, key, value) in enumerate([
        ("Перед профилем, c", "refine_upstream_chords", "0.375"),
        ("За профилем, c", "refine_downstream_chords", "0.75"),
        ("Сверху / снизу, c", "refine_transverse_chords", "0.375")]):
        app.entry(refine, row, label, key, value)
    ttk.Label(refine, text="У профиля — вдвое больше ячеек на хорду.\nДополнительные параметры и приближение к стенке доступны в сеткопостроителе.",
              wraplength=280, style="Muted.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=7)
    ttk.Label(app.form_pages["mesh"].body, textvariable=app.resource_note, wraplength=300).pack(fill="x", pady=6)
    ttk.Button(app.form_pages["mesh"].body, text="Обновить сетку", command=app.preview_mesh).pack(fill="x", pady=3)
    ttk.Button(app.form_pages["mesh"].body, text="Открыть сеткопостроитель…", command=app.open_mesh).pack(fill="x", pady=3)

    cpu = group("calculation", "Вычисления")
    app.entry(cpu, 0, "Потоков CPU", "openmp_threads", "1")
    app.entry(cpu, 1, "Скорость на решётке", "lattice_speed", "0.04")
    app.sgs = tk.StringVar(value="Без явной SGS")
    choice = ttk.Frame(cpu)
    choice.grid(row=2, column=0, columnspan=2, sticky='ew', pady=5)
    ttk.Label(choice, text="Подсеточная модель", wraplength=260).pack(anchor='w')
    ttk.Combobox(choice, textvariable=app.sgs, state="readonly", width=16, values=["Без явной SGS", "Smagorinsky"]).pack(fill='x', pady=(3,0))
    ttk.Label(cpu, text="Два уровня: CPU, без явной SGS.\nЧисло потоков сохраняется в проекте.", wraplength=280, style="Muted.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=7)
    run = group("calculation", "Время и запись")
    for row, (label, key, value) in enumerate([
        ("Длительность tU/c", "duration_convective", "0.2"), ("Осреднять после tU/c", "average_after", "0.1"),
        ("Силы: каждые N шагов", "sample_every", "10"), ("Кадры: каждые N шагов", "frame_every", "20"),
        ("Невязки: каждые N шагов", "check_every", "10"), ("Checkpoint: N шагов", "checkpoint_every", "1000")]):
        app.entry(run, row, label, key, value)
    ttk.Label(run, text="По умолчанию — короткая проверка запуска. Для исследования нужны проверки сетки, области, размаха и статистики.",
              wraplength=280, style="Muted.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=7)
    ttk.Button(app.form_pages["calculation"].body, text="Невязки и условия окончания…", command=app.open_convergence).pack(fill="x", pady=3)
    ttk.Label(app.form_pages["calculation"].body, text="При отрыве течение может быть нестационарным: малые невязки не являются обязательным условием окончания.", wraplength=300, style="Muted.TLabel").pack(fill="x", pady=9)

    context = ttk.Frame(app.content_host, style="Surface.TFrame", padding=(10, 7))
    context.pack(fill="x", padx=(6, 0), pady=(0, 7))
    ttk.Label(context, textvariable=app.point_note, style="Surface.TLabel").pack(anchor="w")
    app.output_label = ttk.Label(context, text="Результаты: папка ещё не выбрана", style="Subtitle.TLabel", wraplength=760)
    app.output_label.pack(anchor="w")
    context.bind("<Configure>", lambda event: app.output_label.configure(wraplength=max(200, event.width - 20)))
    app.tabs = ttk.Notebook(app.content_host)
    app.tabs.pack(fill="both", expand=True, padx=(6, 0))
    app.figures, app.canvases, app.tab_frames = {}, {}, {}
    for key, label in (("geometry", "Профиль"), ("mesh", "Сетка"), ("history", "Cl / Cd / Cm"),
                       ("statistics", "Средние"), ("field", "Поле течения"), ("experiment", "Продувки")):
        frame = ttk.Frame(app.tabs)
        app.tabs.add(frame, text=label)
        app.tab_frames[key] = frame
        if key == "mesh":
            bar = ttk.Frame(frame, padding=4)
            bar.pack(fill="x")
            ttk.Button(bar, text="Вся область", command=lambda: app.redraw_mesh(False)).pack(side="left", padx=2)
            ttk.Button(bar, text="К профилю", command=lambda: app.redraw_mesh(True)).pack(side="left", padx=2)
        if key == "history":
            ttk.Label(frame, textvariable=app.sample_note, padding=(8, 5), style="Muted.TLabel").pack(fill="x")
        if key == "statistics":
            bar = ttk.Frame(frame, padding=5)
            bar.pack(fill="x")
            ttk.Label(bar, text="После tU/c:").pack(side="left")
            app.analysis_cutoff = tk.StringVar(value="")
            entry = ttk.Entry(bar, textvariable=app.analysis_cutoff, width=9)
            entry.pack(side="left", padx=4)
            refresh = ttk.Button(bar, text="Пересчитать", command=app.refresh_statistics)
            refresh.pack(side="left", padx=3)
            export = ttk.Button(bar, text="Отчёт для проверки…", command=app.export_analysis)
            export.pack(side="right", padx=3)
            app.busy_widgets.extend((entry, refresh, export))
            app.statistics_note = tk.StringVar(value="Откройте завершённый или остановленный расчёт.")
            ttk.Label(frame, textvariable=app.statistics_note, wraplength=690, padding=5).pack(fill="x")
            columns = ("coefficient", "mean", "rms", "first", "last", "change")
            app.statistics_table = ttk.Treeview(frame, columns=columns, show="headings", height=3)
            for column, text in zip(columns, ("Коэф.", "Среднее", "RMS", "1-я половина", "2-я половина", "Разность")):
                app.statistics_table.heading(column, text=text)
                app.statistics_table.column(column, width=92, minwidth=60, anchor="center")
            app.statistics_table.pack(fill="x", padx=5, pady=4)
            ttk.Label(frame, text="RMS — пульсации, не ошибка среднего. Отсечка анализа не меняет исходные данные.", wraplength=690, padding=5, style="Muted.TLabel").pack(fill="x")
        if key == "field":
            bar = ttk.Frame(frame, padding=4)
            bar.pack(fill="x")
            app.field_choice = tk.StringVar(value="Скорость |u|/U∞")
            app.field_choices = {"Скорость |u|/U∞": "speed", "Скорость, м/с": "speed_m_s", "Давление Cp": "cp",
                                 "Давление p−p∞, Па": "pressure_pa", "Вихрь ωz c/U∞": "omega_z"}
            combo = ttk.Combobox(bar, textvariable=app.field_choice, values=list(app.field_choices), state="readonly", width=25)
            combo.pack(side="left", padx=4)
            combo.bind("<<ComboboxSelected>>", lambda _: app.draw_frame())
            app.gif_button = ttk.Button(bar, text="Сохранить GIF…", command=app.export_animation)
            app.gif_button.pack(side="right", padx=4)
            ttk.Label(frame, textvariable=app.frame_note, padding=(8, 2), style="Muted.TLabel").pack(fill="x")
            app.frame_index = tk.IntVar(value=0)
            app.slider = ttk.Scale(frame, from_=0, to=0, variable=app.frame_index, command=lambda _: app.draw_frame())
            app.slider.pack(fill="x", padx=8)
        if key == "experiment":
            from .experiments import catalog
            app.references = catalog()
            app.reference_choice = tk.StringVar(value=app.references[0]["label"])
            combo = ttk.Combobox(frame, textvariable=app.reference_choice, values=[s["label"] for s in app.references], state="readonly")
            combo.pack(fill="x", padx=5, pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _: app.draw_experiment())
            app.reference_note = ttk.Label(frame, wraplength=700, padding=5)
            app.reference_note.pack(fill="x")
            ttk.Button(frame, text="Сравнить расчёт с продувкой", command=app.compare).pack(anchor="w", padx=5)
        figure = Figure(figsize=(7, 5), dpi=95, constrained_layout=True, facecolor="white")
        canvas = FigureCanvasTkAgg(figure, master=frame)
        # Pack toolbar first to keep it reachable even in a short window.
        nav = NavigationToolbar2Tk(canvas, frame, pack_toolbar=False)
        nav.pack(side="bottom", fill="x")
        canvas.get_tk_widget().pack(fill="both", expand=True)
        app.figures[key], app.canvases[key], app.toolbars[key] = figure, canvas, nav
    log_tab = ttk.Frame(app.tabs)
    app.tabs.add(log_tab, text="Журнал")
    app.tab_frames["log"] = log_tab
    scroll = ttk.Scrollbar(log_tab)
    scroll.pack(side="right", fill="y")
    app.log = tk.Text(log_tab, wrap="word", state="disabled", background="white", foreground=COLORS["ink"],
                      relief="flat", padx=10, pady=10, yscrollcommand=scroll.set)
    app.log.pack(fill="both", expand=True)
    scroll.configure(command=app.log.yview)
