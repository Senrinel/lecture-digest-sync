"""Main Pipeline Orchestrator.

Executes the pipeline stages:
1. Audio file ingestion and name parsing
2. Audio preprocessing (independent filter switches)
3. Conservative VAD chunking
4. STT transcription (Channel selection, e.g. C1 / C2)
5. Merging into 01_raw.txt and 01_raw.json
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from lecture_pipeline.glossary import load_course_glossary
from lecture_pipeline.naming import parse_audio_filename
from lecture_pipeline.preprocess import PreprocessingOptions, preprocess_audio
from lecture_pipeline.segmentation import chunk_audio_file
from lecture_pipeline.state import PipelineRunState
from lecture_pipeline.stt import get_stt_channel

console = Console()


class LecturePipelineOrchestrator:
    def __init__(self, config_path: str | Path = "config.yaml"):
        self.config_path = Path(config_path)
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

    def process_file(
        self,
        audio_path: str | Path,
        channel_id: Optional[str] = None,
        custom_pre_options: Optional[PreprocessingOptions] = None,
    ) -> Path:
        """Processes an audio file through MVP stages (Preprocessing -> Chunking -> STT -> 01_raw.txt)."""
        input_audio = Path(audio_path).resolve()
        if not input_audio.exists():
            raise FileNotFoundError(f"Input file not found: {input_audio}")

        # 1. Parse naming
        info = parse_audio_filename(input_audio)
        if info.warning:
            console.print(f"[yellow]⚠️ {info.warning}[/yellow]")

        work_base = Path(self.config["paths"]["work_dir"])
        target_work_dir = work_base / info.course_name / info.date_str
        if info.time_str:
            target_work_dir = target_work_dir / info.time_str
        target_work_dir.mkdir(parents=True, exist_ok=True)

        # State tracking
        state = PipelineRunState.load_or_create(
            work_dir=target_work_dir,
            audio_file=input_audio,
            course_name=info.course_name,
            date_str=info.date_str,
            config=self.config,
        )

        console.print(f"\n[bold green]🚀 開始處理課程：{info.course_name} (日期: {info.date_str})[/bold green]")
        console.print(f"檔案路徑：{input_audio}")
        console.print(f"工作目錄：{target_work_dir}\n")

        # 2. Stage: Preprocessing
        preprocessed_wav = target_work_dir / "00_preprocessed.wav"
        pre_opts = custom_pre_options or PreprocessingOptions.from_dict(self.config.get("preprocessing", {}))

        if not state.is_stage_completed("preprocess") or not preprocessed_wav.exists():
            console.print("[cyan]⚙️ [步驟 1/3] 音訊前處理 (濾波 / 響度正規化 / 降噪)...[/cyan]")
            state.mark_stage_start("preprocess")
            preprocess_audio(input_audio, preprocessed_wav, options=pre_opts)
            state.mark_stage_completed("preprocess", {
                "output_file": str(preprocessed_wav),
                "options": pre_opts.to_dict(),
            })
            console.print(f"  ✅ 前處理完成：{preprocessed_wav.name}")
        else:
            console.print(f"[dim]⏩ [步驟 1/3] 已完成前處理，跳過：{preprocessed_wav.name}[/dim]")

        # 3. Stage: Conservative VAD Chunking
        chunks_dir = target_work_dir / "chunks"
        manifest_file = chunks_dir / "chunks_manifest.json"

        if not state.is_stage_completed("chunking") or not manifest_file.exists():
            console.print("[cyan]✂️ [步驟 2/3] 智慧 VAD 靜音切段 (目標 10~15 分鐘)...[/cyan]")
            state.mark_stage_start("chunking")
            seg_cfg = self.config.get("segmentation", {})
            chunks = chunk_audio_file(
                input_audio=preprocessed_wav,
                output_dir=chunks_dir,
                target_chunk_sec=float(seg_cfg.get("target_chunk_sec", 600)),
                search_window_sec=float(seg_cfg.get("search_window_sec", 30)),
                silence_thresh_db=int(seg_cfg.get("silence_thresh_db", -40)),
                min_silence_sec=float(seg_cfg.get("min_silence_sec", 0.5)),
            )
            state.mark_stage_completed("chunking", {
                "total_chunks": len(chunks),
                "manifest": str(manifest_file),
            })
            console.print(f"  ✅ 切段完成：共切為 {len(chunks)} 個語音片段")
        else:
            with open(manifest_file, "r", encoding="utf-8") as f:
                chunks_data = json.load(f)
            console.print(f"[dim]⏩ [步驟 2/3] 已完成切段，跳過（共 {len(chunks_data)} 個片段）[/dim]")

        # 4. Stage: STT Transcription
        raw_txt_path = target_work_dir / "01_raw.txt"
        raw_json_path = target_work_dir / "01_raw.json"
        active_channel_id = channel_id or self.config.get("stt", {}).get("default_channel", "C1")

        if not state.is_stage_completed("stt") or not raw_txt_path.exists():
            console.print(f"[cyan]🎙️ [步驟 3/3] 執行語音辨識 (通道: {active_channel_id})...[/cyan]")
            state.mark_stage_start("stt")

            with open(manifest_file, "r", encoding="utf-8") as f:
                chunks_info = json.load(f)

            channel = get_stt_channel(active_channel_id)
            glossary = load_course_glossary(info.course_name, self.config["paths"]["courses_dir"])
            stt_cfg = self.config.get("stt", {})

            results: List[Dict[str, Any]] = []
            full_text_parts: List[str] = []

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(f"辨識進度 ({len(chunks_info)} 個片段)...", total=len(chunks_info))

                for item in chunks_info:
                    c_idx = item["index"]
                    c_path = item["file_path"]
                    c_start = item["start_sec"]
                    c_end = item["end_sec"]

                    chunk_res = channel.transcribe_chunk(
                        audio_path=c_path,
                        chunk_index=c_idx,
                        start_sec=c_start,
                        end_sec=c_end,
                        course_name=info.course_name,
                        glossary=glossary,
                        prompt_style=stt_cfg.get("prompt_style", "verbatim"),
                        temperature=float(stt_cfg.get("temperature", 0.0)),
                    )

                    results.append({
                        "index": chunk_res.chunk_index,
                        "start_sec": chunk_res.start_sec,
                        "end_sec": chunk_res.end_sec,
                        "model": chunk_res.model_name,
                        "latency_sec": chunk_res.latency_sec,
                        "text": chunk_res.text,
                    })

                    full_text_parts.append(chunk_res.text)
                    progress.advance(task)

            # Save raw outputs
            with open(raw_json_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)

            merged_text = "\n\n".join(full_text_parts)
            with open(raw_txt_path, "w", encoding="utf-8") as f:
                f.write(merged_text)

            state.mark_stage_completed("stt", {
                "channel": active_channel_id,
                "raw_txt": str(raw_txt_path),
                "raw_json": str(raw_json_path),
                "total_chars": len(merged_text),
            })

            console.print(f"  ✅ 語音轉錄完成！共辨識 {len(merged_text)} 字")
            console.print(f"  📄 逐字稿輸出檔案：[bold yellow]{raw_txt_path}[/bold yellow]")
        else:
            console.print(f"[dim]⏩ [步驟 3/3] 已完成語音辨識，跳過：{raw_txt_path.name}[/dim]")

        # 5. Save run variable matrix snapshot
        run_record = {
            "run_id": state.run_id,
            "timestamp": time.time(),
            "course": info.course_name,
            "date": info.date_str,
            "channel": active_channel_id,
            "preprocessing": pre_opts.to_dict(),
            "raw_text_path": str(raw_txt_path),
            "stages": {k: v.duration_sec for k, v in state.stages.items()},
        }
        with open(target_work_dir / "run.json", "w", encoding="utf-8") as f:
            json.dump(run_record, f, indent=2, ensure_ascii=False)

        console.print(f"\n[bold green]🎉 MVP 處理完成！結果已存放至：{target_work_dir}[/bold green]\n")
        return raw_txt_path
