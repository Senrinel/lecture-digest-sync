"""Faster-whisper runner executed inside the isolated backends/fasterwhisper venv."""

import argparse
import json
import os
import sys
import time

# On Windows, automatically register NVIDIA CUDA / cuBLAS / cuDNN DLL paths from pip packages
if sys.platform == "win32":
    try:
        import site
        for site_pkg in site.getsitepackages():
            nvidia_base = os.path.join(site_pkg, "nvidia")
            if os.path.isdir(nvidia_base):
                for sub in os.listdir(nvidia_base):
                    bin_dir = os.path.join(nvidia_base, sub, "bin")
                    if os.path.isdir(bin_dir):
                        os.environ["PATH"] = bin_dir + os.path.pathsep + os.environ["PATH"]
                        try:
                            os.add_dll_directory(bin_dir)
                        except Exception:
                            pass
    except Exception:
        pass

from faster_whisper import WhisperModel

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr:
    sys.stderr.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--model", default="large-v3", help="Model size or repo ID (e.g. large-v3, turbo, MediaTek-Research/Breeze-ASR-25)")
    parser.add_argument("--device", default="cuda", help="cuda or cpu")
    parser.add_argument("--compute-type", default="int8_float16", help="int8, float16, int8_float16")
    parser.add_argument("--language", default="zh", help="Language code")
    parser.add_argument("--initial-prompt", default="這是一段台灣大學課堂的中文教學錄音，請使用繁體中文記錄。", help="Context prompt")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size")
    parser.add_argument("--output-json", default=None, help="Optional path to write JSON output directly")
    args = parser.parse_args()

    start_time = time.time()
    # Load model
    model = WhisperModel(
        model_size_or_path=args.model,
        device=args.device,
        compute_type=args.compute_type,
    )

    # Transcribe with hallucination mitigations:
    # condition_on_previous_text=False prevents infinite repetition loops on lecture audio
    # vad_filter=True filters non-speech noises
    segments, info = model.transcribe(
        args.audio,
        language=args.language,
        initial_prompt=args.initial_prompt,
        beam_size=args.beam_size,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    text_parts = []
    timestamps = []
    for seg in segments:
        text_parts.append(seg.text.strip())
        timestamps.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })

    duration = round(time.time() - start_time, 2)
    output = {
        "text": "".join(text_parts),
        "language": info.language,
        "language_probability": round(info.language_probability, 2),
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
