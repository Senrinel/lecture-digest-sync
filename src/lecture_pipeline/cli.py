"""CLI entrypoint for the Lecture Pipeline."""

from pathlib import Path
from typing import Optional
import typer
from rich.console import Console

from lecture_pipeline.orchestrator import LecturePipelineOrchestrator
from lecture_pipeline.preprocess import PreprocessingOptions

app = typer.Typer(help="大學課堂錄音自動化 ASR 系統 CLI", no_args_is_help=True)
console = Console()


@app.command()
def process(
    audio_path: Path = typer.Argument(..., help="欲處理的課堂錄音音檔路徑 (.wav / .mp3)"),
    channel: str = typer.Option("C1", "--channel", "-c", help="使用的 STT 通道 (C1: Gemini Flash, C2: Gemini Transcribe)"),
    hp: str = typer.Option("100Hz", "--hp", help="高通濾波開關 (off | 80Hz | 100Hz | 120Hz)"),
    loud: str = typer.Option("-19LUFS", "--loud", help="響度正規化開關 (off | -16LUFS | -19LUFS | -23LUFS)"),
    dyn: str = typer.Option("off", "--dyn", help="動態音量平衡開關 (off | light | standard)"),
    nr: str = typer.Option("off", "--nr", help="頻譜底噪抑制開關 (off | 6dB | 12dB | 18dB)"),
    config: Path = typer.Option(Path("config.yaml"), "--config", help="設定檔路徑"),
):
    """處理單個課堂錄音音檔 (前處理 -> 切段 -> 轉錄 01_raw.txt)。"""
    opts = PreprocessingOptions(hp=hp, loud=loud, dyn=dyn, nr=nr)
    orchestrator = LecturePipelineOrchestrator(config_path=config)
    orchestrator.process_file(audio_path=audio_path, channel_id=channel, custom_pre_options=opts)


@app.command()
def probe():
    """執行 Gemini 模型連線與可用性探測。"""
    import subprocess
    import sys
    subprocess.run([sys.executable, "scripts/probe_gemini.py"])


if __name__ == "__main__":
    app()
