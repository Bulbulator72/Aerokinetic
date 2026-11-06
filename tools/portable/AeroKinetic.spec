# PyInstaller 6.16, onedir. Target platform is the interpreter running this spec.
import os
import sys
from pathlib import Path

kit = Path(os.environ['AK_PORTABLE_KIT'])
stage = Path(os.environ['AK_PORTABLE_STAGE'])
tools = kit / 'tools/portable'
extra_binaries = []
extra_data = [(str(stage / 'portable'), 'portable')]
runtime_dlls = []
if sys.platform == 'win32':
    runtime_dlls = list((stage / 'portable/toolchain/runtime').glob('*.dll'))
    extra_binaries.extend((str(path), '.') for path in runtime_dlls)
if sys.platform == 'linux':
    # The managed development Python uses Tcl/Tk 9; PyInstaller 6.16's
    # automatic search misses this layout. Windows uses its standard hook.
    library = Path(sys.base_prefix) / 'lib'
    for path in library.glob('libtcl*.so'):
        extra_binaries.append((str(path), '.'))
    for source, destination in (('tcl9.0', '_tcl_data'), ('tk9.0', '_tk_data')):
        if (library / source).is_dir():
            extra_data.append((str(library / source), destination))
a = Analysis(
    [str(tools / 'entry.py')], pathex=[str(tools)],
    binaries=extra_binaries, datas=extra_data,
    hiddenimports=['tkinter', 'matplotlib.backends.backend_tkagg', 'matplotlib.backends.backend_agg',
                   'PIL.ImageTk', 'PIL._imagingtk', 'PIL._tkinter_finder'],
    hookspath=[str(tools / 'hooks')],
    hooksconfig={'matplotlib': {'backends': ['TkAgg', 'Agg']}},
    excludes=['cupy', 'IPython', 'pytest', 'sympy.testing'],
    noarchive=False, optimize=0,
)
if runtime_dlls:
    # Prefer the runtime matching the staged compiler over an older CRT
    # collected from the build Python. The MSVC v14 runtime is backward
    # compatible; one consistent copy serves Python and compiled kernels.
    names = {path.name.casefold() for path in runtime_dlls}
    a.binaries = [entry for entry in a.binaries if entry[0].casefold() not in names]
    a.binaries.extend((path.name, str(path), 'BINARY') for path in runtime_dlls)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AeroKinetic',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=sys.platform != 'win32', disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AeroKinetic')
