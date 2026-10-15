import subprocess
from pathlib import Path
from lecture_pipeline.orchestrator import LecturePipelineOrchestrator
from lecture_pipeline.preprocess import PreprocessingOptions
from lecture_pipeline.stt.base import STTChannelBase, STTChunkResult
from lecture_pipeline.stt import register_channel


class MockSTTChannel(STTChannelBase):
    @property
    def channel_id(self) -> str:
        return "MOCK"

    @property
    def display_name(self) -> str:
        return "Mock STT Channel"

    def transcribe_chunk(
        self,
        audio_path,
        chunk_index,
        start_sec,
        end_sec,
        course_name="",
        glossary=None,
        prompt_style="verbatim",
        temperature=0.0,
    ) -> STTChunkResult:
        return STTChunkResult(
            chunk_index=chunk_index,
            text=f"這是第 {chunk_index} 個片段的模擬逐字稿測試。",
            raw_text=f"这是第 {chunk_index} 个片段的模拟测试。",
            channel_name=self.channel_id,
            model_name="mock-v1",
            latency_sec=0.1,
            start_sec=start_sec,
            end_sec=end_sec,
        )


def generate_test_audio(path: Path):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=4",
        "-ar", "16000",
        "-ac", "1",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_orchestrator_end_to_end_mvp(tmp_path):
    register_channel("MOCK", MockSTTChannel)

    test_audio = tmp_path / "MachineLearning_20261015.wav"
    generate_test_audio(test_audio)

    # Custom config for test
    cfg_path = tmp_path / "test_config.yaml"
    posix_tmp = tmp_path.as_posix()
    cfg_content = f"""
paths:
  inbox_dir: "{posix_tmp}/inbox"
  work_dir: "{posix_tmp}/work"
  archive_dir: "{posix_tmp}/archive"
  logs_dir: "{posix_tmp}/logs"
  benchmark_dir: "{posix_tmp}/benchmark"
  prompts_dir: "prompts"
  courses_dir: "courses"

preprocessing:
  hp: "100Hz"
  loud: "-19LUFS"
  dyn: "off"
  nr: "off"

segmentation:
  target_chunk_sec: 10
  search_window_sec: 2
  silence_thresh_db: -40
  min_silence_sec: 0.5

stt:
  default_channel: "MOCK"
  prompt_style: "verbatim"
  temperature: 0.0
"""
    cfg_path.write_text(cfg_content, encoding="utf-8")

    orchestrator = LecturePipelineOrchestrator(config_path=cfg_path)
    raw_txt_path = orchestrator.process_file(
        audio_path=test_audio,
        channel_id="MOCK",
    )

    assert raw_txt_path.exists()
    content = raw_txt_path.read_text(encoding="utf-8")
    assert "逐字稿測試" in content

    target_work_dir = tmp_path / "work" / "MachineLearning" / "20261015"
    assert (target_work_dir / "00_preprocessed.wav").exists()
    assert (target_work_dir / "chunks" / "chunks_manifest.json").exists()
    assert (target_work_dir / "01_raw.json").exists()
    assert (target_work_dir / "run.json").exists()
    assert (target_work_dir / "state.json").exists()
