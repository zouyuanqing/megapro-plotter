"""Safety guard for MegaPro M0 (Marlin pen plotter).

Every gcode line is vetted by :func:`check` before it may reach the machine.
Heaters, spindles and lasers are never allowed on this pen toolhead, lines
are length- and charset-limited, and any Z motion needs an explicit opt-in.
"""

from __future__ import annotations

__all__ = [
    "GuardReject",
    "BLOCKED_PREFIXES",
    "LASER_TOKENS",
    "MAX_LINE",
    "check",
]


class GuardReject(Exception):
    """Raised when a gcode line is not safe to send to the machine."""


#: First-token prefixes that are always rejected (matched case-insensitively
#: against the first whitespace-separated token; a prefix only matches when
#: it is the whole token or is followed by a non-digit, so ``M32``/``M30``/
#: ``M500`` stay legal while ``M3 S255`` and ``M104 S200`` are blocked).
BLOCKED_PREFIXES = ("M104", "M109", "M140", "M190", "M3", "M4", "M5")

#: First tokens that are always rejected (laser control; exact match so that
#: coordinate-system codes such as ``G54`` stay legal).
LASER_TOKENS = ("G6", "G5")

#: Maximum accepted line length in characters.
MAX_LINE = 96

#: First tokens treated as linear moves for the Z-motion rule.
_LINEAR_MOVES = ("G0", "G1")


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _is_blocked(first: str) -> bool:
    """Match *first* (already upper-cased) against BLOCKED_PREFIXES."""
    for prefix in BLOCKED_PREFIXES:
        if first == prefix:
            return True
        if first.startswith(prefix):
            rest = first[len(prefix):]
            # "M32" must NOT match "M3": a longer token only matches when
            # the character right after the prefix is not a digit
            # (catches glued forms such as "M104S200").
            if rest and not rest[0].isdigit():
                return True
    return False


def _has_z_word(tokens: list[str]) -> bool:
    """True if any token addresses the Z axis (bare ``Z`` or ``Z<value>``)."""
    for token in tokens:
        if token == "Z":
            return True
        if token.startswith("Z") and _is_number(token[1:]):
            return True
    return False


def check(line: str, allow_z: bool = False, lift_configured: bool = False) -> None:
    """Vet one gcode *line*; return None or raise :class:`GuardReject`.

    Empty and comment-only lines pass (comment filtering is the caller's
    job).  Raises :class:`GuardReject` on: non-ASCII characters, lines
    longer than :data:`MAX_LINE`, heater/spindle prefixes, laser tokens,
    pre-numbered (``N...``) lines, and Z motion without opt-in.
    """
    text = line.strip()
    if not text:
        return None
    if any(ord(char) > 127 for char in text):
        raise GuardReject(
            f"non-ASCII characters are not allowed: {text[:48]!r}"
        )
    if len(text) > MAX_LINE:
        raise GuardReject(
            f"line too long ({len(text)} chars, max is {MAX_LINE})"
        )
    # Strip a trailing "; ..." comment for token parsing.
    code = text.split(";", 1)[0].strip()
    if not code:
        return None  # comment-only line
    upper = code.upper()
    if len(upper) >= 2 and upper[0] == "N" and upper[1].isdigit():
        raise GuardReject(
            "pre-numbered lines (N...) are not allowed; "
            "send plain gcode without line numbers"
        )
    tokens = upper.split()
    first = tokens[0]
    if _is_blocked(first):
        raise GuardReject(
            f"blocked command {first}: heater/spindle control "
            "is disabled on the pen toolhead"
        )
    if first in LASER_TOKENS:
        raise GuardReject(
            f"blocked command {first}: laser control "
            "is disabled on the pen toolhead"
        )
    if first == "G28":
        if _has_z_word(tokens[1:]) and not allow_z:
            raise GuardReject(
                "homing Z (G28 with Z) requires --allow-z; "
                "plain XY homing does not need it"
            )
        return None
    if first in _LINEAR_MOVES:
        for token in tokens[1:]:
            if token == "Z":
                # Bare Z word: Z homing intent.
                if not (allow_z and lift_configured):
                    raise GuardReject(
                        f"Z homing move on {first} requires --allow-z "
                        "and a configured Z lift"
                    )
            elif token.startswith("Z") and _is_number(token[1:]):
                value = float(token[1:])
                if value < 0 and not (allow_z and lift_configured):
                    raise GuardReject(
                        f"negative Z move (Z{value:g}) on {first} requires "
                        "--allow-z and a configured Z lift"
                    )
    return None
