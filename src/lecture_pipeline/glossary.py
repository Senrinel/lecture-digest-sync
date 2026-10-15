"""Course glossary loader.

Loads domain-specific terminology and keywords from courses/<CourseName>/glossary.txt.
"""

from pathlib import Path
from typing import List


def load_course_glossary(course_name: str, courses_dir: str | Path = "courses") -> List[str]:
    """Loads a list of custom vocabulary terms for a course."""
    glossary_path = Path(courses_dir) / course_name / "glossary.txt"
    if not glossary_path.exists():
        return []

    terms = []
    with open(glossary_path, "r", encoding="utf-8") as f:
        for line in f:
            term = line.strip()
            # Ignore empty lines and comments
            if term and not term.startswith("#"):
                terms.append(term)
    return terms
