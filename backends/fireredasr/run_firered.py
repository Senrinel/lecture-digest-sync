"""FireRedASR runner executed inside the isolated backends/qwen (or fireredasr) venv."""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
import soundfile as sf
import torch

# Ensure fireredasr repo is in sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR / "repo"
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from fireredasr.models.fireredasr import FireRedAsr

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr:
    sys.stderr.reconfigure(encoding="utf-8")


def resolve_model_dir(model_name_or_path: str) -> str:
    """Resolve model path; download from Hugging Face if not a local directory."""
    if os.path.isdir(model_name_or_path):
        return os.path.abspath(model_name_or_path)

    # Download from HuggingFace
    from huggingface_hub import snapshot_download
    print(f"Downloading model from Hugging Face: {model_name_or_path}...", file=sys.stderr)
    local_path = snapshot_download(repo_id=model_name_or_path)
    return local_path


def slice_audio_if_needed(audio_path: str, max_chunk_sec: float = 30.0) -> list[tuple[str, float, float]]:
    """Slice audio into max_chunk_sec sub-chunks for AED model stability.

    Returns:
        List of (temp_wav_path, start_sec, end_sec)
    """
    data, samplerate = sf.read(audio_path)
    # Convert stereo to mono if needed
    if len(data.shape) > 1:
        data = data.mean(axis=1)

    total_samples = len(data)
    total_sec = total_samples / samplerate

    if total_sec <= max_chunk_sec:
        # Return original audio path directly
        return [(audio_path, 0.0, total_sec)]

    # Split into chunks of max_chunk_sec
    chunk_samples = int(max_chunk_sec * samplerate)
    sub_chunks = []
    tmp_dir = tempfile.mkdtemp(prefix="firered_slices_")

    for idx, start_sample in enumerate(range(0, total_samples, chunk_samples)):
        end_sample = min(start_sample + chunk_samples, total_samples)
        slice_data = data[start_sample:end_sample]
        slice_start = start_sample / samplerate
        slice_end = end_sample / samplerate

        slice_path = os.path.join(tmp_dir, f"slice_{idx:04d}.wav")
        # FireRedASR requires 16kHz 16-bit PCM WAV
        sf.write(slice_path, slice_data, samplerate, subtype="PCM_16")
        sub_chunks.append((slice_path, slice_start, slice_end))

    return sub_chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--model-dir", default="FireRedTeam/FireRedASR-AED-L", help="HF repo or local path")
    parser.add_argument("--asr-type", default="aed", choices=["aed", "llm"])
    parser.add_argument("--use-gpu", type=int, default=1)
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--output-json", default=None, help="Path to save JSON output")
    args = parser.parse_args()

    start_time = time.time()
    model_dir = resolve_model_dir(args.model_dir)

    print(f"Loading FireRedASR ({args.asr_type}) from {model_dir}...", file=sys.stderr)
    model = FireRedAsr.from_pretrained(args.asr_type, model_dir)

    # Slice audio if longer than 30s
    slices = slice_audio_if_needed(args.audio, max_chunk_sec=30.0)
    print(f"Processing {len(slices)} audio slices with FireRedASR...", file=sys.stderr)

    transcribed_parts = []
    timestamps = []

    try:
        for slice_path, s_start, s_end in slices:
            uttid = os.path.basename(slice_path)
            results = model.transcribe(
                [uttid],
                [slice_path],
                {
                    "use_gpu": args.use_gpu,
                    "beam_size": args.beam_size,
                }
            )
            for r in results:
                text = r.get("text", "").strip()
                if text:
                    transcribed_parts.append(text)
                    timestamps.append({
                        "start": round(s_start, 2),
                        "end": round(s_end, 2),
                        "text": text,
                    })
    finally:
        # Clean up temporary sliced files
        for slice_path, _, _ in slices:
            if slice_path != args.audio and os.path.isfile(slice_path):
                try:
                    os.unlink(slice_path)
                except OSError:
                    pass

    duration = round(time.time() - start_time, 2)
    output = {
        "text": "".join(transcribed_parts),
        "model": args.model_dir,
        "asr_type": args.asr_type,
        "duration_sec": duration,
        "timestamps": timestamps,
    }

    if args.output_json:
        out_path = os.path.abspath(args.output_json)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
