# Исследовательская полная FD-реконструкция внешнего слоя

Это CPU1-прототип для проверки дальних границ, отдельно от установленного
пакета и GUI. Он не меняет выпущенное ядро0.5.1. Вязкость фиксирована:
nu=.04*32/30000. Входы AirfoilPilot ограничены N32/Re30000/U=.04/noSGS.
Не применять этот набор как готовый решатель высокого Re.

Нужны уже закреплённые зависимости проекта: Python3.12, lbmpy/pystencils1.4,
numpy2.3.5, sympy1.12.1. Новых загрузок или сервисов нет. На Linux проверены
численные результаты; Windows и автономная exe-сборка этого прототипа
не проверялись. Установка всего приложения описана в основных инструкциях.

Запускать из корня репозитория активным Python проекта. Для журналов и
выходов выбирать новые папки, чтобы не смешать результаты:

```text
python tools/exterior_checks/strain_check.py
python tools/exterior_checks/preflight.py
python tools/exterior_checks/noise_probe.py straight --output scratch/exterior_noise
python tools/exterior_checks/noise_probe.py rectangle --output scratch/exterior_rectangle
python tools/exterior_checks/acoustic_axis_probe.py --axis x --output scratch/exterior_sound_x
python tools/exterior_checks/acoustic_axis_probe.py --axis y --output scratch/exterior_sound_y
python tools/exterior_checks/budget_probe.py --output scratch/exterior_budget
python tools/exterior_checks/shear_probe.py --output scratch/exterior_shear
python tools/exterior_checks/vortex_probe.py corner --output scratch/exterior_corner
python tools/exterior_checks/vortex_probe.py seam --output scratch/exterior_seam
python tools/exterior_checks/radiation_probe.py --output scratch/exterior_radial
python tools/exterior_checks/glancing_probe.py --wavelength 4 --output scratch/exterior_tangent4
python tools/exterior_checks/glancing_probe.py --wavelength 8 --output scratch/exterior_tangent8
python tools/exterior_checks/glancing_probe.py --wavelength 16 --output scratch/exterior_tangent16
python tools/exterior_checks/glancing_probe.py --wavelength 16 --three-d --output scratch/exterior_tangent3d
python tools/exterior_checks/spectral_probe.py --output scratch/exterior_spectral
python tools/exterior_checks/airfoil_preflight.py --project examples/naca0012_re30000_n32_characteristic_down3.json --output scratch/exterior_body_preflight
python tools/exterior_checks/run_airfoil_pilot.py --project examples/naca0012_re30000_n32_characteristic_down3.json --output scratch/airfoil_down3
python tools/exterior_checks/run_airfoil_pilot.py --project examples/naca0012_re30000_n32_characteristic_down6.json --output scratch/airfoil_down6
python tools/exterior_checks/check_evidence.py --require-airfoil
```

Создать родительскую scratch до запуска. preflight.py сохраняет свой
результат в tools/exterior_checks/preflight, strain_check.py — рядом с
исходником. Остальные каталоги задаются --output. Повторять в новых папках.
Для воспроизводимости и ограничения потоков BLAS использовать
OPENBLAS_NUM_THREADS=1. Настройки локального C-компилятора и кэша брать
из установленной среды, не копировать облачные абсолютные пути.

Обязательные допуски зафиксированы до результатов в
docs/verification_exterior_fd_20261107/PLAN.json, GLANCING_PLAN.json и
AIRFOIL_PLAN.json. Скрипты пишут CHECKS.json: завершение процесса само
по себе не означает passed. Подробности и ограничения — в REPORT_RU.md.

Один служебный узел добавляется на каждой открытой стороне. У него явно
учитываемый обмен массой/импульсом; все27 PDF перестраиваются только там.
Исходная физическая область сохраняется. collide-stream хранит PDF до
столкновения. Старые checkpoint программы с другой фазой несовместимы;
AirfoilPilot использует отдельный исследовательский формат и точный CPU1
restart. Его NPZ/матрицы/кадры — локальные результаты, не часть поставки.
Дополнительный cold_restart_probe.py восстанавливает замороженную копию
checkpoint шага2400 в новом процессе и сравнивает шаги2401..2560 с
непрерывным родителем. analyze_airfoil.py анализирует только полностью
завершённые окна6..12, сверяет условия старых контролей и читает прежнюю
экспериментальную оцифровку. Команды для повторения анализа:

```text
python tools/exterior_checks/analyze_airfoil.py --project-root . --runs scratch --output scratch/exterior_comparison
```

В --runs ожидаются именно airfoil_down3 и airfoil_down6. check_evidence.py
проверяет опубликованный отчёт/хеши, а не новые файлы в scratch; он
не запускает CFD и не подтверждает физическую точность.

Линейный inviscid-предиктор характеристик, SSPRK3 и FD-напряжения не
являются полной нелинейной вязкой NSCBC. Glancing-тест проверяет
ограниченность/плотность; не подтверждает фазовую и вязкую точность
восстановленного из начального поля входящего инварианта. Один устойчивый
блок Фурье не доказывает устойчивость всех мод. N32 и окно6..12 не
подтверждают сеточную или статистическую сходимость профиля.
