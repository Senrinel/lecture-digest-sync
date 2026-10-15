"""Semi-automatic Google Drive / Google Docs uploader module.

Uploads cleaned transcript (02_clean.md) and study notes (03_notes.md) to Google Drive,
converting them directly into Google Docs format.
This allows seamless one-click source syncing within Gemini Notebook (NotebookLM).
"""

import os
from pathlib import Path
from typing import Dict, Optional
from rich.console import Console

console = Console()
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GoogleDrivePublisher:
    def __init__(
        self,
        credentials_file: str | Path = "credentials.json",
        token_file: str | Path = "token.json",
        root_folder_name: str = "LectureNotes",
    ):
        self.credentials_file = Path(credentials_file)
        self.token_file = Path(token_file)
        self.root_folder_name = root_folder_name
        self.service = None

    def authenticate(self) -> bool:
        """Authenticates with Google Drive via OAuth 2.0 desktop flow."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            console.print("[red]❌ 請安裝 google-api-python-client 與 google-auth-oauthlib。[/red]")
            return False

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_file.exists():
                    console.print(
                        f"[yellow]⚠️ 尚未找到 Google Drive 憑證檔案：{self.credentials_file}[/yellow]\n"
                        "若需啟用半自動同步至 Google Drive，請在 Google Cloud Console 下載 OAuth 用戶端 ID 憑證（credentials.json）。\n"
                    )
                    return False
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), SCOPES)
                creds = flow.run_local_server(port=0)

            with open(self.token_file, "w", encoding="utf-8") as token:
                token.write(creds.to_json())

        self.service = build("drive", "v3", credentials=creds)
        return True

    def _get_or_create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """Finds or creates a folder in Google Drive."""
        query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{folder_name}' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        response = self.service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            folder_metadata["parents"] = [parent_id]

        folder = self.service.files().create(body=folder_metadata, fields="id").execute()
        return folder.get("id")

    def upload_markdown_as_doc(
        self,
        markdown_path: str | Path,
        doc_title: str,
        course_name: str,
    ) -> Optional[str]:
        """Uploads a markdown file and converts it into a Google Doc inside LectureNotes/<Course>/."""
        if not self.service and not self.authenticate():
            return None

        from googleapiclient.http import MediaFileUpload

        p = Path(markdown_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")

        root_id = self._get_or_create_folder(self.root_folder_name)
        course_folder_id = self._get_or_create_folder(course_name, parent_id=root_id)

        file_metadata = {
            "name": doc_title,
            "mimeType": "application/vnd.google-apps.document",  # Converts to Google Docs format!
            "parents": [course_folder_id],
        }

        media = MediaFileUpload(str(p), mimetype="text/markdown", resumable=True)

        # Check if already exists to update
        query = f"name = '{doc_title}' and '{course_folder_id}' in parents and trashed = false"
        res = self.service.files().list(q=query, spaces="drive", fields="files(id)").execute()
        existing = res.get("files", [])

        if existing:
            file_id = existing[0]["id"]
            updated = self.service.files().update(fileId=file_id, media_body=media).execute()
            console.print(f"  ☁️ Google Doc 已更新：{doc_title}")
            return updated.get("id")
        else:
            created = self.service.files().create(body=file_metadata, media_body=media, fields="id").execute()
            console.print(f"  ☁️ Google Doc 已建立：{doc_title}")
            return created.get("id")
