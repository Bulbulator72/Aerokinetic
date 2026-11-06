"""Inspect the supplied startup run; this is not aerodynamic validation.

Run from an environment with the installed AeroKinetic 0.2.0 and Matplotlib:
    python benchmarks/user_run_20261026_203246_5e0a15/analyze.py

The original uploaded data are read only. Numerical reproduction evidence,
when present, was generated separately from its original project.json.
"""

import csv
from dataclasses import replace
import json
import math
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from aerokinetic import Project
from aerokinetic.experiments import compare_run


def main():
    root = Path(__file__).resolve().parent
    original = root / "original" / "run_20261026_203246_5e0a15"
    project = Project.load(original / "project.json")
    metadata = json.loads((original / "metadata.json").read_text(encoding="utf-8"))
    summary = json.loads((original / "summary.json").read_text(encoding="utf-8"))
    with (original / "history.csv").open(encoding="utf-8", newline="") as handle:
        rows = [{key: float(value) for key, value in row.items()} for row in csv.DictReader(handle)]
    selected = [row for row in rows if row["t_star"] >= summary["average_after_t_star"]]
    assert project.case_fingerprint == metadata["case_fingerprint"]
    assert len(selected) == summary["averaging_samples"]
    assert all(b["step"] > a["step"] for a, b in zip(rows, rows[1:]))
    for key in ("cl", "cd", "cm"):
        values = [row[key] for row in selected]
        assert math.isclose(float(np.mean(values)), summary["mean"][key], abs_tol=1e-12)
        assert math.isclose(float(np.std(values)), summary["rms_fluctuation"][key], abs_tol=1e-12)

    comparison = compare_run(original, "ohtake_re30000")
    (root / "comparison_only.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cl_reference = comparison["differences"][0]["experimental_interpolated"]

    fig = Figure(figsize=(9, 7), constrained_layout=True)
    FigureCanvasAgg(fig)
    for ax, key, label in zip(fig.subplots(3, 1, sharex=True), ("cl", "cd", "cm"), ("Cl", "Cd", "Cm")):
        ax.plot([r["t_star"] for r in rows], [r[key] for r in rows], "o-", markersize=3,
                color="#17648d", label="Расчёт: сохранённые выборки")
        ax.axvspan(summary["average_after_t_star"], summary["t_star"], color="#f1b867", alpha=0.2,
                   label="Окно среднего: 8 выборок")
        ax.axhline(summary["mean"][key], color="#b26313", linestyle="--", linewidth=1,
                   label="Среднее в указанном окне")
        if key == "cl":
            ax.axhline(cl_reference, color="#298346", linestyle=":", label="Продувка Ohtake: интерполяция Cl")
            ax.legend(fontsize=8, loc="upper right")
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
    ax.set_xlabel("tU∞/c")
    fig.suptitle("Пользовательский прогон: NACA 0012, Re=30 000, α=5°\n"
                 "Только запуск 0…0,2: совпадение с продувкой не установлено", fontsize=11)
    fig.savefig(root / "history_startup.png", dpi=160)
    fig.clear()

    last_frame = sorted((original / "frames").glob("*.npz"))[-1]
    with np.load(last_frame, allow_pickle=False) as data:
        speed, mask = data["speed"].copy(), data["mask"].copy()
        origin, dx, timestamp = data["origin"].copy(), float(data["dx"]), float(data["t_star"])
    assert np.isfinite(speed[~mask]).all() and np.isnan(speed[mask]).all()
    extent = [origin[0], origin[0] + speed.shape[0] * dx,
              origin[1], origin[1] + speed.shape[1] * dx]
    fig = Figure(figsize=(11, 4), constrained_layout=True)
    FigureCanvasAgg(fig)
    axes = fig.subplots(1, 2, sharex=True, sharey=True)
    for ax, interpolation, title in zip(axes, ("nearest", "bilinear"),
                                         ("Реальные ячейки поля", "Те же данные: интерполяция цветов")):
        image = ax.imshow(speed.T, extent=extent, origin="lower", interpolation=interpolation,
                          cmap="viridis", vmin=0, vmax=float(np.nanmax(speed)))
        for contour in project.section.normalized(project.flow.alpha_deg):
            xy = np.vstack((contour.xy, contour.xy[0]))
            ax.plot(xy[:, 0], xy[:, 1], color="white", linewidth=0.8)
        ax.set(xlim=(-0.25, 1.2), ylim=(-0.3, 0.3), aspect="equal", facecolor="0.35",
               xlabel="x/c от точки момента", title=title)
    axes[0].set_ylabel("y/c")
    fig.colorbar(image, ax=list(axes), label="|u|/U∞", shrink=0.8)
    fig.suptitle(f"Сечение посередине размаха, tU/c={timestamp:g}; 32 ячейки/хорду\n"
                 "Белая линия — исходный контур; расчётные массивы одинаковы", fontsize=11)
    fig.savefig(root / "field_resolution.png", dpi=160)
    fig.clear()

    # A modest next run measures execution cost and shows later transient flow.
    # It cannot establish spatial or statistical convergence of the experiment.
    pilot = replace(project, title="NACA0012 Re30000 alpha5 - diagnostic only, 64 cells/chord",
                    grid=replace(project.grid, cells_per_chord=64, memory_limit_gib=8),
                    run=replace(project.run, duration_convective=2, average_after=1,
                                sample_every=20, frame_every=100, check_every=20, checkpoint_every=1000))
    pilot.save(root / "next_diagnostic_64.json")
    assert pilot.plan().estimated_memory_gib < pilot.grid.memory_limit_gib
    plans = []
    for n in (32, 64, 96, 128, 192, 256):
        plan = replace(project, grid=replace(project.grid, cells_per_chord=n)).plan()
        plans.append({"cells_per_chord": n, "shape": plan.shape,
                      "estimated_memory_gib": plan.estimated_memory_gib})
    (root / "memory_plans.json").write_text(json.dumps(plans, indent=2) + "\n", encoding="utf-8")

    reproducibility = root / "cross_platform_reproduction.json"
    reproduced = json.loads(reproducibility.read_text()) if reproducibility.exists() else {"status": "not_run"}
    force_difference = max(abs(x) for x in reproduced.get("coefficient_differences", {}).values()) if reproduced["status"] == "passed" else None
    text = f"""AEROKINETIC — РАЗБОР ПОЛЬЗОВАТЕЛЬСКОГО ПРОГОНА

Исходный прогон: run_20261026_203246_5e0a15.
В архиве угол 5°. Снимок с Cl=-0.0292245 относится к другому прогону при 4°.

ФАКТИЧЕСКИЕ НАСТРОЙКИ
NACA0012 без закрылка, Re={project.flow.reynolds:g}, alpha={project.flow.alpha_deg:g}°.
Физическая хорда {project.flow.physical_chord_m:g} м, стандартная атмосфера ISA.
U∞={project.flow.physical_speed:.8g} м/с, физическое M∞={project.flow.physical_mach:.8g}.
Сетка {metadata['plan']['shape']}, 32 ячейки/хорду, равномерная 3D.
Шаг Δ/c={dx:g}; при хорде 1 м это 31,25 мм.
Максимум твёрдых ячеек в вертикальном столбце: {int(mask.sum(axis=1).max())}.
Размах 0,5c, периодичность; перед профилем 1,5c, за ним 3c, сверху/снизу 1,5c.
SGS выключена; это не подтверждение DNS.

ВРЕМЯ И СТАТИСТИКА
{summary['steps']} шагов, конец tU/c={summary['t_star']:g}, то есть {summary['t_star'] * project.flow.physical_chord_m / project.flow.physical_speed:.6g} с.
За это безразмерное время U∞t=0,2c: это запуск течения.
Всего {summary['samples']} выборок сил; среднее по {summary['averaging_samples']} выборкам tU/c=0,1…0,2.
Cl={summary['mean']['cl']:.9g}; Cd={summary['mean']['cd']:.9g}; Cm={summary['mean']['cm']:.9g}.
RMS пульсаций Cl={summary['rms_fluctuation']['cl']:.9g} — не ошибка среднего.
Первый сохранённый Cl={rows[0]['cl']:.9g}: в истории присутствует стартовый скачок.
Конец работы по заданному числу шагов не означает сходимость течения.
Время цикла расчёта по summary: {summary['wall_clock_seconds']:.3f} с; первоначальная JIT-компиляция сюда не входит.

ПРОВЕРКИ ДАННЫХ
CRC исходного архива проверен; SHA-256 записан в SOURCE.json.
Project fingerprint соответствует metadata; средние и RMS пересчитаны по CSV.
Все 9 кадров прочитаны; жидкие поля конечны, NaN соответствуют маске тела.
Максимальный решёточный локальный Ma по записанным выборкам: {max(r['local_mach_max'] for r in rows):.8g}.
Максимальное |rho/rho∞-1| по этим выборкам: {max(r['density_error_max'] for r in rows):.8g}.
Это диагностика состояния, не доказательство точности аэродинамики.
Воспроизведение исходного проекта на Linux CPU: {reproduced['status']}.
Максимальная абсолютная разность средних Cl/Cd/Cm между пользовательскими файлами и Linux: {force_difference}.
Проверены все 9 полей speed/Cp/omega_z; подробные разности в cross_platform_reproduction.json.
До прогона заданы допуски: 1e-8 для коэффициентов и 1e-9 для полей.
Повторён именно один короткий случай; это не полная проверка Windows/MSVC.

ПРОДУВКА
Ohtake, Nakae, Motohashi (2007), DOI 10.2322/jjsass.55.439, рис.5, Re=30000.
Линейно интерполированный Cl при 5°: {cl_reference:.9g}.
Разность данного стартового среднего и оцифровки: {summary['mean']['cl'] - cl_reference:+.9g}.
Статус: comparison_only; validation=inconclusive.
Границы оцифровки ±0,12° и ±0,008 Cl не являются погрешностью трубы.
Опыты Cd/Cm для этого Re пока не извлечены. Стенка трубы, геометрия реальной
модели, возмущения и условия перехода ещё требуют согласования.

ПОЧЕМУ ВИДНЫ ПИКСЕЛИ
Программа показывает дискретные ячейки через interpolation='nearest'.
По толщине профиля всего около четырёх ячеек; тонкие структуры не разрешены.
field_resolution.png показывает один и тот же массив с двумя вариантами
отрисовки. Интерполяция цветов не добавляет расчётных данных и не улучшает Cl.

ТОЧКА МОМЕНТА
В проекте x=0,0375 м при хорде 1 м: это 3,75% хорды, не c/4.
Если требуется c/4, соответствующее x=0,25 м. Cl/Cd от выбора точки не меняются.
В следующем диагностическом файле исходная точка сохранена, а не изменена молча.

СЛЕДУЮЩИЙ КОНТРОЛЬНЫЙ ПРОГОН
next_diagnostic_64.json: тот же профиль, Re, угол, хорда, точка момента,
область, периодический размах, SGS, seed, скорость решётки и один поток CPU.
64 ячейки/хорду, длительность 2 tU/c, среднее после 1 tU/c.
Всего {math.ceil(pilot.run.duration_convective / pilot.plan().dt_convective)} шагов.
Оценка памяти {pilot.plan().estimated_memory_gib:.3f} GiB при явном лимите 8 GiB.
Это оценка, не замер пика с JIT и сохранением контрольного состояния.
Назначение — посмотреть следующий участок переходного процесса и измерить
стоимость более мелкой сетки. Разрешение и время по-прежнему не валидированы;
порог tU/c=1 не является критерием установившегося течения.
Этот прогон нельзя считать эталонной полярой или исследованием сходимости.

ДАЛЬНЕЙШАЯ РАЗРАБОТКА
Локальное сгущение сетки около тела, пограничного слоя и следа с проверкой
балансов и переноса через интерфейс уровней. Затем независимые исследования
сетки, области, размаха, решёточного Ma/времени и статистики, после них
количественное сравнение с продувкой. Более гладкая картинка не заменяет эти шаги.

Решатель, wheel и файлы пользователя этим анализом не менялись.
"""
    (root / "REPORT_RU.txt").write_text(text, encoding="utf-8")
    print(json.dumps({"report": str(root / 'REPORT_RU.txt'), "reproduction": reproduced['status'],
                      "pilot_steps": math.ceil(pilot.run.duration_convective / pilot.plan().dt_convective),
                      "pilot_memory_gib_estimated": pilot.plan().estimated_memory_gib}, ensure_ascii=False))


if __name__ == "__main__":
    main()
