"""Audio compression, intermediate pruning, and long-term lecture archival.

Retains only the 4 essential artifacts in long-term storage:
1. High-compression audio (Opus @ 32kbps or AAC)
2. Full cleaned transcript (02_cleaned.txt)
3. Key-point lecture notes (03_summary.md)
4. Full execution log (run.json)

Deletes intermediate heavy artifacts:
- chunks/ directory (dozens of temporary WAV splits)
- 00_normalized.wav (uncompressed PCM WAV)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional
import yaml
from rich.console import Console

console = Console(legacy_windows=False)

DEFAULT_RECORD_DIR = Path(r"C:\Users\LinKJ\Pictures\Record")


def get_record_archive_base_dir(config_path: Path | str = "config.yaml") -> Path:
    """Retrieve configured long-term archive directory for lecture records."""
    env_dir = os.environ.get("RECORD_ARCHIVE_DIR")
    if env_dir:
        return Path(env_dir).resolve()

    p = Path(config_path)
    if p.is_file():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            custom_dir = data.get("paths", {}).get("record_archive_dir")
            if custom_dir:
                return Path(custom_dir).resolve()
        except Exception:
            pass

    return DEFAULT_RECORD_DIR.resolve()


def compress_audio_to_opus(
    source_wav: Path | str,
    output_path: Path | str,
    bitrate: str = "32k",
) -> Path:
    """Compress WAV audio into ultra-compact speech Opus format (or AAC fallback)."""
    src = Path(source_wav).resolve()
    dst = Path(output_path).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not src.is_file():
        raise FileNotFoundError(f"Source audio file not found: {src}")

    # 1. Attempt libopus (highest efficiency for speech)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-c:a",
        "libopus",
        "-b:a",
        bitrate,
        "-v",
        "error",
        str(dst),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
        return dst

    # 2. Fallback to AAC if libopus fails
    aac_dst = dst.with_suffix(".m4a")
    cmd_fallback = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-c:a",
        "aac",
        "-b:a",
        "48k",
        "-v",
        "error",
        str(aac_dst),
    ]
    res_fallback = subprocess.run(cmd_fallback, capture_output=True, text=True)
    if res_fallback.returncode == 0 and aac_dst.is_file() and aac_dst.stat().st_size > 0:
        return aac_dst

    raise RuntimeError(
        f"Audio compression failed with FFmpeg: {res.stderr or res_fallback.stderr}"
    )


def prune_and_archive(
    work_dir: Path | str,
    course: str,
    date: str,
    record_base_dir: Optional[Path | str] = None,
    project_logs_dir: Optional[Path | str] = None,
    delete_chunks: bool = True,
    delete_norm_wav: bool = True,
) -> Dict[str, Path]:
    """Execute lifecycle pruning and long-term archival.

    1. Encodes 00_normalized.wav -> high compression Opus/AAC audio.
    2. Copies 4 pillars to destination:
       - Compressed audio
       - 02_cleaned.txt (full cleaned lecture text)
       - 03_summary.md (structured review notes)
       - run.json (detailed pipeline execution log)
    3. Prunes intermediate bulky products (chunks/ & 00_normalized.wav).

    Returns:
        Dict mapping artifact keys to their paths in long-term archive.
    """
    w_dir = Path(work_dir).resolve()
    base_dest = (
        Path(record_base_dir).resolve()
        if record_base_dir
        else get_record_archive_base_dir()
    )
    dest_dir = base_dest / course / date
    dest_dir.mkdir(parents=True, exist_ok=True)

    archived_files: Dict[str, Path] = {}

    # 1. Compress normalized WAV
    norm_wav = w_dir / "00_normalized.wav"
    compressed_audio_dest = dest_dir / f"{course}_{date}_audio.opus"

    if norm_wav.is_file():
        try:
            actual_compressed = compress_audio_to_opus(norm_wav, compressed_audio_dest)
            archived_files["audio"] = actual_compressed
            console.print(
                f"[green]✔ 高壓縮音檔封存成功: {actual_compressed.name} "
                f"({actual_compressed.stat().st_size // 1024} KB)[/green]"
            )
        except Exception as e:
            console.print(f"[yellow]⚠️ 音檔壓縮失敗，保留原始檔封存: {e}[/yellow]")
            fallback_dest = dest_dir / norm_wav.name
            shutil.copy2(str(norm_wav), str(fallback_dest))
            archived_files["audio"] = fallback_dest

    # 2. Archive cleaned lecture transcript
    cleaned_txt = w_dir / "02_cleaned.txt"
    if cleaned_txt.is_file():
        dest_txt = dest_dir / "02_cleaned.txt"
        shutil.copy2(str(cleaned_txt), str(dest_txt))
        archived_files["cleaned_transcript"] = dest_txt

    # 3. Archive summary notes
    summary_md = w_dir / "03_summary.md"
    if summary_md.is_file():
        dest_md = dest_dir / "03_summary.md"
        shutil.copy2(str(summary_md), str(dest_md))
        archived_files["summary_notes"] = dest_md

    # 3.5 Generate and archive interactive audio-synced HTML player
    try:
        from lecture_pipeline.export.html_player import generate_interactive_player
        audio_name = compressed_audio_dest.name if "audio" in archived_files else "00_normalized.wav"
        player_html = generate_interactive_player(
            work_dir=w_dir,
            course=course,
            date=date,
            audio_filename=audio_name,
        )
        dest_player = dest_dir / player_html.name
        shutil.copy2(str(player_html), str(dest_player))
        archived_files["player_html"] = dest_player
        console.print(f"[green]✔ 互動同步播放器生成完畢: {dest_player.name}[/green]")
    except Exception as e:
        console.print(f"[dim]⚠️ 播放器生成略過: {e}[/dim]")

    # 4. Archive run execution log into project logs directory (專案 logs 資料夾)
    run_log = w_dir / "run.json"
    if run_log.is_file():
        proj_logs_base = (
            Path(project_logs_dir).resolve()
            if project_logs_dir
            else Path("logs") / "runs"
        )
        proj_log_dir = proj_logs_base / course / date
        proj_log_dir.mkdir(parents=True, exist_ok=True)
        dest_log = proj_log_dir / "run.json"
        shutil.copy2(str(run_log), str(dest_log))
        archived_files["run_log"] = dest_log

        # Ensure Record directory remains clean of tech logs
        old_record_log = dest_dir / "run.json"
        if old_record_log.is_file():
            try:
                old_record_log.unlink()
            except Exception:
                pass

    # 5. Prune intermediate heavy waste
    if delete_chunks:
        chunks_dir = w_dir / "chunks"
        if chunks_dir.is_dir():
            try:
                shutil.rmtree(str(chunks_dir))
                console.print(f"[dim]🗑️ 已修剪暫存目錄: {chunks_dir.name}[/dim]")
            except Exception as e:
                console.print(f"[dim]⚠️ 暫存目錄清理警告: {e}[/dim]")

    if delete_norm_wav:
        if norm_wav.is_file():
            try:
                norm_wav.unlink()
                console.print(f"[dim]🗑️ 已修剪未壓縮原始檔: {norm_wav.name}[/dim]")
            except Exception as e:
                console.print(f"[dim]⚠️ 原始檔清理警告: {e}[/dim]")

    console.print(
        f"[bold green]📦 課堂長期封存完成：{dest_dir} (保留壓縮音檔、文稿、筆記與 log)[/bold green]"
    )
    return archived_files
