"""Unit tests for Phase 5 Google Drive Publisher (Specification Section 9)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.export.drive_publisher import GoogleDrivePublisher
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
