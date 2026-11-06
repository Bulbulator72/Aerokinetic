"""Collect completed 0.4 verification evidence without changing calculations."""
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    root=Path(__file__).resolve().parents[2];evidence=Path(__file__).resolve().parent
    release=Path('/workspace/aerokinetic_040_release');dev=Path('/workspace/aerokinetic_040_dev')
    offline=read(release/'offline/OFFLINE_CHECK.json');benchmark=read(release/'benchmark/CHECK.json')
    gui=read(release/'gui/CHECK.json');final_gui=read(release/'gui_final/CHECK.json')
    stats=read(release/'gui_statistics/CHECK.json')
    for item in (offline,benchmark,gui,final_gui,stats):assert item['status']=='passed'
    legacy=read(dev/'reference030/new_continuation_check.json')
    assert max(legacy['pdf_errors'])<=2e-12 and legacy['max_load_error']<=1e-10
    wheel=release/'final_dist_v2/aerokinetic-0.4.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel) as archive:
        assert archive.testzip() is None
        files=[n for n in archive.namelist() if n.startswith('aerokinetic/')]
        assert all(archive.read(n)==(root/'src'/n).read_bytes() for n in files)
    wheel_check={'status':'passed','wheel':wheel.name,'bytes':wheel.stat().st_size,
                 'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'runtime_files':len(files),
                 'runtime_matches_source':True,'dependencies_added':False,'native_windows':'not_run'}
    write(evidence/'WHEEL_CHECK.json',wheel_check)
    pairs=[(release/'offline/OFFLINE_CHECK.json','OFFLINE_CHECK.json'),
           (release/'offline/interface/checks.json','INTERFACE_CHECKS.json'),
           (release/'offline/tests.xml','tests.xml'),
           (release/'offline/tests_first_attempt.xml','tests_first_attempt.xml'),
           (release/'offline/uniform_corrected.xml','uniform_corrected.xml'),
           (release/'offline/TEST_RECHECK.json','TEST_RECHECK.json'),
           (release/'benchmark/CHECK.json','BENCHMARK_CPU_CHECK.json'),
           (release/'gui/CHECK.json','GUI_WORKER_CHECK.json'),
           (release/'gui_final/CHECK.json','GUI_FINAL_CHECK.json'),
           (release/'gui_statistics/CHECK.json','GUI_STATISTICS_CHECK.json'),
           (release/'gui_final/mesh_refined.png','mesh_builder.png'),
           (release/'gui_final/residuals.png','residuals_window.png'),
           (dev/'reference030/new_continuation_check.json','REFERENCE_030_CHECK.json'),
           (dev/'reference030/identity.json','REFERENCE_030_IDENTITY.json'),
           (dev/'controls_tests.xml','source_controls_tests.xml'),
           (dev/'run_controls_tests.xml','source_run_controls_tests.xml'),
           (dev/'airfoil_regression.xml','source_airfoil_regression.xml')]
    run=release/'offline/airfoil'
    for name in ('project.json','metadata.json','summary.json','history.csv','residuals.csv','experiment_ohtake_re30000.json'):
        pairs.append((run/name,'startup_'+name))
    for source,target in pairs:shutil.copy2(source,evidence/target)
    checks={'implementation':{'status':'passed','installed_tests':offline['tests_passed'],
                'first_test_attempt':'61 passed; test-only ownership correction; see TEST_RECHECK.json',
                'corrected_uniform_test':'passed with original 2e-12 tolerance',
                'additional_source_airfoil_regression':19},
            'threaded_interface':{'status':'passed','positive_cases':10,'negative_control':'passed'},
            'residuals_and_stopping':{'status':'passed','four_conservative_components':True,'checkpointed_monitor':True},
            'gui':{'status':'passed','actual_worker_threads':2,'mesh_and_conditions':True,'final_ui_sync':True},
            'offline_linux_cpu':{'status':'passed','python_network_attempts':offline['network_attempts'],'fresh_jit':True},
            'performance':{'status':'passed','benchmark':benchmark['results'][-1]['speedup_against_first'],
                'linear_thread_scaling':'not_established'},
            'numerical_aerodynamic_uncertainty':{'status':'not_run'},
            'wind_tunnel_accuracy':{'status':'inconclusive'},'windows_msvc':{'status':'not_run'},'gpu':{'status':'not_run'}}
    write(evidence/'CHECKS.json',checks)
    results=benchmark['results'];summary=offline['actual_airfoil']
    point=offline['preliminary_experiment']['differences'][0]
    table='\n'.join(f"| {r['version']} | {r['threads']} | {r['median_seconds_per_coarse_step']:.6f} | {r['median_cpu_time_per_wall_time']:.4f} |" for r in results)
    text=f'''# Проверка AeroKinetic 0.4.0

27 октября 2026 г. Реализованы сеткопостроитель, четыре невязки
стационарности, условия окончания и CPU OpenMP для двух уровней.
Преобразования PDF↔моменты выполняются скомпилированным кодом.
Физическая модель, вязкости, геометрия и оператор переноса не заменялись.

## Статусы

| Направление | Статус |
|---|---|
| Реализация / релевантная регрессия | passed |
| Четыре нормы, условия и продолжение | passed |
| Расчёт и совпадение 1/2 потоков | passed |
| Жидкостный стык с новым CPU-преобразованием | passed |
| Окна / сохранение / реальные кнопки | passed, Linux Tk |
| Автономный runtime | passed, Linux CPU |
| Неопределённость аэродинамики профиля | not_run |
| Точность Cl/Cd/Cm по продувкам | inconclusive |
| Windows/MSVC и GPU | not_run |

Критерии зафиксированы до исполнения: [PLAN.json](PLAN.json).
Итоговые статусы — [CHECKS.json](CHECKS.json). Новые невязки имеют определение
d(rho,j_common)/dt* с объёмными L2/L∞, а не выдаются за невязки внутреннего
неявного NS-решателя. Интегральный баланс не управляет автоостановом.

## Проверки

Установленный пакет прошёл {offline['tests_passed']} релевантных теста:
геометрия/единицы/план, описательная статистика и экспериментальные данные,
сетка, нормы четырёх компонент, объёмные веса, dt, минимальное время,
сброс/восстановление счётчика, условия окончания и CPU-преобразования.
Проверены оба режима 1/2 потоков, фактические OpenMP num_threads(2),
нагрузки/PDF, контрольные состояния, CSV и отклонение повреждённого префикса.
Включены независимые аналитические затухания сдвига и дробная стенка Bouzidi.
Дополнительно 19 тестов регрессии профиля/нагрузок/продолжения прошли
на исходниках; численный runtime итогового wheel побайтно им соответствует.

В первом полном исполнении 61 тест прошёл, один новый тест сравнивал
также нетронутые ghost/solid-области равномерного решателя и упал.
Это была ошибка области теста: критерий относится к физическим жидким PDF.
После исправления маски он повторно прошёл **с тем же допуском 2·10⁻¹²**.
Runtime не менялся. Сохранены [первый журнал](tests_first_attempt.xml),
[повторный](uniform_corrected.xml), [описание](TEST_RECHECK.json) и итоговый
[JUnit](tests.xml), в котором заменён только этот тест. Отказы не скрыты,
численные допуски не расширялись.

Преобразования проверены для обоих представлений распределений, случайного
порядка D3Q27, плотности/импульса/стрессов и недопустимых состояний.
Ограничения положительности и Mach сохраняются, отсечения нет.
Официальное состояние 0.3.0 продолжено новым backend: максимум различия PDF
{max(legacy['pdf_errors']):.6g}, различия нагрузок {legacy['max_load_error']:.6g}
за четыре следующих шага. Это проверка совместимости; длительная
траектория хаотического течения не обязана совпадать побитно.

Новый параллельный жидкостный блок прошёл три сетки наклонной волны,
три сетки вихря через угол, вихри через грань/ребро, акустический пакет
в обе стороны и отрицательный контроль отключённого согласования потоков.
[INTERFACE_CHECKS.json](INTERFACE_CHECKS.json) содержит ошибки и балансы.

## Окна и продолжение

Проверены реальные Tk-кнопки: закрытие черновика сетки не меняет проект;
применение, увеличение, два потока и сохранение условий работают.
Неизменённые физика/seed/возмущения сохраняются. Неподходящая защитная
полоса отклоняется. При открытии проекта окно условий обновляет поля.
Рабочий поток реально останавливался и продолжался, затем завершился
по пределу 30 шагов. Получены 15 записей четырёх невязок, три вида графиков
и GIF давления. Повторных строк и Tk-ошибок нет.
Экспорт статистики сохраняет исходные файлы; новый архив содержит residuals.csv.
См. [GUI_WORKER_CHECK.json](GUI_WORKER_CHECK.json),
[GUI_FINAL_CHECK.json](GUI_FINAL_CHECK.json),
[GUI_STATISTICS_CHECK.json](GUI_STATISTICS_CHECK.json),
[сетку](mesh_builder.png) и [невязки](residuals_window.png).

## Скорость

Тот же NACA0012, Re=10000, 5°, c=0.15 м, 32→64 ячейки/хорду,
область 1c/2c/1c, размах 0.5c. Четыре прогревочных шага, затем три
серии по восемь полных синхронизированных крупных шагов.
Включены обе мелкие стадии, стенка, нагрузки, потоки и балансы;
компиляция, невязки и файлы исключены.

| Версия | CPU-потоки | Медиана с/шаг | CPU-время / реальное время |
|---|---:|---:|---:|
{table}

Общий выигрыш последнего варианта относительно 0.3:
{results[-1]['speedup_against_first']:.4f}×. Это выигрыш всех изменений.
Отдельный выигрыш 1→2 потока здесь мал и не доказывает масштабирования.
Доступны три логических CPU при квоте 200000/100000, то есть двух ядер.
Потоки BLAS=1, OMP_WAIT_POLICY=PASSIVE. Часть обмена остаётся последовательной,
а объёмные массивы ограничены памятью. Результат i9-12900K на 4/8/16 потоках
не измерялся. Сырые серии и конфигурации: [BENCHMARK_CPU_CHECK.json](BENCHMARK_CPU_CHECK.json).

## Контрольный аэродинамический запуск

NACA0012 Re=30000, 5°, c=0.15 м, ISA, без SGS, 32→64, два CPU-потока,
то же короткое tU/c=0.2 и окно после 0.1, что и в 0.3.0.
Завершены {summary['steps']} крупных и {summary['fine_steps']} мелких шагов.
Средний Cl={summary['mean']['cl']:.9g}; эксперимент Ohtake, рис.5,
интерполированный Cl={point['experimental_interpolated']:.9g};
разность {point['difference']:+.9g}.
Различия средних с 0.3: {json.dumps(offline['mean_difference_from_030_startup'])}.

Это стартовый процесс, а не подтверждение аэродинамической точности.
Сходимость по разрешению/области/размаху/времени и статистике не установлена.
Cd/Cm в низкорейнольдсовом эталоне пока не извлечены. Сравнение с другой
версией собственного кода — регрессия реализации; экспериментальным
эталоном остаётся только продувка. Диапазон ядра Re=10⁴…10⁵, M≤0.15 прежний.
Контрольные данные — startup_*.json/csv и [OFFLINE_CHECK.json](OFFLINE_CHECK.json).

## Автономность и воспроизведение

Linux x86-64, CPython 3.12, NumPy 2.3.5, lbmpy/pystencils 1.4, SymPy 1.12.1.
Прежний закреплённый набор локальных зависимостей, новая изолированная venv,
установленный wheel, пустые кеши в начале проверки, новые OpenMP-ядра.
Python-соединения и DNS блокировались; попыток: {offline['network_attempts']}.
Пиковый RSS процесса Linux: {offline['linux_process_peak_rss_gib']:.3f} ГиБ;
не включает пик отдельных дочерних процессов компилятора.

```text
python -u tools/check_040_offline.py --output fresh_check
python tools/smoke_040.py --output fresh_gui_check
python tools/benchmark_cpu.py --output cpu_benchmark --threads 1 2
```

GUI-команда требует дисплея. Численный этап продолжен после успешного
повторения исправленного теста; полный исходный и повторный журналы сохранены.
Нативные Windows/MSVC и GPU здесь не исполнялись. Установщик Build Tools
и ошибка 5003 не менялись. Поставка включает один TXT/BAT, runtime wheel,
исходники, критерии, SHA-256 и инструкции; проверка точного доставленного
payload и отката хранится отдельно от самого payload, чтобы избежать
самовключения хеша файла.
'''
    (evidence/'REPORT_RU.md').write_text(text,encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
