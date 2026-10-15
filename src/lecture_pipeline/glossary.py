"""Course glossary and custom vocabulary management module."""

from __future__ import annotations

from pathlib import Path
from typing import List


def load_course_glossary(
    course: str,
    courses_dir: Path | str = "courses",
) -> List[str]:
    """Load technical terminology and proper nouns for a given course.

    Looks for `courses/<course>/glossary.txt`.
    File format: one term per line. Lines starting with '#' are ignored.

    Args:
        course: Name of the course (e.g. 'DataMining', 'Machine-Learning').
        courses_dir: Base directory for course metadata.

    Returns:
        List of distinct glossary phrases (stripped, non-empty).
    """
    course_folder = Path(courses_dir) / course
    glossary_file = course_folder / "glossary.txt"

    if not glossary_file.is_file():
        return []

    terms: List[str] = []
    seen = set()

    with open(glossary_file, "r", encoding="utf-8") as f:
        for line in f:
            # Strip inline comments and whitespace
            cleaned = line.split("#", 1)[0].strip()
            if not cleaned:
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                terms.append(cleaned)

    return terms

