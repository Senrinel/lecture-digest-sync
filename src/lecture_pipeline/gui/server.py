"""FastAPI Web Server for Lecture Digest Sync GUI.

Provides RESTful endpoints and WebSockets for the responsive Web and Desktop interfaces.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lecture_pipeline.gui.bridge import (
    broadcaster,
    get_archived_lectures,
    get_current_config,
    get_full_system_status,
    get_lecture_content,
    get_remembered_gui_mode,
    get_timetable_rules,
    is_processing_active,
    is_watcher_paused,
    save_remembered_gui_mode,
    save_timetable_rules,
    set_watcher_paused,
    start_processing_task,
    update_pipeline_config,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    broadcaster.register_loop(loop)
    yield


app = FastAPI(title="Lecture Digest Sync GUI Server", lifespan=lifespan)

# Allow CORS for local dev / dual-mode cross-origin access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class ModeRequest(BaseModel):
    mode: str


class ConfigUpdateRequest(BaseModel):
    params: Dict[str, Any]


class TimetableSaveRequest(BaseModel):
    enabled: bool = True
    rules: List[Dict[str, Any]]


class ProcessFileRequest(BaseModel):
    filepath: str
    channel_id: Optional[str] = "C1"


class ProcessFolderRequest(BaseModel):
    folderpath: str
    channel_id: Optional[str] = "C1"


# ---------------------------------------------------------------------------
# Core Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.is_file():
        # Fallback to root gui_preview.html if templates/index.html not created yet
        root_preview = Path("gui_preview.html")
        if root_preview.is_file():
            return HTMLResponse(content=root_preview.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="Index template not found")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/icon.png")
async def serve_icon():
    icon_file = STATIC_DIR / "icon.png"
    if not icon_file.is_file():
        icon_file = Path("icon.png")
    if icon_file.is_file():
        return FileResponse(str(icon_file), media_type="image/png")
    raise HTTPException(status_code=404, detail="icon.png not found")


@app.get("/api/status")
async def api_status():
    """Retrieve full live status (hardware GPU, network, Google Docs auth, watcher)."""
    return get_full_system_status()


@app.post("/api/mode")
async def api_set_mode(req: ModeRequest):
    """Update and persist GUI execution mode ('window', 'web', 'both') in config.yaml."""
    saved_mode = save_remembered_gui_mode(req.mode)
    return {
        "status": "ok",
        "mode": saved_mode,
        "message": f"介面模式已更新並寫入設定檔：{saved_mode}",
    }


@app.get("/api/config")
async def api_get_config():
    """Retrieve current system configuration."""
    return get_current_config()


@app.post("/api/config")
async def api_update_config(req: ConfigUpdateRequest):
    """Update pipeline configuration."""
    updated = update_pipeline_config(req.params)
    return {"status": "ok", "config": updated}


@app.get("/api/timetable")
async def api_get_timetable():
    """Get timetable rules from config.yaml."""
    return get_timetable_rules()


@app.post("/api/timetable")
async def api_save_timetable(req: TimetableSaveRequest):
    """Save timetable rules to config.yaml."""
    res = save_timetable_rules(rules=req.rules, enabled=req.enabled)
    return {"status": "ok", "timetable": res}


@app.post("/api/watcher/toggle")
async def api_toggle_watcher():
    """Toggle background inbox watcher paused/resumed."""
    new_state = set_watcher_paused(not is_watcher_paused())
    return {
        "status": "ok",
        "paused": new_state,
        "badge": "已暫停" if new_state else "常駐待命",
    }


@app.get("/api/history")
async def api_get_history():
    """Retrieve list of archived lecture folders."""
    return get_archived_lectures()


@app.get("/api/history/{lecture_id}")
async def api_get_lecture_detail(lecture_id: str):
    """Retrieve full text files and audio metadata for a lecture."""
    content = get_lecture_content(lecture_id)
    if not content:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return content


@app.get("/api/audio/{lecture_id}")
async def api_stream_audio(lecture_id: str):
    """Stream audio file for the dual sync player."""
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    search_dirs = [
        Path(paths.get("archive_dir", "archive")),
        Path(paths.get("work_dir", "work")),
    ]

    for base in search_dirs:
        item = base / lecture_id
        if item.is_dir():
            for ext in [".opus", ".wav", ".mp3", ".m4a"]:
                found = list(item.glob(f"*{ext}"))
                if found:
                    media_type = "audio/ogg" if ext == ".opus" else f"audio/{ext[1:]}"
                    return FileResponse(str(found[0]), media_type=media_type)

    raise HTTPException(status_code=404, detail="Audio file not found")


@app.post("/api/process/upload")
async def api_upload_audio(file: UploadFile = File(...)):
    """Upload audio file and place it in the inbox directory for processing."""
    cfg = get_current_config()
    inbox_dir = Path(cfg.get("paths", {}).get("inbox_dir", "inbox"))
    inbox_dir.mkdir(parents=True, exist_ok=True)

    dest_file = inbox_dir / file.filename
    with open(dest_file, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Trigger processing in background
    started = start_processing_task([dest_file])
    return {
        "status": "ok",
        "filename": file.filename,
        "processing_started": started,
        "message": "音檔已上傳並進入處理佇列",
    }


@app.post("/api/process/file")
async def api_process_file(req: ProcessFileRequest):
    """Trigger processing on a local file path."""
    p = Path(req.filepath)
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {req.filepath}")

    started = start_processing_task([p], channel_id=req.channel_id or "C1")
    if not started:
        raise HTTPException(status_code=409, detail="Another task is already in progress")

    return {"status": "ok", "message": f"已啟動處理任務：{p.name}"}


@app.post("/api/process/folder")
async def api_process_folder(req: ProcessFolderRequest):
    """Trigger processing on all audio files in a local folder."""
    p = Path(req.folderpath)
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Folder not found: {req.folderpath}")

    audio_files: List[Path] = []
    for ext in [".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"]:
        audio_files.extend(list(p.glob(f"*{ext}")))

    if not audio_files:
        raise HTTPException(status_code=400, detail="No audio files found in directory")

    started = start_processing_task(audio_files, channel_id=req.channel_id or "C1")
    if not started:
        raise HTTPException(status_code=409, detail="Another task is already in progress")

    return {"status": "ok", "count": len(audio_files), "message": f"已啟動整堂課處理（共 {len(audio_files)} 檔）"}


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket connection for real-time progress, log streaming, and watcher events."""
    await broadcaster.connect(websocket)
    try:
        await websocket.send_json({"type": "connected", "message": "WebSocket connected"})
        while True:
            data = await websocket.receive_text()
            # Echo ping/pong if needed
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket)
    except Exception:
        broadcaster.disconnect(websocket)
