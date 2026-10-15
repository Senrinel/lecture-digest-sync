"""Unit tests for course glossary loading."""

from pathlib import Path
from lecture_pipeline.glossary import load_course_glossary


def test_load_course_glossary_missing_file(tmp_path: Path):
    """Verify empty list returned when glossary.txt does not exist."""
    terms = load_course_glossary("NonExistentCourse", courses_dir=tmp_path)
    assert terms == []


def test_load_course_glossary_with_comments_and_blanks(tmp_path: Path):
    """Verify comments and blank lines are ignored, and duplicates are removed."""
    course_dir = tmp_path / "DataMining"
    course_dir.mkdir()
    glossary_file = course_dir / "glossary.txt"

    content = """# Data Mining Terminology
Transformer
Attention Mechanism

# Evaluation Metrics
Precision
Recall
F1-Score
Precision  # Duplicate
"""
    glossary_file.write_text(content, encoding="utf-8")

    terms = load_course_glossary("DataMining", courses_dir=tmp_path)
    assert terms == ["Transformer", "Attention Mechanism", "Precision", "Recall", "F1-Score"]
