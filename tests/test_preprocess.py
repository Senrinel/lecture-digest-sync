"""Unit tests for the audio preprocessing module."""

import math
import struct
import wave
from pathlib import Path
from lecture_pipeline.preprocess import build_ffmpeg_audio_filters, preprocess_audio
from lecture_pipeline.state import PreprocessOptions


def create_synthetic_wav(filepath: Path, duration_sec: float = 2.0, sample_rate: int = 44100):
    """Generate a clean synthetic sine-wave WAV file for testing."""
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(filepath), "w") as wav_file:
        wav_file.setnchannels(2)  # Stereo
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            # 440Hz tone
            val = int(10000 * math.sin(2 * math.pi * 440 * (i / sample_rate)))
            packed = struct.pack("<hh", val, val)
            frames.extend(packed)
        wav_file.writeframes(frames)


def test_build_ffmpeg_audio_filters():
    """Verify correct FFmpeg audio filter chain construction."""
    opts = PreprocessOptions(hp="100Hz", loud="-19LUFS", dyn="standard", nr="12dB", vadtrim="on")
    filters = build_ffmpeg_audio_filters(opts)

    assert "highpass=f=100" in filters
    assert "dynaudnorm=f=1000:g=31:p=0.95" in filters
    assert "afftdn=nr=12:nf=-25" in filters
    assert "loudnorm=I=-19:TP=-1.5:LRA=11" in filters
    assert any("silenceremove" in f for f in filters)


def test_build_ffmpeg_audio_filters_all_off():
    """Verify empty filter list when all options are off."""
    opts = PreprocessOptions(hp="off", loud="off", dyn="off", nr="off", vadtrim="off")
    filters = build_ffmpeg_audio_filters(opts)
    assert filters == []


def test_preprocess_audio_execution(tmp_path: Path):
    """Verify actual execution of FFmpeg on synthetic audio."""
    raw_wav = tmp_path / "raw_stereo.wav"
    out_wav = tmp_path / "preprocessed.wav"

    create_synthetic_wav(raw_wav, duration_sec=1.5, sample_rate=44100)
    assert raw_wav.is_file()

    opts = PreprocessOptions(hp="100Hz", loud="-19LUFS", dyn="off", nr="off")
    processed = preprocess_audio(raw_wav, out_wav, opts=opts)

    assert processed.is_file()
    assert processed.stat().st_size > 0

    # Verify output audio format is 16kHz mono 16-bit PCM
    with wave.open(str(processed), "rb") as w:
        assert w.getnchannels() == 1  # Mono
        assert w.getframerate() == 16000  # 16kHz
        assert w.getsampwidth() == 2  # 16-bit
