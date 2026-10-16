"""Offline network resilience and pending upload queue management.

Handles:
1. Internet connectivity probing.
2. Queuing finished lecture documents when offline.
3. Automated background resumption of pending uploads once internet returns.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any, Dict, List
from rich.console import Console

from lecture_pipeline.automation.notifier import notify_offline_pending, notify_upload_success

console = Console(legacy_windows=False)

DEFAULT_QUEUE_DIR = Path("pending_uploads")


def is_internet_connected(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Check whether internet connectivity is currently available.

    Probes a reliable DNS server (8.8.8.8) using a raw socket with 2s timeout.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except (OSError, socket.error):
        return False


class OfflineUploadQueue:
    """Manages queue of lectures processed offline awaiting cloud synchronization."""

    def __init__(self, queue_dir: Path | str = DEFAULT_QUEUE_DIR):
        self.queue_dir = Path(queue_dir).resolve()
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.queue_file = self.queue_dir / "queue.json"

    def _load_queue(self) -> List[Dict[str, Any]]:
        if not self.queue_file.is_file():
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_queue(self, queue: List[Dict[str, Any]]) -> None:
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)

    def enqueue(
        self,
        work_dir: Path | str,
        display_name: str,
        course: str,
        date: str,
    ) -> None:
        """Add completed lecture to offline upload queue and trigger desktop alert."""
        w_dir = Path(work_dir).resolve()
        queue = self._load_queue()

        # Prevent duplicate entries
        for item in queue:
            if item.get("work_dir") == str(w_dir):
                return

        item = {
            "work_dir": str(w_dir),
            "display_name": display_name,
            "course": course,
            "date": date,
            "enqueued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        queue.append(item)
        self._save_queue(queue)

        console.print(f"[yellow]📶 離線排隊：{display_name} 已加入待上傳佇列，等待連線...[/yellow]")
        notify_offline_pending(display_name)

    def get_pending(self) -> List[Dict[str, Any]]:
        """Return all items currently waiting in queue."""
        return self._load_queue()

    def process_pending_uploads(self, publisher: Any) -> int:
        """Process pending uploads if internet is connected.

        Args:
            publisher: GoogleDrivePublisher instance.

        Returns:
            Number of successfully uploaded items.
        """
        if not is_internet_connected():
            return 0

        queue = self._load_queue()
        if not queue:
            return 0

        remaining: List[Dict[str, Any]] = []
        uploaded_count = 0

        console.print(f"\n[cyan]🌐 偵測到網路已恢復，開始處理待上傳佇列 ({len(queue)} 筆)...[/cyan]")

        for item in queue:
            w_dir = Path(item["work_dir"])
            display_name = item.get("display_name", w_dir.name)
            if not w_dir.is_dir():
                # Directory removed, omit
                continue

            try:
                pub_res = publisher.publish_lecture(w_dir)
                doc_link = pub_res.doc_link if hasattr(pub_res, "doc_link") else None
                console.print(f"[green]✔ 佇列項目上傳成功：{display_name}[/green]")
                notify_upload_success(display_name, doc_url=doc_link)
                uploaded_count += 1
            except Exception as e:
                console.print(f"[yellow]⚠️ 上傳重試失敗 ({display_name}): {e}，保留於佇列。[/yellow]")
                remaining.append(item)

        self._save_queue(remaining)
        return uploaded_count
