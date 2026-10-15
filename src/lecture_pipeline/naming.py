"""Audio file naming parser and course organizer.

Handles naming conventions:
- Standard: CourseName_YYYYMMDD[_HHMM].ext
- Recorder raw: e.g., 261015_0930.mp3 -> Date 20261015, Course: Unsorted
- Fallback: Uses file modification time and filename without ext as course.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ParsedAudioInfo:
    file_path: Path
    course_name: str
    date_str: str  # YYYYMMDD
    time_str: str  # HHMM or empty
    is_standard_name: bool
    warning: str = ""

    @property
    def canonical_id(self) -> str:
        """Returns standard directory identifier: CourseName/YYYYMMDD or CourseName/YYYYMMDD_HHMM."""
        if self.time_str:
            return f"{self.course_name}/{self.date_str}_{self.time_str}"
        return f"{self.course_name}/{self.date_str}"


def parse_audio_filename(file_path: str | Path) -> ParsedAudioInfo:
    """Parses an audio file name and extracts course name and date/time."""
    p = Path(file_path)
    stem = p.stem.strip()

    # Pattern 1: Standard CourseName_YYYYMMDD or CourseName_YYYYMMDD_HHMM
    # e.g., MachineLearning_20261015, Algorithms_20261015_0930
    std_pattern = r"^([a-zA-Z0-9\-]+)_(\d{8})(?:_(\d{4}))?$"
    m_std = re.match(std_pattern, stem)
    if m_std:
        course = m_std.group(1)
        date_str = m_std.group(2)
        time_str = m_std.group(3) or ""
        return ParsedAudioInfo(
            file_path=p,
            course_name=course,
            date_str=date_str,
            time_str=time_str,
            is_standard_name=True,
        )

    # Pattern 2: Sony ICD-PX470 / generic recorder raw name: YYMMDD_HHMM or YYYYMMDD_HHMM
    # e.g., 261015_0930 or 20261015_0930
    rec_pattern = r"^(\d{2}|\d{4})(\d{2})(\d{2})_(\d{4})$"
    m_rec = re.match(rec_pattern, stem)
    if m_rec:
        yr = m_rec.group(1)
        if len(yr) == 2:
            full_year = f"20{yr}"
        else:
            full_year = yr
        mm = m_rec.group(2)
        dd = m_rec.group(3)
        time_str = m_rec.group(4)
        date_str = f"{full_year}{mm}{dd}"
        return ParsedAudioInfo(
            file_path=p,
            course_name="Unsorted",
            date_str=date_str,
            time_str=time_str,
            is_standard_name=False,
            warning=f"非標準檔名，已識別錄音日期 {date_str}，暫歸類為 Unsorted 課程。",
        )

    # Pattern 3: Fallback using file modification time
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        fallback_date = mtime.strftime("%Y%m%d")
        fallback_time = mtime.strftime("%H%M")
    except Exception:
        now = datetime.now()
        fallback_date = now.strftime("%Y%m%d")
        fallback_time = now.strftime("%H%M")

    # Sanitize stem for folder safety
    safe_course = re.sub(r"[^a-zA-Z0-9_\-]", "_", stem) or "Unsorted"

    return ParsedAudioInfo(
        file_path=p,
        course_name=safe_course,
        date_str=fallback_date,
        time_str=fallback_time,
        is_standard_name=False,
        warning=f"無法解析檔名格式 '{p.name}'，採用檔案修改時間 {fallback_date} 作為日期標記。",
    )
