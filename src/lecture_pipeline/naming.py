"""Filename parsing and routing module.

Handles standard course naming conventions and Sony ICD-PX470 recorder raw filename exceptions.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel


class AudioMetadata(BaseModel):
    """Metadata parsed from audio filename."""
    original_path: str
    original_filename: str
    course_name: str
    date_str: str          # YYYY-MM-DD normalized
    time_str: str | None = None  # HHMM or HHMMSS
    is_standard: bool
    exception_note: str | None = None


# Patterns
# 1. Standard: CourseName_YYYYMMDD[_HHMM] (e.g., DataMining_20261015, ML-101_20261015_0930)
PATTERN_STANDARD_1 = re.compile(
    r"^([A-Za-z0-9\-]+)_(\d{4})(\d{2})(\d{2})(?:_(\d{2,6}))?$"
)

# 2. Standard with dashes: CourseName_YYYY-MM-DD[_HHMM]
PATTERN_STANDARD_2 = re.compile(
    r"^([A-Za-z0-9\-]+)_(\d{4})-(\d{2})-(\d{2})(?:_(\d{2,6}))?$"
)

# 3. Sony ICD-PX470 raw recording name: YYMMDD_HHMM (e.g., 261015_0930)
PATTERN_SONY_RAW = re.compile(
    r"^(\d{2})(\d{2})(\d{2})_(\d{4})$"
)

# 4. Long raw timestamp: YYYYMMDD_HHMMSS (e.g., 20261015_093000)
PATTERN_RAW_TIMESTAMP = re.compile(
    r"^(\d{4})(\d{2})(\d{2})_(\d{4,6})$"
)


def parse_audio_filename(file_path: str | Path) -> AudioMetadata:
    """Parse an audio filename and extract course and date information.

    Args:
        file_path: Path to the audio file.

    Returns:
        AudioMetadata object containing extracted info and validity flags.
    """
    path = Path(file_path)
    stem = path.stem
    orig_path_str = str(path.resolve())

    # Try standard pattern 1 (Course_YYYYMMDD[_HHMM])
    m = PATTERN_STANDARD_1.match(stem)
    if m:
        course, y, mo, d, t = m.groups()
        return AudioMetadata(
            original_path=orig_path_str,
            original_filename=path.name,
            course_name=course,
            date_str=f"{y}-{mo}-{d}",
            time_str=t,
            is_standard=True,
            exception_note=None,
        )

    # Try standard pattern 2 (Course_YYYY-MM-DD[_HHMM])
    m = PATTERN_STANDARD_2.match(stem)
    if m:
        course, y, mo, d, t = m.groups()
        return AudioMetadata(
            original_path=orig_path_str,
            original_filename=path.name,
            course_name=course,
            date_str=f"{y}-{mo}-{d}",
            time_str=t,
            is_standard=True,
            exception_note=None,
        )

    # Try Sony raw format (YYMMDD_HHMM)
    m = PATTERN_SONY_RAW.match(stem)
    if m:
        yy, mo, d, t = m.groups()
        year = f"20{yy}"
        return AudioMetadata(
            original_path=orig_path_str,
            original_filename=path.name,
            course_name="Unsorted",
            date_str=f"{year}-{mo}-{d}",
            time_str=t,
            is_standard=False,
            exception_note="Sony recorder raw filename format (YYMMDD_HHMM). Course set to 'Unsorted'.",
        )

    # Try full timestamp format (YYYYMMDD_HHMMSS)
    m = PATTERN_RAW_TIMESTAMP.match(stem)
    if m:
        y, mo, d, t = m.groups()
        return AudioMetadata(
            original_path=orig_path_str,
            original_filename=path.name,
            course_name="Unsorted",
            date_str=f"{y}-{mo}-{d}",
            time_str=t,
            is_standard=False,
            exception_note="Timestamp filename without course prefix. Course set to 'Unsorted'.",
        )

    # Fallback: extract date from file modification time
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        fallback_date = mtime.strftime("%Y-%m-%d")
        fallback_time = mtime.strftime("%H%M")
    except Exception:
        fallback_date = datetime.now().strftime("%Y-%m-%d")
        fallback_time = None

    return AudioMetadata(
        original_path=orig_path_str,
        original_filename=path.name,
        course_name="Unsorted",
        date_str=fallback_date,
        time_str=fallback_time,
        is_standard=False,
        exception_note="Non-standard filename. Extracted date from filesystem metadata.",
    )


def get_work_directory(base_work_dir: str | Path, meta: AudioMetadata) -> Path:
    """Compute the dedicated working directory for an audio file.

    Format: <base_work_dir>/<Course>/<Date>/
    """
    base = Path(base_work_dir)
    return base / meta.course_name / meta.date_str
