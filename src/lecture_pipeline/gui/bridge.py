"""Bridge layer connecting FastAPI GUI with lecture pipeline backend.

Handles config persistence (remembering GUI execution mode), system diagnostics,
timetable CRUD, lecture history queries, background task execution, and WebSocket log streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml
from starlette.websockets import WebSocket, WebSocketState

from lecture_pipeline.state import load_config
from lecture_pipeline.automation.offline_queue import is_internet_connected
from lecture_pipeline.automation.watcher import check_disk_space, run_pipeline_for_group


CONFIG_FILE_PATH = Path("config.yaml")


class EventBroadcaster:
    """Manages connected WebSockets and broadcasts live progress, logs, and events."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.lock = threading.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def register_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        with self.lock:
            self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        with self.lock:
            self.active_connections.discard(websocket)

    def broadcast_sync(self, message: Dict[str, Any]) -> None:
        """Thread-safe synchronous broadcast to all active WebSockets."""
        if not self.loop or self.loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        dead_connections: List[WebSocket] = []
        with self.lock:
            connections = list(self.active_connections)

        for conn in connections:
            if conn.client_state == WebSocketState.CONNECTED:
                try:
                    await conn.send_text(payload)
                except Exception:
                    dead_connections.append(conn)
            else:
                dead_connections.append(conn)

        if dead_connections:
            with self.lock:
                for dead in dead_connections:
                    self.active_connections.discard(dead)


broadcaster = EventBroadcaster()


# ---------------------------------------------------------------------------
# Config & Execution Mode Persistence
# ---------------------------------------------------------------------------

def get_current_config() -> Dict[str, Any]:
    """Load configuration from config.yaml with defaults."""
    cfg = load_config(CONFIG_FILE_PATH)
    if "gui" not in cfg:
        cfg["gui"] = {
            "mode": "window",
            "host": "127.0.0.1",
            "port": 8000,
            "auto_open_browser": True,
            "window_width": 1280,
            "window_height": 800,
        }
    return cfg


def get_remembered_gui_mode() -> str:
    """Return remembered execution mode ('window', 'web', 'both') from config.yaml."""
    cfg = get_current_config()
    gui_cfg = cfg.get("gui", {})
    mode = str(gui_cfg.get("mode", "window")).lower().strip()
    return mode if mode in {"window", "web", "both"} else "window"


def save_remembered_gui_mode(mode: str) -> str:
    """Save user-selected GUI mode to config.yaml so next launch remembers it."""
    valid_mode = mode.lower().strip()
    if valid_mode not in {"window", "web", "both"}:
        valid_mode = "window"

    if not CONFIG_FILE_PATH.is_file():
        return valid_mode

    try:
        content = CONFIG_FILE_PATH.read_text(encoding="utf-8")
        # Replace existing gui.mode
        pattern = r"(gui:\s*\n(?:\s+#[^\n]*\n)*\s+mode:\s*[\"']?)[a-zA-Z0-9_]+([\"']?)"
        if re.search(pattern, content):
            new_content = re.sub(pattern, rf"\g<1>{valid_mode}\g<2>", content)
        else:
            # Append if not found
            new_content = content + f"\n\ngui:\n  mode: \"{valid_mode}\"\n  host: \"127.0.0.1\"\n  port: 8000\n"

        CONFIG_FILE_PATH.write_text(new_content, encoding="utf-8")
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to update mode in config.yaml: {e}")

    return valid_mode


def update_pipeline_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update configurable parameters in config.yaml."""
    cfg = load_config(CONFIG_FILE_PATH)
    for k, v in updates.items():
        if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
            cfg[k].update(v)
        else:
            cfg[k] = v

    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to save config.yaml: {e}")

    return cfg


# ---------------------------------------------------------------------------
# Timetable Management (CRUD)
# ---------------------------------------------------------------------------

DAY_ZH_MAP = {
    "Monday": "週一",
    "Tuesday": "週二",
    "Wednesday": "週三",
    "Thursday": "週四",
    "Friday": "週五",
    "Saturday": "週六",
    "Sunday": "週日",
}


def get_timetable_rules() -> Dict[str, Any]:
    """Parse timetable entries from config.yaml into clean JSON rules."""
    cfg = load_config(CONFIG_FILE_PATH)
    tt_section = cfg.get("timetable", {})
    enabled = bool(tt_section.get("enabled", True))

    rules: List[Dict[str, Any]] = []
    rule_id = 1
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        day_rules = tt_section.get(day)
        if isinstance(day_rules, list):
            for r in day_rules:
                if isinstance(r, dict) and "time" in r and "course" in r:
                    rules.append({
                        "id": f"rule_{rule_id}",
                        "day": day,
                        "dayZh": DAY_ZH_MAP.get(day, day),
                        "time": str(r["time"]),
                        "course": str(r["course"]),
                    })
                    rule_id += 1

    return {"enabled": enabled, "rules": rules}


def save_timetable_rules(rules: List[Dict[str, Any]], enabled: bool = True) -> Dict[str, Any]:
    """Save timetable rules back into config.yaml."""
    cfg = load_config(CONFIG_FILE_PATH)
    new_tt: Dict[str, Any] = {"enabled": enabled}

    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        new_tt[day] = []

    for r in rules:
        day = r.get("day", "Monday")
        if day in new_tt:
            new_tt[day].append({
                "time": str(r.get("time", "09:00-11:00")),
                "course": str(r.get("course", "")),
            })

    # Clean up empty days to None for clean YAML
    for day in list(new_tt.keys()):
        if day != "enabled" and not new_tt[day]:
            new_tt[day] = None

    cfg["timetable"] = new_tt
    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to update timetable: {e}")

    return get_timetable_rules()


# ---------------------------------------------------------------------------
# System Status & Diagnostics
# ---------------------------------------------------------------------------

_watcher_paused = False


def is_watcher_paused() -> bool:
    global _watcher_paused
    return _watcher_paused


def set_watcher_paused(paused: bool) -> bool:
    global _watcher_paused
    _watcher_paused = paused
    broadcaster.broadcast_sync({
        "type": "watcher_state",
        "paused": _watcher_paused,
        "badge": "已暫停" if _watcher_paused else "常駐待命",
    })
    return _watcher_paused


def get_full_system_status() -> Dict[str, Any]:
    """Collect real hardware, network, token, and watcher diagnostics."""
    # 1. GPU Check
    gpu_name = "未偵測到 GPU"
    vram_free = "0 MB"
    gpu_driver = "未知"
    gpu_ok = False
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name,memory.free,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if smi.returncode == 0 and smi.stdout.strip():
            parts = [p.strip() for p in smi.stdout.strip().split(",")]
            if len(parts) >= 3:
                gpu_name = parts[0]
                free_mb = int(parts[1])
                vram_free = f"{round(free_mb / 1024, 1)} GB"
                gpu_driver = parts[2]
                gpu_ok = True
    except Exception:
        pass

    # 2. Internet Connection
    net_ok = is_internet_connected(timeout=2.0)

    # 3. Google Docs OAuth status
    token_file = Path("token.json")
    cred_file = Path("credentials.json")
    docs_authorized = token_file.is_file() and cred_file.is_file()

    # 4. Disk space
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    inbox_dir = paths.get("inbox_dir", "inbox")
    disk_ok, disk_free_gb = check_disk_space(inbox_dir, min_free_gb=2.0)

    # 5. Gemini API Key check
    gemini_key_set = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    return {
        "mode": get_remembered_gui_mode(),
        "gpu": {
            "ok": gpu_ok,
            "name": gpu_name,
            "vram_free": vram_free,
            "driver": gpu_driver,
            "label": f"{gpu_name} ({vram_free})" if gpu_ok else "本機 CPU 運算",
        },
        "network": {
            "ok": net_ok,
            "label": "在線 / 額度充裕" if net_ok else "離線模式 (自動排隊)",
        },
        "google_docs": {
            "ok": docs_authorized,
            "label": "Google Docs 已授權" if docs_authorized else "尚未設定 OAuth",
        },
        "api_key": {
            "ok": gemini_key_set,
            "label": "Gemini API 金鑰就緒" if gemini_key_set else "缺少 GEMINI_API_KEY",
        },
        "disk": {
            "ok": disk_ok,
            "free_gb": disk_free_gb,
        },
        "watcher": {
            "paused": is_watcher_paused(),
            "badge": "已暫停" if is_watcher_paused() else "常駐待命",
        },
    }


# ---------------------------------------------------------------------------
# History & Lecture Library
# ---------------------------------------------------------------------------

def get_archived_lectures() -> List[Dict[str, Any]]:
    """Scan archive and work directories for processed lecture bundles."""
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    archive_dir = Path(paths.get("archive_dir", "archive"))
    work_dir = Path(paths.get("work_dir", "work"))

    search_dirs = [archive_dir, work_dir]
    lectures: List[Dict[str, Any]] = []

    seen_ids: Set[str] = set()

    for base in search_dirs:
        if not base.is_dir():
            continue

        for item in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not item.is_dir():
                continue

            lecture_id = item.name
            if lecture_id in seen_ids:
                continue

            summary_file = item / "03_summary.md"
            cleaned_file = item / "02_cleaned.txt"
            raw_file = item / "01_raw.txt"
            run_json = item / "run.json"

            # Locate audio file in this lecture directory
            audio_path = None
            for ext in [".opus", ".wav", ".mp3", ".m4a"]:
                matched = list(item.glob(f"*{ext}"))
                if matched:
                    audio_path = matched[0]
                    break

            if summary_file.is_file() or cleaned_file.is_file() or raw_file.is_file():
                seen_ids.add(lecture_id)

                summary_text = summary_file.read_text(encoding="utf-8") if summary_file.is_file() else ""
                cleaned_text = cleaned_file.read_text(encoding="utf-8") if cleaned_file.is_file() else ""
                
                # Extract title/course if available
                course_name = lecture_id
                date_str = ""
                m = re.match(r"^(\d{6})_(.+)$", lecture_id)
                if m:
                    date_str = f"20{m.group(1)[:2]}-{m.group(1)[2:4]}-{m.group(1)[4:6]}"
                    course_name = m.group(2)

                lectures.append({
                    "id": lecture_id,
                    "title": course_name,
                    "date": date_str or time.strftime("%Y-%m-%d", time.localtime(item.stat().st_mtime)),
                    "path": str(item.resolve()),
                    "has_summary": summary_file.is_file(),
                    "has_cleaned": cleaned_file.is_file(),
                    "has_audio": audio_path is not None,
                    "audio_filename": audio_path.name if audio_path else None,
                    "summary_preview": summary_text[:200] if summary_text else "",
                    "char_count": len(cleaned_text),
                })

    return lectures


def get_lecture_content(lecture_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full text files and audio metadata for a specific lecture."""
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    search_dirs = [
        Path(paths.get("archive_dir", "archive")),
        Path(paths.get("work_dir", "work")),
    ]

    for base in search_dirs:
        item = base / lecture_id
        if item.is_dir():
            summary_file = item / "03_summary.md"
            cleaned_file = item / "02_cleaned.txt"
            raw_file = item / "01_raw.txt"

            audio_file = None
            for ext in [".opus", ".wav", ".mp3", ".m4a"]:
                found = list(item.glob(f"*{ext}"))
                if found:
                    audio_file = found[0]
                    break

            return {
                "id": lecture_id,
                "summary": summary_file.read_text(encoding="utf-8") if summary_file.is_file() else "",
                "cleaned": cleaned_file.read_text(encoding="utf-8") if cleaned_file.is_file() else "",
                "raw": raw_file.read_text(encoding="utf-8") if raw_file.is_file() else "",
                "has_audio": audio_file is not None,
                "audio_url": f"/api/audio/{lecture_id}" if audio_file else None,
            }

    return None


# ---------------------------------------------------------------------------
# Background Task Execution
# ---------------------------------------------------------------------------

_processing_lock = threading.Lock()
_current_task_info: Dict[str, Any] = {"running": False}


def is_processing_active() -> bool:
    return _current_task_info.get("running", False)


def start_processing_task(audio_files: List[Path], channel_id: str = "C1") -> bool:
    """Launch end-to-end lecture pipeline in background thread with WebSocket progress broadcasts."""
    global _current_task_info
    with _processing_lock:
        if _current_task_info.get("running", False):
            return False

        _current_task_info = {
            "running": True,
            "files": [f.name for f in audio_files],
            "start_time": time.time(),
        }

    def _worker():
        global _current_task_info
        try:
            broadcaster.broadcast_sync({
                "type": "progress",
                "progress": 5,
                "stage": "準備前處理",
                "detail": f"開始處理音訊檔案：{', '.join(f.name for f in audio_files)}",
            })

            cfg = get_current_config()
            paths = cfg.get("paths", {})
            archive_dir = paths.get("archive_dir", "archive")
            rec_archive = paths.get("record_archive_dir")

            out_dir = run_pipeline_for_group(
                audio_files=audio_files,
                channel_id=channel_id,
                pre_flags=cfg.get("preprocessing", {}).get("hp", "100Hz"),
                cleanup_model=cfg.get("cleanup", {}).get("model", "gemini-3.5-flash-lite"),
                summary_model=cfg.get("summarize", {}).get("model", "gemini-3.5-flash-lite"),
                archive_dir=archive_dir,
                upload_drive=True,
                record_archive_base_dir=rec_archive,
            )

            broadcaster.broadcast_sync({
                "type": "progress",
                "progress": 100,
                "stage": "處理完成",
                "detail": f"課堂已成功歸檔至 {out_dir.name}",
            })
            broadcaster.broadcast_sync({
                "type": "task_completed",
                "lecture_id": out_dir.name,
            })

        except Exception as e:
            broadcaster.broadcast_sync({
                "type": "progress",
                "progress": 0,
                "stage": "處理失敗",
                "detail": f"管線執行異常：{str(e)}",
            })
        finally:
            with _processing_lock:
                _current_task_info = {"running": False}

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    return True
