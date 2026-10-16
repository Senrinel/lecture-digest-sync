"""Google Drive API publisher for Phase 5 (Specification Section 9).

Automatically uploads cleaned transcripts and lecture notes to Google Drive,
converting them into native Google Docs stored under `LectureNotes/<Course>/`
for 1-click citation and live sync in Google NotebookLM.
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from pydantic import BaseModel, Field
from rich.console import Console

from lecture_pipeline.export.notebook_bundle import prepare_notebook_bundle
from lecture_pipeline.state import load_run_state, save_run_state

console = Console(legacy_windows=False)

# Standard OAuth 2.0 Scope for Google Drive file management
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
]


class DriveDocInfo(BaseModel):
    """Metadata for an uploaded Google Doc."""

    file_id: str
    title: str
    web_link: str
    status: str  # "CREATED" | "UPDATED"
    mime_type: str = "application/vnd.google-apps.document"


class DrivePublishResult(BaseModel):
    """Result of publishing lecture notes to Google Drive."""

    course: str
    date: str
    root_folder_id: str
    course_folder_id: str
    course_folder_link: str
    docs: List[DriveDocInfo]
    uploaded_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _run_local_server_with_display(
    flow: InstalledAppFlow,
    port: int = 8080,
    timeout_seconds: Optional[int] = 1800,
) -> Any:
    """Run local server for OAuth with prominent URL printing and multi-method browser launch."""
    import wsgiref.simple_server
    from google_auth_oauthlib.flow import _RedirectWSGIApp, _WSGIRequestHandler

    success_html = (
        "<html><head><meta charset='utf-8'><title>Google Drive 授權成功</title></head>"
        "<body style='font-family: sans-serif; text-align: center; padding: 40px; background: #0f172a; color: #f8fafc;'>"
        "<h2 style='color: #34d399;'>🎉 Google Drive 授權成功！</h2>"
        "<p style='color: #94a3b8; font-size: 16px;'>已成功取得存取權杖，您可以關閉此分頁並返回終端機繼續執行。</p>"
        "</body></html>"
    )
    wsgi_app = _RedirectWSGIApp(success_html)
    local_server = wsgiref.simple_server.make_server(
        "localhost",
        port,
        wsgi_app,
        handler_class=_WSGIRequestHandler,
    )

    try:
        flow.redirect_uri = f"http://localhost:{local_server.server_port}/"
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

        console.print("\n" + "=" * 70)
        console.print("[bold yellow]👉 請點擊以下授權連結進行 Google 帳號授權：[/bold yellow]")
        console.print(f"[bold underline cyan]{auth_url}[/bold underline cyan]")
        console.print("=" * 70 + "\n")
        sys.stdout.flush()

        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "google_auth_url.txt").write_text(auth_url, encoding="utf-8")

        # Attempt to open browser automatically on Windows
        try:
            if sys.platform == "win32":
                os.startfile(auth_url)
            else:
                webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception:
            pass

        local_server.timeout = timeout_seconds

        # Loop until a request containing OAuth parameters arrives (ignores favicon etc.)
        start_wait = time.time()
        while not wsgi_app.last_request_uri or ("code=" not in wsgi_app.last_request_uri and "error=" not in wsgi_app.last_request_uri):
            local_server.handle_request()
            if timeout_seconds and (time.time() - start_wait > timeout_seconds):
                raise TimeoutError("授權等候逾時（超過 30 分鐘），請重新執行授權。")

        if not wsgi_app.last_request_uri:
            raise RuntimeError("未收到有效的授權回傳網址。")

        authorization_response = wsgi_app.last_request_uri.replace("http", "https")
        flow.fetch_token(authorization_response=authorization_response)
    finally:
        local_server.server_close()

    return flow.credentials


class GoogleDrivePublisher:
    """Manages OAuth2 authentication, folder structuring, and Google Docs conversion on Google Drive."""

    def __init__(
        self,
        credentials_file: str | Path = "credentials.json",
        token_file: str | Path = "token.json",
        scopes: Optional[List[str]] = None,
    ):
        self.credentials_file = Path(credentials_file).resolve()
        self.token_file = Path(token_file).resolve()
        self.scopes = scopes or DEFAULT_SCOPES
        self._service = None

    def get_service(self):
        """Authenticate and return Google Drive API v3 resource."""
        if self._service is not None:
            return self._service

        creds = None
        if self.token_file.is_file():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file), self.scopes)
            except Exception as e:
                console.print(f"[yellow]⚠️ 讀取現有 token.json 失敗 ({e})，重新啟動授權流程...[/yellow]")
                creds = None

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    console.print("[dim]🔄 正在靜默更新 Google Drive 存取權杖 (Refresh Token)...[/dim]")
                    creds.refresh(Request())
                except Exception as e:
                    console.print(f"[yellow]⚠️ Refresh Token 更新失敗 ({e})，需要重新登入。[/yellow]")
                    creds = None

            if not creds:
                if not self.credentials_file.is_file():
                    raise FileNotFoundError(
                        f"找不到 Google OAuth 用戶端憑證：{self.credentials_file}。\n"
                        f"請依指引自 Google Cloud Console 下載憑證並命名為 'credentials.json' 放置於專案根目錄。"
                    )

                console.print(f"\n[bold cyan]🔐 啟動 Google OAuth 2.0 桌面授權流程...[/bold cyan]")
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), self.scopes)
                creds = _run_local_server_with_display(flow, port=8080)

            # Save the credentials for subsequent automated executions
            with open(self.token_file, "w", encoding="utf-8") as token:
                token.write(creds.to_json())
            console.print(f"[green]✔ Google Drive 授權成功！憑證已快取至：{self.token_file}[/green]\n")

        import socket
        socket.setdefaulttimeout(180)

        import httplib2
        import google_auth_httplib2
        authorized_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=180))
        self._service = build("drive", "v3", http=authorized_http)
        return self._service

    def get_or_create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """Find existing folder or create new one in Google Drive."""
        service = self.get_service()

        query_parts = [
            f"name = '{folder_name}'",
            "mimeType = 'application/vnd.google-apps.folder'",
            "trashed = false",
        ]
        if parent_id:
            query_parts.append(f"'{parent_id}' in parents")

        query = " and ".join(query_parts)
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name)",
            )
            .execute()
        )

        files = response.get("files", [])
        if files:
            return files[0]["id"]

        # Create folder if not found
        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            folder_metadata["parents"] = [parent_id]

        folder = (
            service.files()
            .create(
                body=folder_metadata,
                fields="id",
            )
            .execute()
        )
        return folder["id"]

    def upload_or_update_doc(
        self,
        local_filepath: Path | str,
        doc_title: str,
        parent_folder_id: str,
    ) -> DriveDocInfo:
        """Upload markdown/text file and automatically convert to native Google Doc."""
        path = Path(local_filepath).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到欲上傳的本機檔案：{path}")

        service = self.get_service()

        # Check if document with same title already exists in the course folder
        query = (
            f"name = '{doc_title}' and "
            f"'{parent_folder_id}' in parents and "
            f"trashed = false"
        )
        res = service.files().list(q=query, spaces="drive", fields="files(id, name, webViewLink)").execute()
        existing_files = res.get("files", [])

        media = MediaFileUpload(str(path), mimetype="text/plain", resumable=False)

        last_err = None
        for attempt in range(3):
            try:
                if existing_files:
                    # Update existing document in-place
                    file_id = existing_files[0]["id"]
                    updated = (
                        service.files()
                        .update(
                            fileId=file_id,
                            media_body=media,
                            fields="id, name, webViewLink",
                        )
                        .execute()
                    )
                    return DriveDocInfo(
                        file_id=updated["id"],
                        title=updated["name"],
                        web_link=updated.get("webViewLink", f"https://docs.google.com/document/d/{updated['id']}/edit"),
                        status="UPDATED",
                    )

                # Create new Google Doc via conversion
                file_metadata = {
                    "name": doc_title,
                    "parents": [parent_folder_id],
                    "mimeType": "application/vnd.google-apps.document",  # Triggers automatic Google Docs conversion
                }
                created = (
                    service.files()
                    .create(
                        body=file_metadata,
                        media_body=media,
                        fields="id, name, webViewLink",
                    )
                    .execute()
                )
                return DriveDocInfo(
                    file_id=created["id"],
                    title=created["name"],
                    web_link=created.get("webViewLink", f"https://docs.google.com/document/d/{created['id']}/edit"),
                    status="CREATED",
                )
            except Exception as e:
                last_err = e
                console.print(f"[yellow]⚠️ Google Docs 轉檔上傳遭遇暫時性中斷 ({e})，正在重試 ({attempt + 1}/3)...[/yellow]")
                time.sleep(2.0)

        raise last_err

    def publish_lecture(
        self,
        work_dir: Path | str,
        custom_course: Optional[str] = None,
        custom_date: Optional[str] = None,
    ) -> DrivePublishResult:
        """Publish Phase 3 and Phase 4 lecture outputs to Google Drive under `LectureNotes/<Course>/`."""
        target_work_dir = Path(work_dir).resolve()
        state = load_run_state(target_work_dir)

        course = custom_course or (state.course if state else "Lecture")
        date_str = custom_date or (state.date if state else datetime.now().strftime("%Y%m%d"))

        # Format human-readable date if YYYYMMDD
        formatted_date = date_str
        if len(date_str) == 8 and date_str.isdigit():
            formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

        # Ensure NotebookLM bundle files exist
        bundle = prepare_notebook_bundle(target_work_dir)

        console.print(f"\n[bold cyan]☁️ 啟動 Google Drive 雲端發布：{course} ({date_str})[/bold cyan]")

        # 1. Establish Folder Structure: LectureNotes/<Course>
        root_folder_id = self.get_or_create_folder("LectureNotes")
        course_folder_id = self.get_or_create_folder(course, parent_id=root_folder_id)
        course_folder_link = f"https://drive.google.com/drive/folders/{course_folder_id}"

        console.print(f"📂 雲端硬碟目標資料夾：[bold]LectureNotes/{course}[/bold]")
        console.print(f"🔗 資料夾連結：[dim]{course_folder_link}[/dim]")

        docs_info: List[DriveDocInfo] = []

        # 2. Upload Phase 4: Lecture Summary Notes
        summary_title = f"{course}_{formatted_date}_重點總結筆記"
        console.print(f"   ⏳ 正在上傳並轉換考前重點筆記 ➜ Google Docs...")
        doc_summary = self.upload_or_update_doc(
            local_filepath=bundle.summary_file,
            doc_title=summary_title,
            parent_folder_id=course_folder_id,
        )
        status_tag = "[green]新建立[/green]" if doc_summary.status == "CREATED" else "[cyan]更新既有[/cyan]"
        console.print(f"   ✔ 重點總結筆記 ({status_tag})：{doc_summary.web_link}")
        docs_info.append(doc_summary)

        time.sleep(2.0)  # Conservative pause between Drive uploads

        # 3. Upload Phase 3: Cleaned Full Transcript
        cleaned_title = f"{course}_{formatted_date}_完整課堂整理文稿"
        console.print(f"   ⏳ 正在上傳並轉換完整課堂整理文稿 ➜ Google Docs...")
        doc_cleaned = self.upload_or_update_doc(
            local_filepath=bundle.cleaned_file,
            doc_title=cleaned_title,
            parent_folder_id=course_folder_id,
        )
        status_tag = "[green]新建立[/green]" if doc_cleaned.status == "CREATED" else "[cyan]更新既有[/cyan]"
        console.print(f"   ✔ 完整整理文稿 ({status_tag})：{doc_cleaned.web_link}")
        docs_info.append(doc_cleaned)

        # 4. Update Run State Metadata
        if state:
            state.stage_status["drive_upload"] = "DONE"
            state.metadata["drive_course_folder_link"] = course_folder_link
            state.metadata["drive_summary_doc_link"] = doc_summary.web_link
            state.metadata["drive_cleaned_doc_link"] = doc_cleaned.web_link
            save_run_state(state, target_work_dir)

        res = DrivePublishResult(
            course=course,
            date=date_str,
            root_folder_id=root_folder_id,
            course_folder_id=course_folder_id,
            course_folder_link=course_folder_link,
            docs=docs_info,
            metadata={"work_dir": str(target_work_dir)},
        )

        return res
