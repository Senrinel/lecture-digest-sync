# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/LinKJ/Desktop/ASR系統/src/lecture_pipeline/gui/templates', 'lecture_pipeline/gui/templates'), ('C:/Users/LinKJ/Desktop/ASR系統/src/lecture_pipeline/gui/static', 'lecture_pipeline/gui/static'), ('C:/Users/LinKJ/Desktop/ASR系統/prompts/library', 'prompts/library'), ('C:/Users/LinKJ/Desktop/ASR系統/icon.png', '.'), ('C:/Users/LinKJ/Desktop/ASR系統/icon.ico', '.')]
binaries = []
hiddenimports = ['uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium', 'win11toast', 'pystray', 'pystray._win32', 'PIL', 'PIL.Image', 'opencc', 'jinja2', 'pydantic', 'yaml', 'google.genai', 'watchdog', 'watchdog.observers', 'watchdog.observers.winapi', 'watchdog.events', 'pypdf', 'rich', 'typer']
tmp_ret = collect_all('opencc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('win11toast')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/LinKJ/Desktop/ASR系統/main.py'],
    pathex=['C:/Users/LinKJ/Desktop/ASR系統/src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LecturePipeline',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Users/LinKJ/Desktop/ASR系統/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LecturePipeline',
)
