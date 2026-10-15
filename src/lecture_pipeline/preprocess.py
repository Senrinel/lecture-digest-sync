"""Audio preprocessing module with independent toggles for experimental benchmarking."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class PreprocessingOptions:
    """Toggles and parameters for audio preprocessing."""
    hp: str = "100Hz"         # off | 80Hz | 100Hz | 120Hz
    loud: str = "-19LUFS"     # off | -16LUFS | -19LUFS | -23LUFS
    dyn: str = "off"          # off | light | standard
    nr: str = "off"           # off | 6dB | 12dB | 18dB
    sample_rate: int = 16000  # Standard ASR sampling rate
    mono: bool = True

    def build_ffmpeg_filter_chain(self) -> List[str]:
        """Constructs the ffmpeg audio filter string (-af ...) from active options."""
        filters = []

        # 1. High-pass filter (cuts desk vibration & A/C hum)
        if self.hp != "off":
            freq = self.hp.replace("Hz", "").strip()
            filters.append(f"highpass=f={freq}")

        # 2. Spectral noise reduction
        if self.nr != "off":
            nr_val = self.nr.replace("dB", "").strip()
            filters.append(f"afftdn=nr={nr_val}:nf=-30")

        # 3. Dynamic audio normalizer (balances soft student vs loud teacher)
        if self.dyn == "light":
            filters.append("dynaudnorm=f=500:g=15:p=0.95")
        elif self.dyn == "standard":
            filters.append("dynaudnorm=f=1000:g=31:p=0.95")

        # 4. Loudness normalization (EBU R128)
        if self.loud != "off":
            lufs = self.loud.replace("LUFS", "").strip()
            filters.append(f"loudnorm=I={lufs}:LRA=11:TP=-1.5")

        return filters

    def to_dict(self) -> Dict[str, str]:
        return {
            "hp": self.hp,
            "loud": self.loud,
            "dyn": self.dyn,
            "nr": self.nr,
            "sample_rate": str(self.sample_rate),
            "mono": str(self.mono),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PreprocessingOptions":
        return cls(
            hp=data.get("hp", "100Hz"),
            loud=data.get("loud", "-19LUFS"),
            dyn=data.get("dyn", "off"),
            nr=data.get("nr", "off"),
            sample_rate=int(data.get("sample_rate", 16000)),
            mono=bool(data.get("mono", True)),
        )


def preprocess_audio(
    input_path: str | Path,
    output_path: str | Path,
    options: Optional[PreprocessingOptions] = None,
) -> Path:
    """Preprocesses an audio file using ffmpeg based on the given options.
    
    Converts to 16kHz mono WAV by default, applying any active filters.
    """
    inp = Path(input_path)
    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)

    opts = options or PreprocessingOptions()
    filters = opts.build_ffmpeg_filter_chain()

    cmd = ["ffmpeg", "-y", "-i", str(inp)]

    if opts.mono:
        cmd.extend(["-ac", "1"])
    if opts.sample_rate:
        cmd.extend(["-ar", str(opts.sample_rate)])

    if filters:
        cmd.extend(["-af", ",".join(filters)])

    cmd.extend(["-c:a", "pcm_s16le", str(outp)])

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg preprocessing failed:\n{result.stderr}")

    return outp
