"""Unit tests for pipeline configuration and run state management."""

from pathlib import Path
from lecture_pipeline.state import (
    ChunkInfo,
    PreprocessOptions,
    RunState,
    SegmentationOptions,
    load_run_state,
    save_run_state,
)


def test_preprocess_options_from_cli_flags():
    """Verify CLI flag parsing into typed options."""
    opts = PreprocessOptions.from_cli_flags("hp,loud")
    assert opts.hp == "100Hz"
    assert opts.loud == "-19LUFS"
    assert opts.dyn == "off"
    assert opts.nr == "off"

    # Explicit values
    opts_explicit = PreprocessOptions.from_cli_flags("hp=120Hz,loud=-16LUFS,dyn=light,nr=6dB")
    assert opts_explicit.hp == "120Hz"
    assert opts_explicit.loud == "-16LUFS"
    assert opts_explicit.dyn == "light"
    assert opts_explicit.nr == "6dB"


def test_save_and_load_run_state(tmp_path: Path):
    """Verify serialization and deserialization of run.json."""
    chunks = [
        ChunkInfo(
            index=1,
            filename="chunk_001.wav",
            filepath=str(tmp_path / "chunk_001.wav"),
            start_sec=0.0,
            end_sec=600.5,
            duration_sec=600.5,
        ),
        ChunkInfo(
            index=2,
            filename="chunk_002.wav",
            filepath=str(tmp_path / "chunk_002.wav"),
            start_sec=600.5,
            end_sec=1210.0,
            duration_sec=609.5,
        ),
    ]

    state = RunState(
        source_filename="DataMining_20261015.mp3",
        source_path="/path/to/DataMining_20261015.mp3",
        course="DataMining",
        date="20261015",
        pre_options=PreprocessOptions(hp="100Hz", loud="-19LUFS"),
        seg_options=SegmentationOptions(target_chunk_sec=600),
        chunks=chunks,
        stage_status={"preprocess": "DONE", "vad_segment": "DONE"},
    )

    state_file = save_run_state(state, tmp_path)
    assert state_file.is_file()

    loaded = load_run_state(tmp_path)
    assert loaded is not None
    assert loaded.run_id == state.run_id
    assert loaded.course == "DataMining"
    assert len(loaded.chunks) == 2
    assert loaded.chunks[0].filename == "chunk_001.wav"
    assert loaded.chunks[1].duration_sec == 609.5
    assert loaded.stage_status["preprocess"] == "DONE"
