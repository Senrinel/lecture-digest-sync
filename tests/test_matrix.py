"""Unit tests for the multi-dimensional benchmark test matrix."""

import math
import struct
import wave
from pathlib import Path
from lecture_pipeline.bench.matrix import (
    MatrixBenchmarkRunner,
    MatrixExperimentResult,
    extract_test_clip,
)


def create_synthetic_wav(filepath: Path, duration_sec: float = 2.0, sample_rate: int = 16000):
    """Generate a clean synthetic sine-wave WAV file for testing."""
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(filepath), "w") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            val = int(10000 * math.sin(2 * math.pi * 440 * (i / sample_rate)))
            packed = struct.pack("<h", val)
            frames.extend(packed)
        wav_file.writeframes(frames)



def test_extract_test_clip(tmp_path: Path):
    """Verify slicing of standardized evaluation clip."""
    src_wav = tmp_path / "long_lecture.wav"
    out_clip = tmp_path / "eval_clip.wav"
    create_synthetic_wav(src_wav, duration_sec=5.0, sample_rate=16000)

    extract_test_clip(src_wav, out_clip, start_sec=1.0, duration_sec=2.0)
    assert out_clip.is_file()
    assert out_clip.stat().st_size > 0


def test_matrix_runner_export(tmp_path: Path):
    """Verify CSV and HTML report export functions."""
    src_wav = tmp_path / "Discrete Mathematics 06.wav"
    create_synthetic_wav(src_wav, duration_sec=4.0, sample_rate=16000)

    runner = MatrixBenchmarkRunner(
        audio_file=src_wav,
        clip_sec=2.0,
        start_sec=0.5,
        base_dir=tmp_path / "matrix_base",
    )

    runner.results.append(
        MatrixExperimentResult(
            experiment_id="TEST_PRE_HP",
            dimension="Preprocess",
            factor_name="前處理組合",
            factor_value="高通濾波 (hp=100Hz)",
            duration_sec=1.2,
            char_count=150,
            rtf=0.03,
            cer=0.05,
            accuracy=0.95,
            term_recall=0.80,
            notes="hp=100Hz",
            preview="這是測試預覽文字",
            full_output="這是測試預覽文字完整版本。",
        )
    )

    csv_out = tmp_path / "results.csv"
    html_out = tmp_path / "report.html"

    runner.export_csv(csv_out)
    runner.export_html(html_out)

    assert csv_out.is_file()
    assert "TEST_PRE_HP" in csv_out.read_text(encoding="utf-8-sig")

    assert html_out.is_file()
    html_content = html_out.read_text(encoding="utf-8")
    assert "TEST_PRE_HP" in html_content
    assert "高通濾波" in html_content
