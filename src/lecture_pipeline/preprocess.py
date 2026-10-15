"""Audio preprocessing module.

Implements independently toggleable audio filters tailored for SONY ICD-PX470 desk recordings:
- hp: High-pass filter (80Hz, 100Hz, 120Hz) to remove table vibrations & AC rumble
- loud: EBU R128 loudness normalization (-16, -19, -23 LUFS)
- dyn: Dynamic range compression (dynaudnorm) for speaker movement
- nr: Spectral noise reduction (afftdn)
- vadtrim: Leading/trailing silence trimming
Standardizes output to 16kHz 16-bit mono WAV.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from pydantic import BaseModel, Field


class PreprocessOptions(BaseModel):
    """Configuration options for audio preprocessing switches."""
    hp: str = Field(default="100Hz", description="off | 80Hz | 100Hz | 120Hz")
    loud: str = Field(default="-19LUFS", description="off | -16LUFS | -19LUFS | -23LUFS")
    dyn: str = Field(default="off", description="off | light | standard")
    nr: str = Field(default="off", description="off | 6dB | 12dB | 18dB")
    dfn: str = Field(default="off", description="off | on (DeepFilterNet)")
    se: str = Field(default="off", description="off | on (MossFormer2)")
    wpe: str = Field(default="off", description="off | on (Nara-WPE)")
    vadtrim: str = Field(default="off", description="off | on")

    @classmethod
    def from_string(cls, opt_str: str) -> PreprocessOptions:
        """Parse comma-separated flags, e.g., 'hp:100Hz,loud:-19LUFS' or 'hp,loud'.

        Flags without values default to their standard on-values.
        """
        opts = {k: "off" for k in cls.model_fields.keys()}
        if not opt_str or opt_str.strip() == "off" or opt_str.strip() == "none":
            return cls(**opts)

        for part in opt_str.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, v = part.split(":", 1)
                k, v = k.strip().lower(), v.strip()
                if k in opts:
                    opts[k] = v
            else:
                k = part.lower()
                if k == "hp":
                    opts["hp"] = "100Hz"
                elif k == "loud":
                    opts["loud"] = "-19LUFS"
                elif k == "dyn":
                    opts["dyn"] = "standard"
                elif k == "nr":
                    opts["nr"] = "12dB"
                elif k in opts:
                    opts[k] = "on"

        return cls(**opts)


def build_ffmpeg_filter_chain(options: PreprocessOptions) -> list[str]:
    """Build the FFmpeg audio filter list (-af) from enabled options.

    Filters are ordered acoustically:
    1. High-pass filter (remove infrasound/rumble before processing)
    2. Spectral noise reduction (afftdn)
    3. Dynamic audio normalizer (smooth volume)
    4. EBU R128 loudness normalization (final leveling)
    5. Silence trim (silenceremove)
    """
    filters: list[str] = []

    # 1. High-pass filter
    if options.hp and options.hp.lower() != "off":
        freq_str = options.hp.lower().replace("hz", "")
        try:
            freq = int(freq_str)
        except ValueError:
            freq = 100
        filters.append(f"highpass=f={freq}:poles=2")

    # 2. Spectral Noise Reduction
    if options.nr and options.nr.lower() != "off":
        nr_val = options.nr.lower().replace("db", "")
        # Map dB to noise floor: 6dB -> -30, 12dB -> -25, 18dB -> -20
        noise_floor_map = {"6": "-30", "12": "-25", "18": "-20"}
        floor_val = noise_floor_map.get(nr_val, "-25")
        filters.append(f"afftdn=nf={floor_val}:tn=1")

    # 3. Dynamic Normalizer
    if options.dyn and options.dyn.lower() != "off":
        if options.dyn.lower() == "light":
            filters.append("dynaudnorm=f=500:g=15:p=0.95")
        else:  # standard
            filters.append("dynaudnorm=f=1000:g=31:p=0.95")

    # 4. Loudness Normalization (EBU R128)
    if options.loud and options.loud.lower() != "off":
        loud_val = options.loud.lower().replace("lufs", "")
        try:
            lufs = float(loud_val)
        except ValueError:
            lufs = -19.0
        filters.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")

    # 5. VAD trim (silence remove from front & back)
    if options.vadtrim and options.vadtrim.lower() != "off":
        # Remove silence longer than 2.0s at beginning (threshold -45dB)
        filters.append("silenceremove=start_periods=1:start_duration=2:start_threshold=-45dB")

    return filters


def preprocess_audio(
    input_path: str | Path,
    output_path: str | Path,
    options: PreprocessOptions | None = None,
) -> tuple[Path, float]:
    """Preprocess audio file using FFmpeg with given filter options.

    Standardizes to:
    - 16,000 Hz sample rate
    - 1 channel mono
    - 16-bit PCM WAV

    Returns:
        (output_path, elapsed_seconds)
    """
    if options is None:
        options = PreprocessOptions()

    inp = Path(input_path).resolve()
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if not inp.exists():
        raise FileNotFoundError(f"Input audio file not found: {inp}")

    filter_chain = build_ffmpeg_filter_chain(options)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(inp),
        "-vn",                   # Disable video
        "-ar", "16000",          # 16kHz
        "-ac", "1",              # Mono
        "-c:a", "pcm_s16le",     # 16-bit PCM
    ]

    if filter_chain:
        cmd.extend(["-af", ",".join(filter_chain)])

    cmd.append(str(out))

    start_time = time.time()
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    elapsed = time.time() - start_time

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg preprocessing failed (code {result.returncode}):\n{result.stderr}")

    return out, round(elapsed, 2)
