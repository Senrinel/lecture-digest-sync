"""Unit tests for Phase 5 Google Drive Publisher (Specification Section 9)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.export.drive_publisher import (
    GoogleAuthRequiredError,
    GoogleDrivePublisher,
)
from lecture_pipeline.state import PreprocessOptions, RunState, SegmentationOptions, save_run_state


@pytest.fixture
def mock_work_dir(tmp_path: Path) -> Path:
    """Create a realistic work directory containing Phase 3 & 4 outputs."""
    work_dir = tmp_path / "work" / "Discrete_Math" / "20261015"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. State
    state = RunState(
        source_filename="Discrete_Math_20261015.mp3",
        source_path=str(work_dir / "raw.mp3"),
        course="Discrete_Math",
        date="20261015",
        pre_options=PreprocessOptions(),
        seg_options=SegmentationOptions(),
        stage_status={"summary": "DONE", "cleanup": "DONE"},
    )
    save_run_state(state, work_dir)

    # 2. Files
    (work_dir / "02_cleaned.txt").write_text("# 完整整理課堂\n\n今天講解圖論與最短路徑演算法。", encoding="utf-8")
    (work_dir / "03_summary.md").write_text("# 考前重點整理\n\n- Dijkstra 演算法 $O(V^2)$", encoding="utf-8")

    return work_dir


def test_publisher_missing_credentials(tmp_path: Path):
    """Verify informative error if credentials.json is missing."""
    publisher = GoogleDrivePublisher(
        credentials_file=tmp_path / "non_existent_creds.json",
        token_file=tmp_path / "non_existent_token.json",
    )
    with pytest.raises(FileNotFoundError, match="找不到 Google OAuth 用戶端憑證"):
        publisher.get_service()


@patch("lecture_pipeline.export.drive_publisher.build")
@patch("lecture_pipeline.export.drive_publisher.Credentials")
def test_publisher_get_or_create_folder(mock_creds_cls, mock_build, tmp_path: Path):
    """Verify querying and creating folders on Google Drive."""
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")

    mock_service = MagicMock()
    mock_build.return_value = mock_service

    # Case 1: Folder already exists
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "folder_123", "name": "LectureNotes"}]
    }

    publisher = GoogleDrivePublisher(
        credentials_file=tmp_path / "creds.json",
        token_file=token_file,
    )
    folder_id = publisher.get_or_create_folder("LectureNotes")
    assert folder_id == "folder_123"

    # Case 2: Folder does not exist -> Create new
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.return_value = {"id": "new_folder_456"}

    created_id = publisher.get_or_create_folder("Discrete_Math", parent_id="folder_123")
    assert created_id == "new_folder_456"


@patch("lecture_pipeline.export.drive_publisher.build")
@patch("lecture_pipeline.export.drive_publisher.Credentials")
def test_publisher_upload_or_update_doc(mock_creds_cls, mock_build, tmp_path: Path):
    """Verify creating and updating Google Docs with conversion."""
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")

    test_md = tmp_path / "notes.md"
    test_md.write_text("# 重點筆記", encoding="utf-8")

    mock_service = MagicMock()
    mock_build.return_value = mock_service

    publisher = GoogleDrivePublisher(
        credentials_file=tmp_path / "creds.json",
        token_file=token_file,
    )

    # 1. New document creation
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.return_value = {
        "id": "doc_new_001",
        "name": "Discrete_Math_重點筆記",
        "webViewLink": "https://docs.google.com/document/d/doc_new_001/edit",
    }

    info_created = publisher.upload_or_update_doc(test_md, "Discrete_Math_重點筆記", "parent_1")
    assert info_created.status == "CREATED"
    assert info_created.file_id == "doc_new_001"
    assert "https://docs.google.com/document/d/doc_new_001/edit" in info_created.web_link

    # 2. Existing document update in-place
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "doc_existing_002", "name": "Discrete_Math_重點筆記"}]
    }
    mock_service.files().update().execute.return_value = {
        "id": "doc_existing_002",
        "name": "Discrete_Math_重點筆記",
        "webViewLink": "https://docs.google.com/document/d/doc_existing_002/edit",
    }

    info_updated = publisher.upload_or_update_doc(test_md, "Discrete_Math_重點筆記", "parent_1")
    assert info_updated.status == "UPDATED"
    assert info_updated.file_id == "doc_existing_002"


@patch("lecture_pipeline.export.drive_publisher.build")
@patch("lecture_pipeline.export.drive_publisher.Credentials")
def test_publish_lecture_bundle_e2e(mock_creds_cls, mock_build, mock_work_dir: Path, tmp_path: Path):
    """Verify end-to-end publishing of bundle to Google Drive."""
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")

    mock_service = MagicMock()
    mock_build.return_value = mock_service

    # Setup folder mocks
    mock_service.files().list().execute.side_effect = [
        {"files": [{"id": "root_f_id", "name": "LectureNotes"}]},  # root folder list
        {"files": [{"id": "course_f_id", "name": "Discrete_Math"}]},  # course folder list
        {"files": []},  # summary doc check
        {"files": []},  # cleaned doc check
    ]
    mock_service.files().create().execute.side_effect = [
        {
            "id": "doc_sum_1",
            "name": "Discrete_Math_2026-10-15_重點總結筆記",
            "webViewLink": "https://docs.google.com/document/d/doc_sum_1/edit",
        },
        {
            "id": "doc_clean_2",
            "name": "Discrete_Math_2026-10-15_完整課堂整理文稿",
            "webViewLink": "https://docs.google.com/document/d/doc_clean_2/edit",
        },
    ]

    publisher = GoogleDrivePublisher(
        credentials_file=tmp_path / "creds.json",
        token_file=token_file,
    )
    result = publisher.publish_lecture(mock_work_dir)

    assert result.course == "Discrete_Math"
    assert result.date == "20261015"
    assert result.course_folder_id == "course_f_id"
    assert len(result.docs) == 2
    assert result.docs[0].file_id == "doc_sum_1"
    assert result.docs[1].file_id == "doc_clean_2"


@patch("lecture_pipeline.export.drive_publisher._run_local_server_with_display")
@patch("lecture_pipeline.export.drive_publisher.Credentials")
def test_publisher_non_interactive_raises_without_opening_browser(mock_creds_cls, mock_local_srv, tmp_path: Path):
    """Verify interactive=False immediately raises GoogleAuthRequiredError without starting OAuth server."""
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"installed":{"client_id":"abc","client_secret":"def"}}', encoding="utf-8")
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token":"x"}', encoding="utf-8")

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "old_refresh"
    fake_creds.refresh.side_effect = RuntimeError("invalid_grant: Bad Request")
    mock_creds_cls.from_authorized_user_file.return_value = fake_creds

    publisher = GoogleDrivePublisher(
        credentials_file=creds_file,
        token_file=token_file,
        interactive=False,
    )
    with pytest.raises(GoogleAuthRequiredError):
        publisher.get_service()

    assert mock_local_srv.call_count == 0


@patch("lecture_pipeline.export.drive_publisher.Credentials")
def test_publisher_check_auth_status_expired_refresh_fails(mock_creds_cls, tmp_path: Path):
    """Verify check_auth_status accurately reports False when refresh token fails with invalid_grant."""
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "revoked"
    fake_creds.refresh.side_effect = RuntimeError("invalid_grant")
    mock_creds_cls.from_authorized_user_file.return_value = fake_creds

    publisher = GoogleDrivePublisher(credentials_file=creds_file, token_file=token_file)
    ok, label = publisher.check_auth_status(attempt_refresh=True)
    assert ok is False
    assert "過期" in label or "授權" in label


def test_offline_queue_aborts_immediately_on_auth_required(tmp_path: Path):
    """Verify OfflineUploadQueue stops retrying remaining items if GoogleAuthRequiredError occurs."""
    from lecture_pipeline.automation.offline_queue import OfflineUploadQueue

    q_dir = tmp_path / "pending_uploads"
    oq = OfflineUploadQueue(queue_dir=q_dir)

    d1 = tmp_path / "work" / "CourseA" / "20261116"
    d2 = tmp_path / "work" / "CourseB" / "20261116"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)

    oq.enqueue(d1, "LectureA.m4a", "CourseA", "20261116")
    oq.enqueue(d2, "LectureB.m4a", "CourseB", "20261116")

    fake_pub = MagicMock()
    fake_pub.publish_lecture.side_effect = GoogleAuthRequiredError("token expired")

    with patch("lecture_pipeline.automation.offline_queue.is_internet_connected", return_value=True):
        cnt = oq.process_pending_uploads(fake_pub)

    assert cnt == 0
    # Crucial: aborted after first item without blocking or looping into item 2
    assert fake_pub.publish_lecture.call_count == 1
    assert len(oq.get_pending()) == 2
