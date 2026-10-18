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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lecture_pipeline.gui.bridge import (
    abort_processing_task,
    add_course,
    add_glossary_term,
    broadcaster,
    browse_file_dialog,
    browse_folder_dialog,
    delete_course,
    delete_glossary_term,
    delete_lecture,
    edit_glossary_term,
    extract_terms_from_uploaded_file,
    get_archived_lectures,
    get_available_courses,
    get_course_glossary_terms,
    get_current_config,
    get_full_system_status,
    get_lecture_content,
    get_remembered_gui_mode,
    get_timetable_rules,
    is_processing_active,
    is_watcher_paused,
    open_file_in_desktop,
    open_folder_in_desktop,
    pause_processing_task,
    rename_lecture,
    resolve_export_lecture_file,
    save_remembered_gui_mode,
    save_timetable_rules,
    set_watcher_paused,
    start_processing_task,
    switch_gui_mode_action,
    update_pipeline_config,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

if not STATIC_DIR.is_dir() and getattr(sys, "frozen", False):
    mei_pass = getattr(sys, "_MEIPASS", None)
    if mei_pass:
        alt_static = Path(mei_pass) / "lecture_pipeline" / "gui" / "static"
        alt_tpl = Path(mei_pass) / "lecture_pipeline" / "gui" / "templates"
        if alt_static.is_dir():
            STATIC_DIR = alt_static
            TEMPLATES_DIR = alt_tpl
    if not STATIC_DIR.is_dir():
        exe_dir = Path(sys.executable).resolve().parent
        alt_static = exe_dir / "static"
        alt_tpl = exe_dir / "templates"
        if alt_static.is_dir():
            STATIC_DIR = alt_static
            TEMPLATES_DIR = alt_tpl


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
    source: Optional[str] = "browser"


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


class OpenFolderRequest(BaseModel):
    path: str


class OpenFileRequest(BaseModel):
    path: Optional[str] = None
    lecture_id: Optional[str] = None
    file_type: Optional[str] = "summary"


class BrowseFolderRequest(BaseModel):
    initial_dir: Optional[str] = None


class BrowseFileRequest(BaseModel):
    initial_dir: Optional[str] = None


class LectureRenameRequest(BaseModel):
    course: Optional[str] = ""
    title: Optional[str] = ""


class CourseCreateRequest(BaseModel):
    course: str


class TermAddRequest(BaseModel):
    course: str
    term: str
    translation: Optional[str] = ""


class TermEditRequest(BaseModel):
    course: str
    old_term: str
    new_term: str
    new_translation: Optional[str] = ""


class TermDeleteRequest(BaseModel):
    course: str
    term: str


# ---------------------------------------------------------------------------
# Core Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.is_file():
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
@app.post("/api/mode/switch")
async def api_set_mode(req: ModeRequest):
    """Update and persist GUI execution mode ('window', 'web', 'both') and trigger desktop/browser actions."""
    return switch_gui_mode_action(mode=req.mode, source=req.source or "browser")


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
@app.get("/api/lecture/{lecture_id}")
async def api_get_lecture_detail(lecture_id: str):
    """Retrieve full text files and audio metadata for a lecture."""
    content = get_lecture_content(lecture_id)
    if not content:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return content


@app.post("/api/lecture/{lecture_id}/rename")
async def api_rename_lecture(lecture_id: str, req: LectureRenameRequest):
    """Rename lecture metadata or course."""
    res = rename_lecture(lecture_id, new_course=req.course or "", new_title=req.title or "")
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.delete("/api/lecture/{lecture_id}")
async def api_delete_lecture(lecture_id: str):
    """Delete lecture bundle."""
    res = delete_lecture(lecture_id)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.post("/api/lecture/{lecture_id}/open-folder")
async def api_open_lecture_folder(lecture_id: str):
    """Open lecture directory in Windows File Explorer (preferring Exports directory)."""
    export_file = resolve_export_lecture_file(lecture_id, "summary")
    if export_file and export_file.is_file():
        ok = open_folder_in_desktop(export_file.parent)
        if ok:
            return {"status": "ok", "path": str(export_file.parent)}

    detail = get_lecture_content(lecture_id)
    if not detail or not detail.get("path"):
        raise HTTPException(status_code=404, detail="Lecture directory not found")
    ok = open_folder_in_desktop(detail["path"])
    if not ok:
        raise HTTPException(status_code=500, detail="Cannot open lecture directory")
    return {"status": "ok", "path": detail["path"]}


@app.get("/api/audio/{lecture_id}")
async def api_stream_audio(lecture_id: str):
    """Stream audio file for the dual sync player."""
    content = get_lecture_content(lecture_id)
    if content and content.get("audio_path"):
        audio_p = Path(content["audio_path"])
        if audio_p.is_file():
            ext = audio_p.suffix.lower()
            media_map = {
                ".flac": "audio/flac",
                ".opus": "audio/ogg",
                ".ogg": "audio/ogg",
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".m4a": "audio/mp4",
                ".aac": "audio/aac",
            }
            media_type = media_map.get(ext, "application/octet-stream")
            return FileResponse(str(audio_p), media_type=media_type)

    cfg = get_current_config()
    paths = cfg.get("paths", {})
    search_dirs = [
        Path(paths.get("archive_dir", "archive")),
        Path(paths.get("work_dir", "work")),
    ]

    for base in search_dirs:
        for p in base.rglob(f"*{lecture_id}*"):
            if p.is_dir():
                for ext in [".flac", ".opus", ".mp3", ".wav", ".m4a"]:
                    found = list(p.glob(f"*{ext}"))
                    if found:
                        media_type = "audio/ogg" if ext == ".opus" else f"audio/{ext[1:]}"
                        return FileResponse(str(found[0]), media_type=media_type)

    raise HTTPException(status_code=404, detail="Audio file not found")


@app.post("/api/task/pause")
async def api_pause_task():
    """Toggle current running task pause/resume state."""
    return pause_processing_task()


@app.post("/api/task/abort")
async def api_abort_task():
    """Force abort current running task."""
    return abort_processing_task()


@app.post("/api/system/open-folder")
async def api_open_folder(req: OpenFolderRequest):
    """Open folder in Windows File Explorer."""
    ok = open_folder_in_desktop(req.path)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Cannot open folder: {req.path}")
    return {"status": "ok", "path": req.path}


@app.post("/api/system/open-file")
async def api_open_file(req: OpenFileRequest):
    """Open target note or summary file in default Windows application, directing to Exports directory."""
    target_path = None
    if req.path:
        target_path = Path(req.path)
    elif req.lecture_id:
        target_path = resolve_export_lecture_file(req.lecture_id, req.file_type or "summary")
        if not target_path or not target_path.exists():
            detail = get_lecture_content(req.lecture_id)
            if detail and detail.get("path"):
                base_dir = Path(detail["path"])
                if req.file_type == "summary":
                    target_path = base_dir / "03_summary.md"
                elif req.file_type == "cleaned":
                    target_path = base_dir / "02_cleaned.txt"
                elif req.file_type == "raw":
                    target_path = base_dir / "01_raw_timestamped.txt"
                    if not target_path.is_file():
                        target_path = base_dir / "01_raw.txt"
                else:
                    target_path = base_dir

    if not target_path or not target_path.exists():
        raise HTTPException(status_code=404, detail="Target file or directory not found")

    if target_path.is_dir():
        ok = open_folder_in_desktop(target_path)
    else:
        ok = open_file_in_desktop(target_path)

    if not ok:
        raise HTTPException(status_code=500, detail=f"Cannot open: {target_path}")
    return {"status": "ok", "path": str(target_path)}


@app.post("/api/system/browse-folder")
async def api_browse_folder(req: BrowseFolderRequest):
    """Launch native directory picker dialog."""
    selected = browse_folder_dialog(req.initial_dir)
    if selected:
        return {"status": "ok", "path": selected}
    return {"status": "cancelled", "path": None}


@app.post("/api/system/browse-file")
async def api_browse_file(req: BrowseFileRequest):
    """Launch native file picker dialog."""
    selected = browse_file_dialog(req.initial_dir)
    if selected:
        return {"status": "ok", "path": selected}
    return {"status": "cancelled", "path": None}


@app.get("/api/glossary")
async def api_get_glossary(course: Optional[str] = None):
    """Get domain terms for course along with list of all available courses."""
    courses = get_available_courses()
    if not courses:
        return {"status": "ok", "course": "", "courses": [], "terms": []}
    selected_course = course if (course and course in courses) else courses[0]
    terms = get_course_glossary_terms(selected_course)
    return {"status": "ok", "course": selected_course, "courses": courses, "terms": terms}


@app.post("/api/glossary/course")
async def api_add_course(req: CourseCreateRequest):
    """Create a new course directory and glossary."""
    res = add_course(req.course)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.delete("/api/glossary/course/{course}")
async def api_delete_course(course: str):
    """Delete a course directory and its glossary."""
    res = delete_course(course)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.post("/api/glossary/term")
async def api_add_term(req: TermAddRequest):
    """Add a domain term to course glossary."""
    res = add_glossary_term(req.course, req.term, req.translation or "")
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.put("/api/glossary/term")
async def api_edit_term(req: TermEditRequest):
    """Edit existing term in course glossary."""
    res = edit_glossary_term(req.course, req.old_term, req.new_term, req.new_translation or "")
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.delete("/api/glossary/term")
async def api_delete_term(req: TermDeleteRequest):
    """Delete domain term from course glossary."""
    res = delete_glossary_term(req.course, req.term)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.post("/api/glossary/upload-pdf")
async def api_upload_pdf(file: UploadFile = File(...), course: str = Form("")):
    """Upload syllabus PDF/TXT and extract technical glossary terms."""
    content = await file.read()
    terms = extract_terms_from_uploaded_file(content, file.filename, course=course)
    return {"status": "ok", "course": course, "terms": terms, "message": f"成功從 {file.filename} 萃取專有名詞"}


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
