from PyInstaller.utils.hooks import collect_data_files, copy_metadata
datas = collect_data_files('aerokinetic') + copy_metadata('aerokinetic', recursive=True)
# Checkpoint identity reads the exact scientific sources on disk.
module_collection_mode = 'py'
