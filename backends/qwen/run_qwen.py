"""Qwen3-ASR runner executed inside the isolated backends/qwen venv."""

import argparse
import json
import os
import sys
import time
import torch
from transformers import Qwen3ASRForConditionalGeneration, Qwen3ASRProcessor

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr:
    sys.stderr.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B-hf", help="Model repo ID")
    parser.add_argument("--device", default="cuda", help="cuda or cpu")
    parser.add_argument("--dtype", default="float16", help="float16 or bfloat16 or float32")
    parser.add_argument("--language", default="zh", help="Language code (e.g. zh or en)")
    parser.add_argument("--prompt", default="", help="Context / hotword biasing prompt")
    parser.add_argument("--output-json", default=None, help="Path to save JSON output")
    args = parser.parse_args()

    start_time = time.time()

    # Determine dtype
    if args.dtype == "float16" and torch.cuda.is_available() and args.device == "cuda":
        torch_dtype = torch.float16
    elif args.dtype == "bfloat16" and torch.cuda.is_available() and args.device == "cuda":
        torch_dtype = torch.bfloat16
    else:
        torch_dtype = torch.float32

    # Load processor and model
    processor = Qwen3ASRProcessor.from_pretrained(args.model)
    model = Qwen3ASRForConditionalGeneration.from_pretrained(
        args.model,
        torch_dtype=torch_dtype,
        device_map=args.device,
    )

    # Prepare inputs using the official apply_transcription_request API
    prompt_arg = args.prompt.strip() if args.prompt.strip() else None
    inputs = processor.apply_transcription_request(
        audio=args.audio,
        language=args.language,
        prompt=prompt_arg,
    ).to(model.device, model.dtype)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=2048,
        )

    # Decode transcription
    input_len = inputs["input_ids"].shape[1]
    generated_ids = output_ids[:, input_len:]
    transcription = processor.decode(generated_ids, return_format="transcription_only")[0]

    duration = round(time.time() - start_time, 2)
    output = {
        "text": transcription.strip(),
        "model": args.model,
        "language": args.language,
        "duration_sec": duration,
        "timestamps": [],
    }

    if args.output_json:
        out_path = os.path.abspath(args.output_json)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
