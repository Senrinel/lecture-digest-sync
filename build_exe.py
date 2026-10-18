"""Build script to create standalone Windows distribution using PyInstaller."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if sys.stderr is not None:
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent

def build():
    print("=" * 70)
    print("🚀 開始建置 LecturePipeline 獨立執行檔 (PyInstaller)...")
    print("=" * 70)

    # 1. Ensure icon.ico exists
    ico_path = ROOT / "icon.ico"
    if not ico_path.is_file():
        from PIL import Image
        img = Image.open(ROOT / "icon.png")
        img.save(ico_path, format="ICO", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
        print("✔ icon.ico 已建立。")

    # 2. Prepare PyInstaller command
    # Collect data files
    templates_dir = ROOT / "src" / "lecture_pipeline" / "gui" / "templates"
    static_dir = ROOT / "src" / "lecture_pipeline" / "gui" / "static"
    prompts_dir = ROOT / "prompts" / "library"

    add_data_args = [
        f"--add-data={templates_dir};lecture_pipeline/gui/templates",
        f"--add-data={static_dir};lecture_pipeline/gui/static",
        f"--add-data={prompts_dir};prompts/library",
        f"--add-data={ROOT / 'icon.png'};.",
        f"--add-data={ROOT / 'icon.ico'};.",
    ]

    hidden_imports = [
        "--hidden-import=uvicorn",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=webview",
        "--hidden-import=webview.platforms.winforms",
        "--hidden-import=webview.platforms.edgechromium",
        "--hidden-import=win11toast",
        "--hidden-import=pystray",
        "--hidden-import=pystray._win32",
        "--hidden-import=PIL",
        "--hidden-import=PIL.Image",
        "--hidden-import=opencc",
        "--hidden-import=jinja2",
        "--hidden-import=pydantic",
        "--hidden-import=yaml",
        "--hidden-import=google.genai",
        "--hidden-import=watchdog",
        "--hidden-import=watchdog.observers",
        "--hidden-import=watchdog.observers.winapi",
        "--hidden-import=watchdog.events",
        "--hidden-import=pypdf",
        "--hidden-import=rich",
        "--hidden-import=typer",
        "--collect-all=opencc",
        "--collect-all=webview",
        "--collect-all=win11toast",
        "--collect-all=pystray",
    ]

    dist_dir = ROOT / "dist" / "LecturePipeline"
    work_dir = ROOT / "build" / "pyinstaller_work"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed", # GUI window, no default black console pop-up
        f"--icon={ico_path}",
        "--name=LecturePipeline",
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={work_dir}",
        f"--paths={ROOT / 'src'}",
        *add_data_args,
        *hidden_imports,
        str(ROOT / "main.py"),
    ]

    print("執行 PyInstaller 指令...")
    subprocess.run(cmd, check=True)
    print("\n✔ PyInstaller 建置完成！輸出目錄：", dist_dir)

    # Copy default config and sample course directories to dist
    print("\n複製預設設定檔與範本資料夾至 dist/LecturePipeline...")
    shutil.copy2(ROOT / "config.yaml", dist_dir / "config.yaml")
    
    courses_src = ROOT / "courses"
    courses_dst = dist_dir / "courses"
    if courses_src.is_dir() and not courses_dst.is_dir():
        shutil.copytree(courses_src, courses_dst)

    prompts_dst = dist_dir / "prompts"
    if (ROOT / "prompts").is_dir() and not prompts_dst.is_dir():
        shutil.copytree(ROOT / "prompts", prompts_dst)

    # Create empty inbox, exports, archive directories
    for folder in ["inbox", "exports", "archive"]:
        (dist_dir / folder).mkdir(exist_ok=True)

    print("✔ 輸出目錄準備就緒：", dist_dir)

if __name__ == "__main__":
    build()
