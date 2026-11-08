# Проверки библиотечного внешнегоFD

Использовать закреплённые зависимости проекта и установленный wheel0.5.3.
Для pytest очистить настройку pythonpath, чтобы проверялся wheel:

```text
python -m pytest -q -o pythonpath= tests/test_exterior.py
```

Сначала создать родительскую scratch; все выходные папки должны быть новыми.

```text
python tools/exterior_library_checks/shear_transport.py --output scratch/shear_initial
python tools/exterior_library_checks/periodic_transport.py --output scratch/shear_periodic
python tools/exterior_library_checks/fd_parameters.py --output scratch/fd_parameters
python tools/exterior_library_checks/run_offline_check.py --python /absolute/venv/bin/python --output scratch/offline
python tools/exterior_library_checks/finer_budget.py --project examples/naca0012_re30000_n64_exterior_down6.json --output scratch/finer_budget
```

Первый shear_initial сохраняет исходный failed-протокол: начальное
равновесное PDF не является чистой гидродинамической сдвиговой модой.
periodic_transport независимо измеряет позднюю вязкость и предсказывает
весь начальный переход полным27x27 оператором. Допуск5% не меняется.
Не переименовывать первый failed в passed; читать оба протокола и отчёт.

run_offline_check — Linux-проверка с новым кэшем и сетевым запретом,
а не установщик. Аргумент --python должен указывать на Python внутри
изолированного venv с установленным wheel. Для такой установки:

```text
python -m venv scratch/env
scratch/env/bin/python -m pip install --no-index --find-links downloads/wheels_linux -r requirements-offline-py312.txt
scratch/env/bin/python -m pip install --no-index --no-deps path/to/aerokinetic-0.5.3-py3-none-any.whl
```

Эквивалентность замороженному прототипу проверяется двумя интерпретаторами:

```text
old-env/bin/python tools/exterior_library_checks/equivalence.py prepare --frozen-root path/to/frozen7Nov --project examples/naca0012_re30000_n32_characteristic_down3.json --output scratch/reference
new-env/bin/python tools/exterior_library_checks/equivalence.py compare --reference scratch/reference --output scratch/equivalence
```

prepare требует неизменённый0.5.2 и точный SHA прототипа7 ноября.
Необязательные повторяемые --checkpoint добавляют16-шаговые проверки
из его нелинейных состояний. Их NPZ нужны локально, не включены в Git.

Полные сопоставимые прогоны двух выходов:

```text
python tools/exterior_library_checks/matched_airfoils.py --project-root . --prototype-runs docs/verification_exterior_fd_20261107/raw --output scratch/library_cases --threads 2
```

Они требуют исходные полные истории прототипа для сравнения по фиксированному
окну6..12. Эти CSV уже включены в каталог raw отчёта 7 ноября;
его бинарные checkpoint для этой команды не нужны.
Новые CFD-файлы не смешиваются со старыми. Проверка кода и
эквивалентности не означает совпадения с продувкой или сеточной сходимости.
Научные планы, допуски, результаты и ограничения — в docs/verification_exterior_library_20261108.

После завершения обоих профилей повторить анализ и проверить сохранённый
пакет доказательств:

```text
python tools/exterior_checks/analyze_airfoil.py --project-root . --runs scratch/library_cases --output scratch/comparison
python tools/exterior_library_checks/analyze_mass.py --runs scratch/library_cases --output scratch/mass --plot
python tools/exterior_library_checks/check_evidence.py --project-root .
```

Последняя команда проверяет включённый в репозиторий текстовый пакет,
исходники и фиксированные окна. Она не запускает новое CFD и не заменяет
верификацию вновь изменённого решателя.
