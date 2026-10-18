"""Dual-layer code obfuscation and pre-build script (Layer A: AST Minification + Layer B: Cython C-Extension)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from setuptools import Extension, setup
from Cython.Build import cythonize
import python_minifier

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_DIR = PROJECT_ROOT / "src" / "lecture_pipeline"
BUILD_ROOT = PROJECT_ROOT / "build"
OBF_SRC_DIR = BUILD_ROOT / "obfuscated_src" / "lecture_pipeline"

# Modules selected for Layer B (Cython Native Compilation to .pyd)
CYTHON_MODULES = [
    Path("cleanup") / "text_cleanup.py",
    Path("bench") / "cer.py",
    Path("bench") / "matrix.py",
    Path("vad_segment.py"),
]


def step1_ensure_icon() -> None:
    """Ensure icon.ico is generated and up to date."""
    print("--- [階段 1/4] 檢查並更新 Windows 多尺寸 icon.ico ---")
    from scripts.generate_icons import generate_ico
    generate_ico(PROJECT_ROOT / "icon.png", PROJECT_ROOT / "icon.ico")


def step2_prepare_build_directory() -> None:
    """Create a clean build directory with source files."""
    print("--- [階段 2/4] 準備混淆建置目錄 (build/obfuscated_src) ---")
    if OBF_SRC_DIR.parent.is_dir():
        shutil.rmtree(OBF_SRC_DIR.parent, ignore_errors=True)
    OBF_SRC_DIR.mkdir(parents=True, exist_ok=True)

    # Copy entire lecture_pipeline package
    for item in SRC_DIR.rglob("*"):
        if item.is_file():
            rel_path = item.relative_to(SRC_DIR)
            dest_path = OBF_SRC_DIR / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_path)

    print(f"  [OK] 完整原始檔結構已同步至: {OBF_SRC_DIR}")


def step3_layer_b_cython_compilation() -> None:
    """Layer B: Compile critical algorithm modules into native Windows .pyd binaries."""
    print("--- [階段 3/4] 執行層級 B：Cython 原生二進位編譯 (.pyd) ---")
    temp_cython_build = BUILD_ROOT / "temp_cython"
    temp_cython_build.mkdir(parents=True, exist_ok=True)

    for rel_mod in CYTHON_MODULES:
        target_py = OBF_SRC_DIR / rel_mod
        if not target_py.is_file():
            print(f"  [!] 略過未找到的模組: {rel_mod}")
            continue

        mod_name = "lecture_pipeline." + str(rel_mod.with_suffix("")).replace("\\", ".").replace("/", ".")
        print(f"  [編譯中] {rel_mod} -> {mod_name} 原生機器碼...")

        ext = Extension(
            name=mod_name,
            sources=[str(target_py)],
            extra_compile_args=["/O2", "/GL"],
        )

        old_argv = sys.argv
        try:
            sys.argv = [
                "setup.py",
                "build_ext",
                "--build-lib",
                str(BUILD_ROOT / "obfuscated_src"),
                "--build-temp",
                str(temp_cython_build),
            ]
            setup(
                ext_modules=cythonize(
                    [ext],
                    compiler_directives={
                        "language_level": "3",
                        "boundscheck": False,
                        "wraparound": True,
                    },
                    quiet=True,
                )
            )
        finally:
            sys.argv = old_argv

        # Remove the original .py file so Python ONLY loads the compiled .pyd
        target_py.unlink()
        print(f"  [OK] {rel_mod} 已編譯為原生二進位並移除原始 .py")

    # Clean intermediate C and obj files
    shutil.rmtree(temp_cython_build, ignore_errors=True)
    for c_file in OBF_SRC_DIR.rglob("*.c"):
        c_file.unlink(missing_ok=True)


def step4_layer_a_ast_obfuscation() -> None:
    """Layer A: Obfuscate remaining Python files with AST minification, mangling, and docstring stripping."""
    print("--- [階段 4/4] 執行層級 A：AST 變數混淆與 Bytecode 深度精簡 ---")
    obfuscated_count = 0

    for py_file in OBF_SRC_DIR.rglob("*.py"):
        rel_path = py_file.relative_to(OBF_SRC_DIR)
        with open(py_file, "r", encoding="utf-8") as f:
            code = f.read()

        # Preserve __init__.py structure but strip comments and docstrings
        is_init = py_file.name == "__init__.py"
        try:
            obfuscated_code = python_minifier.minify(
                code,
                remove_annotations=False,
                remove_pass=True,
                remove_literal_statements=True,  # Strip all docstrings
                combine_imports=False,
                rename_locals=not is_init,  # Safely rename all local variables in non-init modules
                preserve_shebang=True,
            )
            with open(py_file, "w", encoding="utf-8") as f:
                f.write(obfuscated_code)
            obfuscated_count += 1
        except Exception as e:
            print(f"  [!] 混淆略過 (維持原文): {rel_path} ({e})")

    print(f"  [OK] 完成 {obfuscated_count} 個 Python 模組之語法樹混淆與 docstring 抹除")


def verify_obfuscated_build() -> None:
    """Verify that both .pyd and obfuscated .py files can be imported and executed seamlessly."""
    print("\n==============================================================================")
    print("  正在驗證混淆與編譯後的模組相容性 (Self-Verification)...")
    print("==============================================================================")

    test_script = f"""
import sys
from pathlib import Path
obf_root = r"{str(BUILD_ROOT / 'obfuscated_src')}"
sys.path.insert(0, obf_root)

# 1. Test Cythonized .pyd module (Layer B)
import lecture_pipeline.bench.cer as cer
sub, ins, dele, total = cer.calculate_levenshtein("機器學習", "機器學期")
assert total == 1, f"Levenshtein distance mismatch: {{total}}"
cer_res = cer.compute_cer("今天好", "今天很好")
assert cer_res.substitutions >= 0
print("[1/3 驗證成功] Layer B (.pyd 原生二進位機器碼模組):", cer.__file__)

# 2. Test Obfuscated .py module (Layer A)
import lecture_pipeline.naming as naming
parsed = naming.parse_filename("離散數學_20261015_0900.m4a")
assert parsed.course == "離散數學" and parsed.date == "20261015", "Naming parse failed"
print("[2/3 驗證成功] Layer A (AST 變數混淆與 Docstring 抹除模組):", naming.__file__)

# 3. Test GUI Bridge import
import lecture_pipeline.gui.bridge as bridge
assert hasattr(bridge, "get_courses_base_dir"), "Bridge missing functions"
print("[3/3 驗證成功] GUI 核心橋接模組載入正常")

print("\\n[SUCCESS] 雙層混淆 (AST Obfuscation + Cython Native Binary) 全數驗證通過！")
"""

    result = subprocess.run(
        [sys.executable, "-c", test_script],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )

    if result.returncode != 0:
        print("[FAIL] 驗證失敗：")
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError("Obfuscated build verification failed!")

    print(result.stdout.strip())


def main() -> None:
    step1_ensure_icon()
    step2_prepare_build_directory()
    step3_layer_b_cython_compilation()
    step4_layer_a_ast_obfuscation()
    verify_obfuscated_build()


if __name__ == "__main__":
    main()
