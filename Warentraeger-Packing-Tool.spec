# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'IPython', 'ipykernel', 'jupyter_client', 'jupyter_core', 'jedi', 'parso', 'plotly', 'narwhals', 'matplotlib_inline', 'prompt_toolkit', 'pygments', 'zmq', 'tornado', 'traitlets', 'stack_data', 'pure_eval', 'asttokens', 'executing', 'comm', 'debugpy', 'psutil', 'nest_asyncio', 'wcwidth', 'xlsxwriter', 'rectpack', 'ipython_pygments_lexers'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Warentraeger-Packing-Tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
