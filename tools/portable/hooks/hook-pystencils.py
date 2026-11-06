from PyInstaller.utils.hooks import collect_data_files, collect_submodules
datas = collect_data_files('pystencils', include_py_files=True)
hiddenimports = collect_submodules('pystencils', filter=lambda n: not any(p in n.split('.') for p in ('tests', 'testing', 'benchmarks')))
module_collection_mode = 'py'
