from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
datas = collect_data_files('aerokinetic') + copy_metadata('aerokinetic', recursive=True)
# Runtime identity includes CLI and lazy API modules as well as GUI imports.
hiddenimports = collect_submodules('aerokinetic')
# Checkpoint and runtime identity read the exact scientific sources on disk.
module_collection_mode = 'py'
