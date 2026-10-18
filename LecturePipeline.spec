# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/LinKJ/Desktop/ASR系統/src/lecture_pipeline/gui/templates', 'lecture_pipeline/gui/templates'), ('C:/Users/LinKJ/Desktop/ASR系統/src/lecture_pipeline/gui/static', 'lecture_pipeline/gui/static'), ('C:/Users/LinKJ/Desktop/ASR系統/prompts/library', 'prompts/library'), ('C:/Users/LinKJ/Desktop/ASR系統/icon.png', '.'), ('C:/Users/LinKJ/Desktop/ASR系統/icon.ico', '.')]
binaries = [('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/glossary.cp312-win_amd64.pyd', 'lecture_pipeline'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/glossary.pyd', 'lecture_pipeline'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/naming.cp312-win_amd64.pyd', 'lecture_pipeline'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/naming.pyd', 'lecture_pipeline'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/vad_segment.cp312-win_amd64.pyd', 'lecture_pipeline'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/vad_segment.pyd', 'lecture_pipeline'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/automation/pruner.cp312-win_amd64.pyd', 'lecture_pipeline/automation'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/automation/pruner.pyd', 'lecture_pipeline/automation'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/automation/quota.cp312-win_amd64.pyd', 'lecture_pipeline/automation'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/automation/quota.pyd', 'lecture_pipeline/automation'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/bench/cer.cp312-win_amd64.pyd', 'lecture_pipeline/bench'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/bench/cer.pyd', 'lecture_pipeline/bench'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/bench/matrix.cp312-win_amd64.pyd', 'lecture_pipeline/bench'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/bench/matrix.pyd', 'lecture_pipeline/bench'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/cleanup/text_cleanup.cp312-win_amd64.pyd', 'lecture_pipeline/cleanup'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/cleanup/text_cleanup.pyd', 'lecture_pipeline/cleanup'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/export/html_player.cp312-win_amd64.pyd', 'lecture_pipeline/export'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/export/html_player.pyd', 'lecture_pipeline/export'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/export/notebook_bundle.cp312-win_amd64.pyd', 'lecture_pipeline/export'), ('C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/lecture_pipeline/export/notebook_bundle.pyd', 'lecture_pipeline/export')]
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
    ['C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src/main.py'],
    pathex=['C:/Users/LinKJ/Desktop/ASR系統/build/obfuscated_src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'tcl', 'tk', 'pytest', 'unittest', 'scipy', 'matplotlib', 'IPython', 'notebook', 'PIL.ImageQt', 'pygments', 'cython'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [('O', None, 'OPTION'), ('O', None, 'OPTION')],
    exclude_binaries=True,
    name='LecturePipeline',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='LecturePipeline',
)
