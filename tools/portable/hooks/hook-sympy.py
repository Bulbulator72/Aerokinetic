from PyInstaller.utils.hooks import collect_data_files, collect_submodules
datas = collect_data_files('sympy', include_py_files=True, excludes=['**/tests/**', '**/benchmarks/**'])
hiddenimports = collect_submodules('sympy', filter=lambda n: not any(p in n.split('.') for p in ('tests', 'testing', 'benchmarks')))
module_collection_mode = 'py'
