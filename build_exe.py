"""Comprehensive build script for LecturePipeline.
Automates:
1. Dual-layer Obfuscation & Cython Native Compilation (Layer A AST + Layer B .pyd)
2. Optimized PyInstaller Onedir Packaging (-OO, dead code excludes)
3. Inno Setup Compilation to produce standalone Windows Installer (Setup.exe)
"""
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


def find_iscc() -> Path | None:
    """Locate Inno Setup compiler executable (ISCC.exe)."""
    # 1. Check system PATH
    iscc_path = shutil.which("iscc") or shutil.which("ISCC.exe")
    if iscc_path:
        return Path(iscc_path)

    # 2. Check standard Windows installation locations
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def run_obfuscation_pipeline() -> None:
    """Run dual-layer obfuscation and native compilation."""
    print("=" * 75)
    print("🔒 [步驟 1/3] 執行雙層混合混淆構建 (Cython Native .pyd + AST 變數混淆)...")
    print("=" * 75)
    obf_script = ROOT / "scripts" / "obfuscate_pipeline.py"
    res = subprocess.run([sys.executable, str(obf_script)], cwd=str(ROOT))
    if res.returncode != 0:
        raise RuntimeError("混淆與二進位原生編譯失敗，中止打包！")


def run_pyinstaller_packaging() -> Path:
    """Run PyInstaller with optimized settings over obfuscated source code."""
    print("\n" + "=" * 75)
    print("📦 [步驟 2/3] 執行 PyInstaller 最佳化打包 (移除無用庫 + Bytecode 最佳化)...")
    print("=" * 75)

    obf_root = ROOT / "build" / "obfuscated_src"
    main_py = obf_root / "main.py"
    ico_path = ROOT / "icon.ico"
    dist_dir = ROOT / "dist" / "LecturePipeline"
    work_dir = ROOT / "build" / "pyinstaller_work"

    templates_dir = ROOT / "src" / "lecture_pipeline" / "gui" / "templates"
    static_dir = ROOT / "src" / "lecture_pipeline" / "gui" / "static"
    prompts_dir = ROOT / "prompts" / "library"

    add_data_args = [
        f"--add-data={templates_dir};lecture_pipeline/gui/templates",
        f"--add-data={static_dir};lecture_pipeline/gui/static",
        f"--add-data={prompts_dir};prompts/library",
        f"--add-data={ROOT / 'icon.png'};.",
        f"--add-data={ico_path};.",
    ]

    # Collect compiled Cython .pyd binaries into distribution
    add_binary_args = []
    for pyd_file in obf_root.rglob("*.pyd"):
        rel_parent = pyd_file.parent.relative_to(obf_root)
        dest_folder = str(rel_parent).replace("/", "\\")
        add_binary_args.append(f"--add-binary={pyd_file};{dest_folder}")
        print(f"  [二進位納入] {pyd_file.name} -> {dest_folder}")

    # Exclude unused heavy libraries to reduce package footprint
    exclude_args = [
        "--exclude-module=tkinter",
        "--exclude-module=tcl",
        "--exclude-module=tk",
        "--exclude-module=pytest",
        "--exclude-module=unittest",
        "--exclude-module=scipy",
        "--exclude-module=matplotlib",
        "--exclude-module=IPython",
        "--exclude-module=notebook",
        "--exclude-module=PIL.ImageQt",
        "--exclude-module=pygments",
        "--exclude-module=cython",
        "--exclude-module=setuptools",
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

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--optimize=2",  # Strip assert statements and docstrings
        f"--icon={ico_path}",
        "--name=LecturePipeline",
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={work_dir}",
        f"--paths={obf_root}",
        *add_data_args,
        *add_binary_args,
        *exclude_args,
        *hidden_imports,
        str(main_py),
    ]

    env = os.environ.copy()
    env["PYTHONOPTIMIZE"] = "2"
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)

    # Post-packaging setup: copy essential config and empty partition dirs
    print("\n準備發布執行目錄配置...")
    shutil.copy2(ROOT / "config.yaml", dist_dir / "config.yaml")

    courses_src = ROOT / "courses"
    courses_dst = dist_dir / "courses"
    if courses_src.is_dir() and not courses_dst.is_dir():
        shutil.copytree(courses_src, courses_dst)

    prompts_dst = dist_dir / "prompts"
    if (ROOT / "prompts").is_dir() and not prompts_dst.is_dir():
        shutil.copytree(ROOT / "prompts", prompts_dst)

    for folder in ["inbox", "exports", "archive"]:
        (dist_dir / folder).mkdir(exist_ok=True)

    print(f"✔ PyInstaller 打包完成！執行檔位於: {dist_dir / 'LecturePipeline.exe'}")
    return dist_dir


def run_inno_setup_compiler() -> Path | None:
    """Compile Inno Setup script to generate Setup_LecturePipeline_v0.1.0.exe."""
    print("\n" + "=" * 75)
    print("🛠️  [步驟 3/3] 執行 Inno Setup 編譯標準 Windows 安裝導引精靈...")
    print("=" * 75)

    iscc_bin = find_iscc()
    if not iscc_bin:
        print("⚠️ 未檢測到 Inno Setup 編譯器 (ISCC.exe)。請確認已安裝 Inno Setup 6。")
        return None

    iss_file = ROOT / "installer.iss"
    if not iss_file.is_file():
        raise FileNotFoundError(f"找不到安裝腳本: {iss_file}")

    print(f"  使用編譯器: {iscc_bin}")
    print(f"  編譯腳本: {iss_file}")

    cmd = [str(iscc_bin), str(iss_file)]
    subprocess.run(cmd, check=True, cwd=str(ROOT))

    installer_exe = ROOT / "dist" / "Setup_LecturePipeline_v0.1.0.exe"
    if installer_exe.is_file():
        size_mb = installer_exe.stat().st_size / (1024 * 1024)
        print(f"\n🎉 安裝程式建置成功！")
        print(f"  安裝檔路徑: {installer_exe}")
        print(f"  安裝檔大小: {size_mb:.2f} MB")
        return installer_exe
    return None


def main() -> None:
    run_obfuscation_pipeline()
    run_pyinstaller_packaging()
    run_inno_setup_compiler()
    print("\n" + "=" * 75)
    print("✅ 全部構建作業已圓滿完成！")
    print("=" * 75)


if __name__ == "__main__":
    main()
