"""Tests for scripts.cam_capture (command construction only, no camera)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cam_capture import build_cmd, timestamped_path


def test_build_cmd_single_frame():
    cmd = build_cmd("ASUS FHD webcam", "logs/x.jpg")
    assert cmd[:4] == ["ffmpeg", "-y", "-f", "dshow"]
    assert "video=ASUS FHD webcam" in cmd
    assert cmd[-3:-1] == ["-frames:v", "1"]
    assert cmd[-1] == "logs/x.jpg"


def test_timestamped_path_unique_names():
    a, b = timestamped_path("logs"), timestamped_path("logs")
    assert a.suffix == ".jpg" and a.parent.name == "logs"
    assert isinstance(b, Path)
