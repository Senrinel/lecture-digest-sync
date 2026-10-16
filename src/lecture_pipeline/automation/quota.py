"""Quota management, next-day deferral, and smart cloud/local routing."""

from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from rich.console import Console

console = Console(legacy_windows=False)

DEFAULT_LOCK_FILE = Path("logs/quota_lock.json")


@dataclass
class DeferredTask:
    """Task deferred due to cloud API quota exhaustion."""

    task_type: str  # 'stt', 'cleanup', or 'summary'
    work_dir: str
    course: str
    date: str
    created_at: str
    resume_after: str
    parameters: Dict[str, Any]


class QuotaManager:
    """Manages cloud quota policies, daily limit tracking, and next-day deferral."""

    def __init__(
        self,
        policy: str = "local_then_defer",
        local_fallback_channel: str = "C7",
        daily_reset_hour_utc: int = 8,  # 08:00 UTC = 16:00 Taiwan time
        lock_file: Path | str = DEFAULT_LOCK_FILE,
    ):
        self.policy = policy.lower().strip()
        self.local_fallback_channel = local_fallback_channel
        self.daily_reset_hour_utc = daily_reset_hour_utc
        self.lock_file = Path(lock_file).resolve()

    def calculate_next_reset_datetime(self) -> datetime.datetime:
        """Calculate next Google API daily quota reset time (UTC 08:00 / TW 16:00)."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        target = now_utc.replace(
            hour=self.daily_reset_hour_utc,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now_utc >= target:
            target += datetime.timedelta(days=1)
        return target

    def mark_quota_exhausted(self, service: str = "gemini", reason: str = "429 Quota Exceeded") -> datetime.datetime:
        """Lock cloud calls and defer until next daily quota reset."""
        reset_time = self.calculate_next_reset_datetime()
        reset_iso = reset_time.isoformat()

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_data = {
            "service": service,
            "locked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "locked_until": reset_iso,
            "reason": reason,
        }
        with open(self.lock_file, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, ensure_ascii=False, indent=2)

        local_reset_str = reset_time.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        console.print(
            f"[bold red]⛔ 雲端 API 額度已用盡 ({reason})！[/bold red]\n"
            f"[yellow]⏳ 任務已自動鎖定並延期至明日重置時間：{local_reset_str}[/yellow]"
        )
        return reset_time

    def is_quota_locked(self) -> Tuple[bool, Optional[str]]:
        """Check if cloud API is currently locked due to quota exhaustion.

        Returns:
            Tuple of (is_locked: bool, locked_until_iso: Optional[str])
        """
        if not self.lock_file.is_file():
            return False, None

        try:
            with open(self.lock_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            until_str = data.get("locked_until")
            if not until_str:
                return False, None

            until_dt = datetime.datetime.fromisoformat(until_str)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if now_utc < until_dt:
                return True, until_str
            else:
                # Expired lock, unlock automatically
                self.unlock_quota()
                return False, None
        except Exception:
            return False, None

    def unlock_quota(self) -> None:
        """Clear quota lock once reset time has elapsed."""
        if self.lock_file.is_file():
            try:
                self.lock_file.unlink()
                console.print("[green]✔ 雲端 API 每日重置時間已到，配額鎖定已解除！[/green]")
            except Exception:
                pass

    def handle_stt_quota_error(self, requested_channel: str) -> str:
        """Switch to high-performance local GPU channel upon STT quota exhaustion."""
        console.print(
            f"[yellow]⚠️ 雲端通道 {requested_channel} 配額已耗盡，"
            f"依策略自動切換至本地高效能 GPU 通道 {self.local_fallback_channel}...[/yellow]"
        )
        self.mark_quota_exhausted(service=f"STT-{requested_channel}")
        return self.local_fallback_channel

    def defer_task(
        self,
        task_type: str,
        work_dir: Path | str,
        course: str,
        date: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Enqueue task into deferred_tasks.json scheduled for next day execution."""
        target = Path(work_dir).resolve()
        deferred_file = target / "deferred_tasks.json"

        next_reset = self.calculate_next_reset_datetime()
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
            "resume_after": next_reset.isoformat(),
            "parameters": params or {},
        }
        tasks.append(new_task)

        with open(deferred_file, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)

        local_reset_time = next_reset.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        console.print(
            f"[yellow]📋 已將 {task_type} 任務延期寫入佇列：{deferred_file.name}\n"
            f"   預計於明日配額重置後執行 ({local_reset_time})[/yellow]"
        )
        return deferred_file

    @staticmethod
    def get_quota_comparison_summary() -> Dict[str, Any]:
        """Comparison between Google Official Limits and Local RTX 4060 capabilities."""
        return {
            "google_official_free_tier": {
                "rpm": 15,
                "rpd": "50~100 requests/day (Gemini 2.5/3.x Flash)",
                "tpm": "1,000,000 tokens/min",
                "audio_token_rate": "25 tokens / audio second (~15,000 tokens / 10min chunk)",
                "reset_time": "Daily at UTC 08:00 (Taiwan 16:00)",
                "bottleneck": "Concentrated batch processing of 3+ lectures in one afternoon hits RPD limits.",
            },
            "local_hardware_capabilities": {
                "gpu": "NVIDIA GeForce RTX 4060 (8GB VRAM)",
                "model": "faster-whisper large-v3-turbo (C7)",
                "vram_usage": "~2.8 GB VRAM (int8_float16)",
                "speed": "15x ~ 20x real-time (2hr lecture finishes in ~7 mins)",
                "cost_and_quota": "0 cost, 100% offline, unlimited runs, 0 rate limit",
                "glossary_extraction": "Pure local NLP / regex (<1s per PDF slide deck, 0 API calls)",
            },
            "optimal_routing_strategy": [
                "1. 配額充足時：優先使用雲端 C1 (Gemini Flash)，語音字字逐字精確。",
                "2. 單日課堂量大或遇到 429 限制時：自動切換至本地 C7 轉錄，節省 100% 雲端語音配額。",
                "3. 課堂講義詞庫提取：100% 本地純離線抽取 (0 API 消耗)，保留寶貴配額給文稿整理與摘要。",
                "4. 整理與摘要遇配額上限時：安全延期至次日 16:00 自動補跑，零人工干預。",
            ],
        }
