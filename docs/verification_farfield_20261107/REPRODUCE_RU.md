# Воспроизведение проверок

Использовать CPython 3.12 x64 и закреплённые зависимости проекта, установленные
в отдельную среду из локального wheelhouse. Инструмент verify_farfield требует
импорта установленного wheel, а не editable-install. Выходные папки отдельных
случаев должны быть новыми. Установка и численные проверки — разные шаги.

Из папки исходного проекта, с Python установленной среды:

```text
python tools/verify_farfield.py --case all --output NEW_BOUNDARY_CHECKS
python tools/audit_uniform_loads.py --farfield characteristic_transport_v1 --output NEW_LOAD_AUDIT
python tools/smoke_052_offline.py --output NEW_COLD_OFFLINE
python -m pytest -q -o pythonpath= tests/test_farfield.py tests/test_portable_packaging.py tests/test_case_planning.py
```

Первый инструмент выполняет восемь групп с фиксированными критериями,
сохраняет сырые CSV/JSON, выбранные сечения и идентификаторы установленного
кода. Поле `riemann` в историческом имени CSV обозначает текущую проверяемую
исследовательскую границу; точная версия берётся из RUNTIME JSON и SHA-256.

В рабочей облачной среде перед запуском задавались локальные каталоги
PYSTENCILS_CONFIG, PYSTENCILS_CACHE_DIR, XDG_CACHE_HOME, MPLCONFIGDIR,
MPLBACKEND=Agg и OPENBLAS_NUM_THREADS=1. Для холодного smoke новые каталоги
создаёт сам инструмент; соединения и DNS из Python блокируются. Отдельной
системной сетевой песочницы эта проверка не устанавливает.

Прогоны профиля из неизменных входных файлов:

```text
python -m aerokinetic.cli run examples/naca0012_re30000_n32_characteristic_down3.json --output NEW_AIRFOIL_DOWN3
python -m aerokinetic.cli run examples/naca0012_re30000_n32_characteristic_down6.json --output NEW_AIRFOIL_DOWN6
```

Параметр `--farfield legacy` возвращает прежнюю границу для нового прогона.
Он меняет численную постановку, поэтому нельзя подменять им границу уже
идущего расчёта. Продление сохраняет режим и все граничные состояния:

```text
python -m aerokinetic.cli continue FINISHED_PARENT --additional-time 6 --output NEW_SEGMENT
```

Сравнение четырёх завершённых случаев выполняет `tools/compare_farfield.py`;
пути задаются через `--legacy-down3`, `--legacy-down6`,
`--characteristic-down3`, `--characteristic-down6`, `--output`.
Он проверяет неизменность физических параметров и окна 6…12, использует
только оцифрованные Cl/Cd/Cm Ohtake с указанными SHA-256 и не экстраполирует.
Неудачный прогон, не достигший этого окна, нельзя включать в таблицу средних.

Данные первоначальных отклонённых вариантов лежат отдельно. Их входные JSON
с `characteristic_v1` требуют сохранённого промежуточного wheel, а не новой
библиотеки: старое экспериментальное имя намеренно не переинтерпретируется.
Это промежуточные локальные исследования; прежние выпущенные проекты с
отсутствующим полем farfield продолжают использовать legacy.

Большие checkpoint и установленные окружения остаются в workspace. Их
отсутствие в Git не мешает воспроизведению новых прогонов по исходным JSON,
но для немедленного продолжения конкретной старой траектории нужен её
собственный checkpoint. Точные хеши включённых/исключённых файлов сохраняются
в научном и публикационном манифестах.
