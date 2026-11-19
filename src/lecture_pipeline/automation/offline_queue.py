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

        console.print(f"[yellow]離線佇列：{display_name} 已加入待上傳佇列，等待網路連線...[/yellow]")
        notify_offline_pending(display_name)

    def get_pending(self) -> List[Dict[str, Any]]:
        """Return all items currently waiting in queue."""
        return self._load_queue()

    def process_pending_uploads(self, publisher: Any) -> int:
        """Process pending uploads if internet is connected.

        Uses non-interactive authentication in the background queue loop so that
        expired/missing OAuth tokens never block inbox monitoring for 30 minutes per item.

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

        from lecture_pipeline.export.drive_publisher import (
            GoogleAuthRequiredError,
            GoogleDrivePublisher,
        )

        # Ensure background queue drains never spawn blocking browser OAuth prompts
        restore_interactive = None
        if isinstance(publisher, GoogleDrivePublisher):
            restore_interactive = publisher.interactive
            publisher.interactive = False
            try:
                publisher.get_service(interactive=False)
            except (GoogleAuthRequiredError, FileNotFoundError, TimeoutError) as auth_err:
                console.print(f"[yellow][WARN] 暫緩自動補傳佇列 ({len(queue)} 筆)：{auth_err}[/yellow]")
                return 0
            except Exception as auth_err:
                console.print(f"[yellow][WARN] Google Drive 連線預檢失敗：{auth_err}[/yellow]")
                return 0

        remaining: List[Dict[str, Any]] = []
        uploaded_count = 0

        console.print(f"\n[cyan]網路已恢復，開始處理待上傳佇列 ({len(queue)} 筆)...[/cyan]")

        try:
            for idx, item in enumerate(queue):
                w_dir = Path(item["work_dir"])
                display_name = item.get("display_name", w_dir.name)
                if not w_dir.is_dir():
                    # Directory removed, omit
                    continue

                try:
                    pub_res = publisher.publish_lecture(w_dir)
                    docs_list = getattr(pub_res, "docs", None)
                    first_doc_link = (
                        docs_list[0].web_link
                        if isinstance(docs_list, list) and docs_list and getattr(docs_list[0], "web_link", None)
                        else None
                    )
                    doc_link = (
                        first_doc_link
                        or getattr(pub_res, "doc_link", None)
                        or getattr(pub_res, "course_folder_link", None)
                    )
                    folder_link = getattr(pub_res, "course_folder_link", None) or doc_link
                    console.print(f"[green][OK] 佇列項目上傳成功：{display_name}[/green]")
                    try:
                        from lecture_pipeline.automation.watcher import log_watcher_event
                        log_watcher_event(
                            f"已同步至 Google Drive [{item.get('course', '')} | {item.get('date', '')}]：{folder_link}"
                            + (f" (文件：{doc_link})" if doc_link and doc_link != folder_link else "")
                        )
                    except Exception:
                        pass
                    notify_upload_success(display_name, doc_url=doc_link)
                    uploaded_count += 1
                except (GoogleAuthRequiredError, TimeoutError) as auth_err:
                    console.print(
                        f"[yellow][WARN] Google 授權中斷，保留剩餘 {len(queue) - idx} 筆待補傳 ({auth_err})。[/yellow]"
                    )
                    remaining.extend(queue[idx:])
                    break
                except Exception as e:
                    console.print(f"[yellow][WARN] 上傳重試失敗 ({display_name}): {e}，保留於佇列。[/yellow]")
                    remaining.append(item)
        finally:
            if restore_interactive is not None and isinstance(publisher, GoogleDrivePublisher):
                publisher.interactive = restore_interactive

        self._save_queue(remaining)
        return uploaded_count
