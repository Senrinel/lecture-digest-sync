"""Unit tests for preprocess.py."""

import subprocess
import pytest
from pathlib import Path
from lecture_pipeline.preprocess import (
    PreprocessOptions,
    build_ffmpeg_filter_chain,
    preprocess_audio,
)
from lecture_pipeline.vad_segment import get_audio_duration


def test_options_from_string():
    # Empty / off
    opts = PreprocessOptions.from_string("off")
    assert opts.hp == "off"
    assert opts.loud == "off"

    # Default triggers
    opts = PreprocessOptions.from_string("hp,loud")
    assert opts.hp == "100Hz"
    assert opts.loud == "-19LUFS"
    assert opts.dyn == "off"

    # Custom parameter overrides
    opts = PreprocessOptions.from_string("hp:80Hz,loud:-16LUFS,dyn:light,nr:6dB")
    assert opts.hp == "80Hz"
    assert opts.loud == "-16LUFS"
    assert opts.dyn == "light"
    assert opts.nr == "6dB"


def test_build_filter_chain():
    opts = PreprocessOptions(hp="100Hz", loud="-19LUFS", dyn="off", nr="off")
    chain = build_ffmpeg_filter_chain(opts)
    assert any("highpass=f=100" in f for f in chain)
    assert any("loudnorm=I=-19" in f for f in chain)
    assert not any("afftdn" in f for f in chain)


def test_actual_preprocessing_with_synthetic_audio(tmp_path):
    # Generate 3-second synthetic sine wave audio with ffmpeg
    raw_audio = tmp_path / "raw.mp3"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=3",
            str(raw_audio),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    out_wav = tmp_path / "processed.wav"
    opts = PreprocessOptions(hp="100Hz", loud="-19LUFS")
    result_path, elapsed = preprocess_audio(raw_audio, out_wav, opts)

    assert result_path.exists()
    assert result_path.stat().st_size > 0
    dur = get_audio_duration(result_path)
    # Duration should be approximately 3 seconds
    assert 2.8 <= dur <= 3.2
