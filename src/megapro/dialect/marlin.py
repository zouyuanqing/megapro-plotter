"""Marlin dialect helpers: boilerplate headers/footers and log descriptions."""

from __future__ import annotations

__all__ = ["header_lines", "footer_lines", "is_motion", "describe"]

_MOTION_TOKENS = ("G0", "G1", "G2", "G3")


def header_lines() -> list[str]:
    """Lines sent before a job: absolute positioning, millimetres, extruder-relative."""
    return ["G90", "G21", "M82"]


def footer_lines() -> list[str]:
    """Lines sent after a job: settle, park XY, disable steppers."""
    return ["M400", "G28 X Y", "M84"]


def _code_part(line: str) -> str:
    """Return *line* stripped of whitespace and any trailing ``;`` comment."""
    if not isinstance(line, str):
        return ""
    return line.strip().split(";", 1)[0].strip()


def is_motion(line: str) -> bool:
    """True if *line* is a G0/G1/G2/G3 motion command."""
    code = _code_part(line)
    if not code:
        return False
    return code.upper().split()[0] in _MOTION_TOKENS


def describe(line: str) -> str:
    """Short one-line description of *line* for logs."""
    if not isinstance(line, str) or not line.strip():
        return "empty line"
    code = _code_part(line)
    if not code:
        return "comment"
    if len(code) > 64:
        return code[:61] + "..."
    return code
