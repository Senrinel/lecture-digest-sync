"""Inbox folder watcher module.

Monitors the inbox/ folder. When a lecture audio file (.wav, .mp3, .m4a, .flac) is added:
1. Waits for file transfer to stabilize (size check).
2. Invokes orchestrator to run the complete pipeline.
3. Moves completed audio from inbox/ to archive/<Course>/<Date>/.
4. Sends Windows desktop notification on completion or failure.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from rich.console import Console

from lecture_pipeline.orchestrator import LecturePipelineOrchestrator
from lecture_pipeline.naming import parse_audio_filename

console = Console()
SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".wma"}


def send_windows_notification(title: str, message: str):
    """Sends a native Windows toast notification via PowerShell."""
    try:
        clean_title = title.replace('"', '`"')
        clean_msg = message.replace('"', '`"')
        ps_cmd = (
            f"[void] [System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
            f"$notify = New-Object System.Windows.Forms.NotifyIcon;"
            f"$notify.Icon = [System.Drawing.SystemIcons]::Information;"
            f"$notify.Visible = $true;"
            f"$notify.ShowBalloonTip(5000, '{clean_title}', '{clean_msg}', [System.Windows.Forms.ToolTipIcon]::Info);"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
    except Exception:
        pass


class AudioInboxHandler(FileSystemEventHandler):
    def __init__(self, orchestrator: LecturePipelineOrchestrator, archive_dir: Path, target_stage: str = "summarize"):
        super().__init__()
        self.orchestrator = orchestrator
        self.archive_dir = archive_dir
        self.target_stage = target_stage
        self.processing_files = set()

    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() in SUPPORTED_EXTENSIONS:
            self._handle_new_audio(p)

    def on_moved(self, event):
        if event.is_directory:
            return
        p = Path(event.dest_path)
        if p.suffix.lower() in SUPPORTED_EXTENSIONS:
            self._handle_new_audio(p)

    def _wait_for_file_stability(self, path: Path, timeout_sec: int = 60) -> bool:
        """Waits until the file size stops changing (ensures file copy/write is finished)."""
        start_time = time.time()
        last_size = -1
        stable_count = 0

        while time.time() - start_time < timeout_sec:
            try:
                curr_size = path.stat().st_size
                if curr_size > 0 and curr_size == last_size:
                    stable_count += 1
                    if stable_count >= 2:
                        return True
                else:
                    stable_count = 0
                last_size = curr_size
            except Exception:
                pass
            time.sleep(1.0)
        return False

    def _handle_new_audio(self, path: Path):
        canonical_path = str(path.resolve())
        if canonical_path in self.processing_files:
            return
        self.processing_files.add(canonical_path)

        console.print(f"\n[bold yellow]📥 偵測到新音檔放入 inbox：{path.name}[/bold yellow]")

        if not self._wait_for_file_stability(path):
            console.print(f"[red]❌ 檔案寫入超時或未就緒：{path.name}[/red]")
            self.processing_files.remove(canonical_path)
            return

        try:
            # Process file
            result_path = self.orchestrator.process_file(
                audio_path=path,
                target_stage=self.target_stage,
            )

            # Move to archive/<Course>/<Date>/
            info = parse_audio_filename(path)
            dest_folder = self.archive_dir / info.course_name / info.date_str
            dest_folder.mkdir(parents=True, exist_ok=True)
            dest_file = dest_folder / path.name

            shutil.move(str(path), str(dest_file))
            console.print(f"📦 原始音檔已封存至：{dest_file}")

            send_windows_notification(
                title=f"課堂錄音處理完成：{info.course_name}",
                message=f"已成功完成 {info.course_name} ({info.date_str}) 逐字稿與筆記！",
            )
        except Exception as e:
            console.print(f"[bold red]❌ 處理失敗：{e}[/bold red]")
            send_windows_notification(
                title=f"課堂錄音處理失敗：{path.name}",
                message=str(e)[:100],
            )
        finally:
            self.processing_files.discard(canonical_path)


def start_watcher(config_path: str | Path = "config.yaml", target_stage: str = "summarize"):
    """Starts monitoring inbox/ directory continuously."""
    orchestrator = LecturePipelineOrchestrator(config_path=config_path)
    inbox_dir = Path(orchestrator.config["paths"]["inbox_dir"]).resolve()
    archive_dir = Path(orchestrator.config["paths"]["archive_dir"]).resolve()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    event_handler = AudioInboxHandler(
        orchestrator=orchestrator,
        archive_dir=archive_dir,
        target_stage=target_stage,
    )
    observer = Observer()
    observer.schedule(event_handler, str(inbox_dir), recursive=False)
    observer.start()

    console.print(f"\n[bold green]👀 資料夾監看服務已啟動！[/bold green]")
    console.print(f"請將錄音檔案放入：[bold yellow]{inbox_dir}[/bold yellow]")
    console.print("系統將自動進行「前處理 → VAD切段 → 語音辨識 → 內文整理 → 重點筆記」全自動流程。")
    console.print("[dim]按 Ctrl+C 可停止監看服務...[/dim]\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
