"""Quota management and fallback policies for cloud API limits."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.console import Console

console = Console()


@dataclass
class DeferredTask:
    """Task deferred due to cloud API quota exhaustion."""

    task_type: str  # 'cleanup' or 'summary'
    work_dir: str
    course: str
    date: str
    created_at: str
    parameters: Dict[str, Any]


class QuotaManager:
    """Manages cloud quota policies ('local_then_defer', 'wait', 'local_all')."""

    def __init__(
        self,
        policy: str = "local_then_defer",
        local_fallback_channel: str = "C7",
        max_wait_sec: int = 300,
    ):
        self.policy = policy.lower().strip()
        self.local_fallback_channel = local_fallback_channel
        self.max_wait_sec = max_wait_sec

    def handle_stt_quota_error(self, requested_channel: str) -> str:
        """Decide what to do when cloud STT hits 429 quota exhaustion.

        Returns:
            The fallback STT channel ID to switch to.
        """
        if self.policy in ["local_then_defer", "local_all"]:
            console.print(
                f"[yellow]⚠️ 雲端通道 {requested_channel} 配額已耗盡，"
                f"依策略自動切換至本地高效能 GPU 通道 {self.local_fallback_channel}...[/yellow]"
            )
            return self.local_fallback_channel
        elif self.policy == "wait":
            console.print(f"[yellow]⚠️ 雲端配額已滿，等待 60 秒後重試...[/yellow]")
            time.sleep(min(60, self.max_wait_sec))
            return requested_channel
        return self.local_fallback_channel

    def defer_task(
        self,
        task_type: str,
        work_dir: Path | str,
        course: str,
        date: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Enqueue task into work_dir/deferred_tasks.json for off-peak retry."""
        target = Path(work_dir).resolve()
        deferred_file = target / "deferred_tasks.json"

        tasks: List[Dict[str, Any]] = []
        if deferred_file.is_file():
            try:
                with open(deferred_file, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
            except Exception:
                tasks = []

        new_task = {
            "task_type": task_type,
            "work_dir": str(target),
            "course": course,
            "date": date,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "parameters": params or {},
        }
        tasks.append(new_task)

        with open(deferred_file, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)

        console.print(
            f"[yellow]📋 已將 {task_type} 任務寫入待辦佇列：{deferred_file.name} "
            f"(待每日額度重置後補跑)[/yellow]"
        )
        return deferred_file
