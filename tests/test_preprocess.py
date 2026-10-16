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


def test_preprocess_audio_multi_part_concat(tmp_path: Path):
    """Verify concatenation of multiple audio parts into a single normalized WAV."""
    part1 = tmp_path / "lecture_part1.wav"
    part2 = tmp_path / "lecture_part2.wav"
    out_wav = tmp_path / "concatenated_out.wav"

    create_synthetic_wav(part1, duration_sec=1.0, sample_rate=44100)
    create_synthetic_wav(part2, duration_sec=1.5, sample_rate=44100)

    processed = preprocess_audio([part1, part2], out_wav)
    assert processed.is_file()

    with wave.open(str(processed), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        duration = w.getnframes() / w.getframerate()
        assert 2.3 <= duration <= 2.7


def test_preprocess_flac_companion_export(tmp_path: Path):
    """Verify generation of companion FLAC file for cloud API upload."""
    raw_wav = tmp_path / "raw.wav"
    out_wav = tmp_path / "norm.wav"
    create_synthetic_wav(raw_wav, duration_sec=1.0)

    processed = preprocess_audio(raw_wav, out_wav, export_flac=True)
    assert processed.is_file()

    flac_file = out_wav.with_suffix(".flac")
    assert flac_file.is_file()
    assert flac_file.stat().st_size > 0


def test_preprocess_neural_enhancement_switches(tmp_path: Path):
    """Verify execution of neural enhancement pipeline with wpe, dfn, se, vadtrim."""
    raw_wav = tmp_path / "speech_test.wav"
    out_wav = tmp_path / "enhanced.wav"
    create_synthetic_wav(raw_wav, duration_sec=2.0)

    opts = PreprocessOptions(hp="100Hz", loud="-19LUFS", wpe="on", dfn="on", se="on", vadtrim="on")
    processed = preprocess_audio(raw_wav, out_wav, opts=opts)

    assert processed.is_file()
    assert processed.stat().st_size > 0

    with wave.open(str(processed), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000


