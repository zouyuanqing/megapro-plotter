"""Camera frame grabber for motion verification (ffmpeg DirectShow).

Used to confirm physical effects (homing, dry-run, pen traces) by
comparing before/after frames. Capture itself needs a real camera;
unit tests only cover command construction (no hardware touched).
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

__all__ = ["build_cmd", "capture", "timestamped_path"]

FFMPEG = "ffmpeg"


def build_cmd(device: str, out_path: str | Path,
              size: str = "1280x720") -> list[str]:
    """ffmpeg argv grabbing exactly one frame from a dshow device."""
    return [FFMPEG, "-y", "-f", "dshow", "-video_size", size,
            "-i", f"video={device}", "-frames:v", "1", str(out_path)]


def timestamped_path(directory: str | Path = "logs",
                     prefix: str = "cam") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(directory) / f"{prefix}_{stamp}.jpg"


def capture(device: str = "ASUS FHD webcam",
            out_path: str | Path | None = None,
            timeout: int = 30) -> Path:
    """Grab one frame; returns the written path (raises on failure)."""
    target = Path(out_path) if out_path else timestamped_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(build_cmd(device, target), check=True,
                   capture_output=True, timeout=timeout)
    return target


if __name__ == "__main__":
    import sys

    device = sys.argv[1] if len(sys.argv) > 1 else "ASUS FHD webcam"
    print(capture(device))
