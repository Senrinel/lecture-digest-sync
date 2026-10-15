import subprocess
from pathlib import Path
from lecture_pipeline.preprocess import PreprocessingOptions, preprocess_audio


def generate_synthetic_audio(path: Path, duration_sec: int = 3):
    """Generates a synthetic sine tone audio file using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=1000:duration={duration_sec}",
        "-ar", "44100",
        "-ac", "2",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_preprocess_audio_default_pipeline(tmp_path):
    input_wav = tmp_path / "input.wav"
    output_wav = tmp_path / "output.wav"
    generate_synthetic_audio(input_wav, duration_sec=3)

    opts = PreprocessingOptions(
        hp="100Hz",
        loud="-19LUFS",
        dyn="off",
        nr="off",
    )
    result_path = preprocess_audio(input_wav, output_wav, options=opts)
    assert result_path.exists()
    assert result_path.stat().st_size > 0


def test_preprocess_audio_all_filters(tmp_path):
    input_wav = tmp_path / "input.wav"
    output_wav = tmp_path / "output_all.wav"
    generate_synthetic_audio(input_wav, duration_sec=3)

    opts = PreprocessingOptions(
        hp="80Hz",
        loud="-16LUFS",
        dyn="light",
        nr="6dB",
    )
    result_path = preprocess_audio(input_wav, output_wav, options=opts)
    assert result_path.exists()
    assert result_path.stat().st_size > 0
