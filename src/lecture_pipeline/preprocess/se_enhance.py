"""Speech Enhancement Module.

Applies spectral and formant speech enhancement to boost speech presence
and clarity for distant lecturer voices.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional
from rich.console import Console

console = Console()


def apply_speech_enhancement(
    input_wav: Path,
    output_wav: Path,
    enhance_python: Optional[Path | str] = None,
) -> Path:
    """Run speech enhancement on audio file via backends/enhance."""
    in_p = Path(input_wav).resolve()
    out_p = Path(output_wav).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if enhance_python is None:
        enhance_python = Path("backends/enhance/.venv/Scripts/python.exe").resolve()
    else:
        enhance_python = Path(enhance_python).resolve()

    runner_script = Path("backends/enhance/run_enhance.py").resolve()

    if not enhance_python.is_file() or not runner_script.is_file():
        console.print("[yellow]⚠️ 找不到 backends/enhance 執行環境，略過語音增強。[/yellow]")
        return in_p

    cmd = [
        str(enhance_python),
        str(runner_script),
        "--input", str(in_p),
        "--output", str(out_p),
        "--se",
    ]

    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if res.returncode != 0 or not out_p.is_file() or out_p.stat().st_size == 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        console.print(f"[yellow]⚠️ 語音增強執行異常 ({err_msg})，保留前一階段音訊。[/yellow]")
        return in_p

    return out_p
