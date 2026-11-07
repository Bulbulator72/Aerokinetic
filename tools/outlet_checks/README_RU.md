# Проверки исследовательской выходной границы

Это инструменты для разработки ветки `research/characteristic-farfield-20261107`.
Они требуют установленный исследовательский пакет0.5.2 и его зависимости.
Они не являются обновлением Windows-программы и не разрешают повышать Re/Ma.

Запускать из корня проекта в активном окружении, например:

```text
python tools/outlet_checks/noise.py straight_outlet --output runs/noise_straight
python tools/outlet_checks/noise.py rectangle --candidate convective --output runs/noise_convective
python tools/outlet_checks/spectrum.py --candidate original --mode 23 2 --ny 48 --output runs/spectrum_23_2
python tools/outlet_checks/spectrum.py --candidate original --mode 23 2 --ny 48 --epsilon 5e-7 --output runs/spectrum_half
python tools/outlet_checks/physics.py --candidate convective --groups preflight budget shear vortex --output runs/physics_a
python tools/outlet_checks/physics.py --candidate convective --groups radiation seam_vortex glancing glancing_3d --output runs/physics_b
python tools/outlet_checks/acoustic.py --candidate copy --output runs/copy_acoustic
```

Каждая папка вывода должна быть новой. Фиксированные физические параметры,
seed и допуски записаны в `docs/verification_outlet_20261107/*PLAN.json`.
В Linux задавать `OPENBLAS_NUM_THREADS=1`; при недоступной домашней папке
нужны доступные пути для `PYSTENCILS_CONFIG`, `PYSTENCILS_CACHE_DIR` и
`XDG_CACHE_HOME`. Их рабочие значения сохранены в отчёте.

`original` загружает установленный `aerokinetic.farfield`. Другие варианты
загружаются из точных снимков `docs/verification_outlet_20261107/prototypes/`.
Название варианта и SHA-256 численного граничного кода сохраняются в отчёте.
Исторические снимки содержат исходные абсолютные пути среды опыта; для
повторения использовать эти четыре переносимые команды. Их изменения
путей импорта не меняют формулы и параметры опытов.

`noise.py` проверяет рост одинакового малого широкополосного возмущения.
`spectrum.py` строит центральной разностью блок полного оператора
столкновение/перенос/граница для одной поперечной Fourier-моды, включая
память границы. Он проверяет независимое отображение комплексной моды и
согласованность копий состояния по связям. Увеличение собственного числа
выше1 означает рост этой линейной моды. Проверка одного блока не является
доказательством устойчивости всех волн или точности обтекания профиля.

Матрица `operator.npy` и поля NPZ остаются локальными. Для передачи достаточно
текстовых CSV/JSON, исходников и SHA-256. Все исследованные здесь варианты
сохраняются независимо от того, прошли ли они допуски. Статус выпуска
определяется отчётом, а не наличием варианта в списке параметров команды.
