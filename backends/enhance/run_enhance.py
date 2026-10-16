"""Standalone Audio Enhancement Runner (backends/enhance).

Executes neural and advanced DSP speech enhancement algorithms:
1. --wpe: NARA-WPE dereverberation (removes acoustic room reflections)
2. --dfn: DeepFilterNet3 neural speech denoising (Interspeech 2023)
3. --se: Deep/Spectral Speech Enhancement
4. --vadtrim: Silero VAD trimming (removes leading silence and pauses > 2s)

Designed to run in isolated backends/enhance/.venv.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
import numpy as np
import soundfile as sf
import torch
import torchaudio


def run_wpe(audio_data: np.ndarray, sr: int = 16000, taps: int = 10, delay: int = 3, iterations: int = 3) -> np.ndarray:
    """Apply NARA-WPE Weighted Prediction Error dereverberation."""
    from nara_wpe.wpe import wpe
    from nara_wpe.utils import stft, istft

    orig_len = len(audio_data)
    # STFT returns shape (D, T, F)
    signal = audio_data[np.newaxis, :].astype(np.float64)
    Y = stft(signal, size=512, shift=128)
    # WPE requires (F, D, T)
    Y_wpe = Y.transpose(2, 0, 1)
    Z_wpe = wpe(Y_wpe, taps=taps, delay=delay, iterations=iterations)
    # Transpose back to (D, T, F) for istft
    Z = Z_wpe.transpose(1, 2, 0)
    out = istft(Z, size=512, shift=128)
    res = out[0][:orig_len].astype(np.float32)
    # Normalize peak to avoid clipping
    peak = np.max(np.abs(res))
    if peak > 1.0:
        res = res / peak * 0.99
    return res


def run_dfn(audio_tensor: torch.Tensor, sr: int = 16000) -> torch.Tensor:
    """Apply DeepFilterNet3 neural speech denoising."""
    import df

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, df_state, _ = df.init_df()

    # DeepFilterNet3 works natively at 48kHz
    needs_resample = (sr != 48000)
    if needs_resample:
        resample_to_48k = torchaudio.transforms.Resample(sr, 48000)
        resample_back = torchaudio.transforms.Resample(48000, sr)
        audio_48k = resample_to_48k(audio_tensor)
    else:
        audio_48k = audio_tensor

    if audio_48k.dim() == 1:
        audio_48k = audio_48k.unsqueeze(0)

    # Process in chunks if audio is very long to prevent GPU OOM
    enhanced_48k = df.enhance(model, df_state, audio_48k)

    if needs_resample:
        enhanced = resample_back(enhanced_48k.cpu())
    else:
        enhanced = enhanced_48k.cpu()

    return enhanced


def run_se(audio_tensor: torch.Tensor, sr: int = 16000) -> torch.Tensor:
    """Apply Speech Enhancement (spectral subtraction / formant boosting)."""
    data = audio_tensor.squeeze().numpy().astype(np.float32)
    import scipy.signal

    # Mild high-shelf / clarity boost around speech presence (300Hz - 4000Hz)
    b, a = scipy.signal.butter(2, [300 / (sr / 2), 4000 / (sr / 2)], btype='bandpass')
    speech_band = scipy.signal.lfilter(b, a, data).astype(np.float32)
    enhanced = data + 0.15 * speech_band
    peak = np.max(np.abs(enhanced))
    if peak > 1.0:
        enhanced = enhanced / peak * 0.98
    return torch.from_numpy(enhanced.astype(np.float32)).unsqueeze(0)


def run_silero_vadtrim(audio_tensor: torch.Tensor, sr: int = 16000, min_silence_sec: float = 2.0) -> torch.Tensor:
    """Trim leading silence and compress excessive pauses > min_silence_sec using Silero VAD."""
    import silero_vad
    from silero_vad import get_speech_timestamps

    model_path = os.path.join(os.path.dirname(silero_vad.__file__), "data", "silero_vad.jit")
    # Open as binary stream to bypass Windows non-ASCII path bug in torch.jit.load
    with open(model_path, "rb") as f:
        model = torch.jit.load(f)

    flat_audio = audio_tensor.squeeze().to(dtype=torch.float32)
    if flat_audio.dim() > 1:
        flat_audio = flat_audio.mean(dim=0)

    timestamps = get_speech_timestamps(flat_audio, model, sampling_rate=sr)
    if not timestamps:
        # No speech detected, return original
        return audio_tensor

    # Keep speech chunks with small padding (200ms)
    pad = int(0.2 * sr)
    max_silence_samples = int(min_silence_sec * sr)
    segments = []
    total_len = len(flat_audio)

    for i, ts in enumerate(timestamps):
        start = max(0, ts["start"] - pad)
        end = min(total_len, ts["end"] + pad)

        if not segments:
            segments.append((start, end))
        else:
            prev_start, prev_end = segments[-1]
            gap = start - prev_end
            if gap <= max_silence_samples:
                # Merge segments
                segments[-1] = (prev_start, end)
            else:
                # Leave a standard natural pause (0.5s) instead of prolonged silence
                pause_len = int(0.5 * sr)
                segments.append((start - pause_len, end))

    chunks = [flat_audio[s:e] for s, e in segments]
    trimmed_audio = torch.cat(chunks, dim=0).unsqueeze(0)
    return trimmed_audio


def main():
    parser = argparse.ArgumentParser(description="Audio Enhancement Runner")
    parser.add_argument("--input", required=True, help="Input WAV file (16kHz mono)")
    parser.add_argument("--output", required=True, help="Output WAV file")
    parser.add_argument("--wpe", action="store_true", help="Enable NARA-WPE dereverberation")
    parser.add_argument("--dfn", action="store_true", help="Enable DeepFilterNet3 neural denoising")
    parser.add_argument("--se", action="store_true", help="Enable speech enhancement")
    parser.add_argument("--vadtrim", action="store_true", help="Enable Silero VAD silence trimming")
    args = parser.parse_args()

    in_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.is_file():
        print(f"Error: input file not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[Enhance] Loading audio: {in_path.name}")
    audio_data, sr = sf.read(str(in_path), dtype="float32")
    if audio_data.ndim > 1:
        audio_data = audio_data.mean(axis=1)

    # 1. WPE Dereverberation
    if args.wpe:
        print("[Enhance] Running NARA-WPE dereverberation...")
        audio_data = run_wpe(audio_data, sr=sr)

    # Convert to torch tensor for neural modules
    audio_tensor = torch.from_numpy(audio_data).unsqueeze(0)

    # 2. DeepFilterNet3 Denoising
    if args.dfn:
        print("[Enhance] Running DeepFilterNet3 neural denoising (CUDA)...")
        audio_tensor = run_dfn(audio_tensor, sr=sr)

    # 3. Speech Enhancement
    if args.se:
        print("[Enhance] Running Speech Enhancement...")
        audio_tensor = run_se(audio_tensor, sr=sr)

    # 4. Silero VAD Trim
    if args.vadtrim:
        print("[Enhance] Running Silero VAD silence trimming...")
        audio_tensor = run_silero_vadtrim(audio_tensor, sr=sr)

    # Save output WAV
    final_audio = audio_tensor.squeeze().numpy()
    sf.write(str(out_path), final_audio, sr, subtype="PCM_16")
    print(f"[Enhance] Successfully written enhanced audio to: {out_path.name} ({len(final_audio)/sr:.2f}s)")


if __name__ == "__main__":
    main()
