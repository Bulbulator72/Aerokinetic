"""Collect completed local release checks; never run or alter the CFD case."""
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    root=Path(__file__).resolve().parents[2]
    release=Path('/workspace/aerokinetic_030_release')
    evidence=Path(__file__).resolve().parent
    offline=read(release/'offline/OFFLINE_CHECK.json')
    gui=read(release/'gui/CHECK.json')
    stats=read(release/'gui_statistics_final/CHECK.json')
    legacy=read(release/'GUI_LEGACY_CHECK.json')
    transaction=read(release/'UPDATE_CHECK_draft.json')
    wheel=read(release/'WHEEL_CHECK.json')
    strict=next(ET.parse(release/'strict_moment_tests.xml').getroot().iter('testsuite'))
    assert int(strict.attrib['tests'])==1 and int(strict.attrib['failures'])==0 and int(strict.attrib['errors'])==0
    for check in (offline,gui,stats,legacy,transaction):assert check['status']=='passed'
    assert wheel['numerical_runtime_and_data_byte_identical']
    run=release/'offline/airfoil'
    for source,target in ((release/'offline/OFFLINE_CHECK.json','OFFLINE_CHECK.json'),
                          (release/'offline/tests.xml','tests.xml'),
                          (release/'offline/interface/checks.json','INTERFACE_CHECKS.json'),
                          (release/'gui/CHECK.json','GUI_CHECK.json'),
                          (release/'gui_statistics_final/CHECK.json','GUI_STATISTICS_CHECK.json'),
                          (release/'GUI_LEGACY_CHECK.json','GUI_LEGACY_CHECK.json'),
                          (release/'UPDATE_CHECK_draft.json','UPDATE_TRANSACTION_CHECK.json'),
                          (release/'WHEEL_CHECK.json','WHEEL_CHECK.json'),
                          (release/'strict_moment_tests.xml','strict_moment_tests.xml'),
                          (run/'project.json','startup_project.json'),
                          (run/'metadata.json','startup_metadata.json'),
                          (run/'summary.json','startup_summary.json'),
                          (run/'history.csv','startup_history.csv'),
                          (run/'experiment_ohtake_re30000.json','EXPERIMENT_COMPARISON.json'),
                          (run/'speed.png','startup_speed.png')):
        shutil.copy2(source,evidence/target)
    summary=offline['actual_airfoil']
    comparison=offline['preliminary_wind_tunnel_comparison']
    difference=comparison['differences'][0]
    checks={'implementation':{'status':'passed','tests':offline['tests_passed'],
               'pytest_seconds':float(next(ET.parse(release/'offline/tests.xml').getroot().iter('testsuite')).attrib['time']),
               'relevant_cases':'Fractional wall decay, geometry, isotropic pressure, symmetry, force/moment/time, budget, virtual fill isolation, restart, crash rollback'},
            'interface_regression':{'status':'passed','positive_cases':10,'negative_control':True},
            'strict_reference_moment_recheck':{'status':'passed','absolute_tolerance':2e-12},
            'numerical_uncertainty':{'status':'not_run','scope':'Airfoil mesh/domain/span/timestep-Mach/long-time statistics'},
            'wind_tunnel':{'status':'inconclusive','scope':'Short startup NACA0012 Re=30000 alpha=5; only digitized Cl available'},
            'offline_linux_cpu':{'status':'passed','fresh_jit':True,'python_network_attempts':offline['network_attempt_count']},
            'gui_linux':{'status':'passed','actual_refined_worker_stop_resume':True,'legacy_results':True,'lossless_analysis_cutoff_export':True},
            'offline_update_linux':{'status':'passed','fault_injection_rollback':True,'user_files_preserved':True},
            'final_wheel':wheel,'windows_msvc':{'status':'not_run'},'gpu':{'status':'not_run'}}
    write(evidence/'CHECKS.json',checks)
    text=f'''# Третий этап сгущения: неподвижный профиль

27 октября 2026 г. В исследовательской версии 0.3.0 реализован профиль
на двух уровнях 2:1: открытые границы, мелкая стенка Bouzidi, Cl/Cd/Cm,
поля и синхронизированное продолжение. **Реализация прошла проверки;
аэродинамическая точность пока не подтверждена.**

## Статусы

| Направление | Статус | Основание |
|---|---|---|
| Реализация и регрессия | passed | {offline['tests_passed']} тестов установленного пакета, свежая локальная JIT-компиляция |
| Жидкостный стык уровней | passed | 10 положительных переносов волны/вихря/пакета и отрицательный контроль |
| Сходимость аэродинамики | not_run | Нет трёх сеток, изменения области/размаха/шага и длинной статистики профиля |
| Экспериментальная точность | inconclusive | Только короткий запуск и оцифрованный Cl; низкорейнольдсовые Cd/Cm пока не извлечены |
| Автономная Linux CPU установка | passed | Закреплённые локальные wheels, --no-index, новая venv, заблокированы Python socket/DNS |
| Окно Linux Tk | passed | Проект, реальные stop/resume, 5 полей, GIF, статистика и экспорт, открытие старого результата |
| Обновление Linux Python | passed | Установка 0.2.1→0.3.0, отклонение повреждения, искусственный сбой и откат |
| Windows/MSVC, GPU | not_run | В этой среде нет соответствующего выполнения |

Исходные критерии зафиксированы в [PLAN.json](PLAN.json) до численного
выполнения. [CHECKS.json](CHECKS.json) разделяет эти статусы; pytest не
объединяет их в подтверждение физической точности.

## Что проверено независимо

- q-связи Bouzidi пересекают точную геометрию; постоянное изотропное давление
  даёт нулевые силу и момент с допуском 2·10⁻¹².
- Плоские дробные стенки y=2.2 и 13.6, ν=0.03, 400 шагов: аналитическое
  затухание синусоидальной сдвиговой моды с ошибкой скорости L2 не более 2%.
- NACA0012 при нулевом угле и без стартового возмущения: 20 крупных шагов,
  |Cl| и |Cm|≤2·10⁻¹⁰, Cd>0, ровно два мелких отражения на крупный шаг.
- Единицы сил сверены с независимой нормировкой мелкой решётки. Перенос
  точки момента проверен при 5° с ненулевым трёхмерным стартовым возмущением;
  отдельная повторная проверка выполнена с допуском 2·10⁻¹² из исходного плана.
- Интегралы исключают покрытые крупные и твёрдые мелкие ячейки. Остатки
  открытого баланса на каждом из 20 шагов: масса/объём≤5·10⁻¹¹,
  импульс/объём≤2·10⁻¹². Фактическая утечка стенки учитывается отдельно.
- Изменение неиспользуемого виртуального заполнения не меняет физические
  PDF за 8 крупных шагов с допуском 2·10⁻¹³. Периодические доноры не добавляют
  фиктивные силу и размах; диагностика/кадры не обновляют историю границ.
- Непрерывный и восстановленный расчёты совпадают по PDF до 5·10⁻¹³
  и коэффициентам до 10⁻¹⁰. Выход проверен с нетривиальной историей пакета.
  Сверены оба уровня, часы, q и структурированные массивы границ.
- CSV непрерывного и stop/resume-прогонов совпал побайтно. Записи/кадры после
  аварийного checkpoint перенесены в recovery. Повреждения истории,
  контрольных массивов и изменение проекта отклоняются.

При разработке исправлен порядок начальной подготовки выхода: история
ExtrapolationOutflow должна создаваться после заполнения начальных PDF,
включая призрачные слои. Также исправлены индексирование аналитического
теста стенки и имя метода в новом тесте продолжения; допуски не расширялись.
Проверка GUI выявила округление отсечки анализа при повторном экспорте;
исправлен её числовой roundtrip, новая проверка экспорта прошла.

## Воспроизводимый короткий расчёт профиля

Случай: NACA0012 с незамкнутой формулой задней кромки, Re=30000, α=5°,
ISA, физическая хорда 0.15 м, без SGS. Крупная сетка 32 ячейки/хорду,
мелкая 64; периодический размах 0.5c. Область: 1.5c перед профилем,
3c за ним, 1.5c сверху/снизу. Мелкие отступы: 0.375c, 0.75c, 0.375c.
Решёточная скорость 0.04; τb крупного уровня 1.5. Полные параметры и
геометрия — [startup_metadata.json](startup_metadata.json) и
[startup_project.json](startup_project.json).

Выполнено {summary['steps']} крупных и {summary['fine_steps']} мелких шагов,
конечное tU/c={summary['t_star']:.6g}; окно усреднения после
{summary['average_after_t_star']:.6g}, {summary['averaging_samples']} выборок.
Это импульсный запуск, без доказанного исключения стартового процесса.
Нет оснований объявлять эту сетку разрешённой DNS.

| Коэффициент | Среднее короткого окна | RMS пульсаций |
|---|---:|---:|
| Cl | {summary['mean']['cl']:.9g} | {summary['rms']['cl']:.9g} |
| Cd | {summary['mean']['cd']:.9g} | {summary['rms']['cd']:.9g} |
| Cm | {summary['mean']['cm']:.9g} | {summary['rms']['cm']:.9g} |

Сырые данные: [startup_history.csv](startup_history.csv),
[startup_summary.json](startup_summary.json), [поле скорости](startup_speed.png).
Поля показывают реальные ячейки; интерполяция рисунка не создаёт разрешения.
Время сил — середина крупного интервала, среднее двух мелких подшагов;
кадр относится к концу крупного шага.

Максимум остатка по записанным выборкам этого случая: масса/объём
{offline['sampled_budget_maxima']['max_abs_mass_residual_per_volume']:.6g}, импульс/объём
{offline['sampled_budget_maxima']['max_abs_momentum_residual_per_volume']:.6g}.
Накопленный обмен массой стенки:
{summary['diagnostics']['cumulative_wall_mass_exchange']:.8g}, относительно начальной
массы жидкости {summary['diagnostics']['cumulative_wall_mass_exchange_over_initial_mass']:.8g}.
**Малый остаток баланса не доказывает точность непроницаемости стенки.**

## Предварительное сопоставление с продувкой

Ohtake, Nakae, Motohashi (2007), DOI
https://doi.org/10.2322/jjsass.55.439, рис.5, Re=30000.
Сохранённая серия содержит только оцифрованный Cl. Внутри серии применяется
линейная интерполяция; экстраполяция запрещена. При 5°:

- опытный интерполированный Cl={difference['experimental_interpolated']:.9g};
- среднее расчётного короткого окна Cl={difference['calculated']:.9g};
- знаковая разность расчёт−опыт={difference['difference']:+.9g}.

Это сравнение интерфейса и сохранённых данных, **не приёмка точности**.
Не установлены сходимость и статистическая стационарность, соответствие
реальной задней кромки, перехода, турбулентности и поправок трубы.
Периодический размах не эквивалентен стенкам/торцам трубы автоматически.
Погрешность оцифровки не является полной экспериментальной неопределённостью.
NASA при Re порядка 6 млн выходит за рабочий диапазон текущего ядра.
Другие CFD-решения в качестве эталона не использовались.
[Полный результат сопоставления](EXPERIMENT_COMPARISON.json).

## Поставка и воспроизведение

Linux x86-64, Python 3.12.14, NumPy 2.3.5, lbmpy/pystencils 1.4,
SymPy 1.12.1, CPU float64, один поток. Использован прежний локальный lock,
новых сторонних зависимостей нет. Созданы новая venv и пустые JIT-кеши;
Python-соединения и DNS блокировались, число попыток:
{offline['network_attempt_count']}. Наблюдавшийся пиковый RSS всего проверяющего
процесса Linux — {offline['linux_cpu_peak_rss_gib']:.3f} ГиБ; это не измерение
памяти Windows и не включает пиковую память отдельных процессов компилятора.

```text
python -m pip install --no-index --find-links downloads/wheels_linux -r requirements-offline-py312.txt aerokinetic-0.3.0-py3-none-any.whl
python -u tools/check_refined_offline.py --output fresh_check
python tools/smoke_refined.py --output fresh_gui_check
```

Последняя команда требует рабочего дисплея. Отдельная GUI-проверка экспорта:
`python tools/smoke_statistics.py <папка_расчёта> --output fresh_statistics`.
Проверяются установленный пакет и действительные кнопки, не src через PYTHONPATH.

Полная численная серия выполнена на wheel-кандидате. После неё исправлена
только точность текстового поля отсечки GUI. [WHEEL_CHECK.json](WHEEL_CHECK.json)
доказывает побайтную идентичность всего численного runtime и данных итогового
wheel; итоговый GUI отдельно проверен через установленное обновление.
Результат всей серии — [OFFLINE_CHECK.json](OFFLINE_CHECK.json),
регрессия стыка — [INTERFACE_CHECKS.json](INTERFACE_CHECKS.json).
Снимок исходников — [SOURCE_SHA256.json](SOURCE_SHA256.json).

Текстовый BAT содержит wheel и исходники, SHA-256 и резервную копию;
проверены CRLF/LF, ZIP CRC, файлы манифеста и Linux-транзакция обновления.
Точный SHA-256 доставляемого TXT хранится во внешнем файле проверки поставки,
чтобы избежать включения хеша файла в самого себя.
Старые результаты 0.2.1 открылись без изменения их файлов. Нативный BAT,
Windows/MSVC и GPU остаются `not_run`; ошибка установщика 5003 этим не исправляется.

Следующий этап — численная неопределённость профиля по сетке, размерам мелкой
и внешней областей, размаху, шагу/Mach и длительности. Только после этого
имеет смысл оценивать ошибку установившейся статистики по продувкам.
'''
    (evidence/'REPORT_RU.md').write_text(text,encoding='utf-8')
    items=[p for p in evidence.iterdir() if p.is_file() and p.name!='ARTIFACT_SHA256.json']
    write(evidence/'ARTIFACT_SHA256.json', {p.name:{'bytes':p.stat().st_size,
          'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(items)})
    print(json.dumps(checks,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
