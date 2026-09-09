"""Argparse CLI for the MegaPro M0 pen plotter (``python -m megapro``).

Exit codes: 0 ok, 2 usage/guard/config failure, 3 lost link mid-command.
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
from pathlib import Path

from ..safety.guard import GuardReject, check
from ..transport import marlin_serial

__all__ = ["main"]


class _UsageError(Exception):
    """Bad usage or configuration (exit 2)."""


class _TransportMissing(Exception):
    """Transport not implemented yet (exit 2, not a lost link)."""


try:
    # Sys.path-independent relative imports; the transport module lands in a
    # later stage, so fall back to placeholders that keep ``--help`` working.
    from ..transport.marlin_serial import (  # noqa: F401
        MarlinError,
        open_session,
        send_and_wait_ok,
        stream_file,
    )
except ImportError:  # pragma: no cover - transport filled in later

    class MarlinError(Exception):  # type: ignore[no-redef]
        """Fallback link-error type until transport.marlin_serial lands."""

    def open_session(*args, **kwargs):  # type: ignore[misc]
        raise _TransportMissing(
            "transport.marlin_serial.open_session is not implemented yet"
        )

    def send_and_wait_ok(*args, **kwargs):  # type: ignore[misc]
        raise _TransportMissing(
            "transport.marlin_serial.send_and_wait_ok is not implemented yet"
        )

    def stream_file(*args, **kwargs):  # type: ignore[misc]
        raise _TransportMissing(
            "transport.marlin_serial.stream_file is not implemented yet"
        )

try:
    from serial import SerialException
except ImportError:  # pragma: no cover - pyserial optional for --help

    class SerialException(Exception):
        """Fallback when pyserial is not installed."""


DEFAULT_BAUD = 250000
HOME_TIMEOUT = 60.0  # G28 can traverse the full 210mm travel
MOVE_TIMEOUT = 30.0  # single G0/G1 moves wait for completion ok
PROFILE_PATH = Path("profiles") / "mega-pro-marlin.yaml"
LOG_DIR = Path("logs")
PROBE_COMMANDS = ("M115", "M503", "M119", "M114")


def _link_errors() -> tuple:
    """Exception types that mean 'link lost' (exit 3)."""
    errors: list = [TimeoutError, SerialException, MarlinError]
    extra = getattr(marlin_serial, "MarlinError", None)
    if isinstance(extra, type) and extra not in errors:
        errors.append(extra)
    return tuple(errors)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _fmt(value: float) -> str:
    return f"{value:g}"


def _safe_name(port: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", port)


def _close_quietly(session) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _load_profile(path: Path = PROFILE_PATH) -> dict:
    """Read a flat ``key: value`` YAML profile with the stdlib only."""
    data: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    for rawline in text.splitlines():
        stripped = rawline.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if rawline[:1] in (" ", "\t"):
            continue  # inside a block scalar such as ``raw: |``
        if ":" not in rawline:
            continue
        key, _, value = rawline.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value in ("", "null", "~", "Null", "NULL"):
            data[key] = None
        elif key == "baud":
            try:
                data[key] = int(value)
            except ValueError:
                data[key] = value
        elif (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            data[key] = value[1:-1]
        else:
            data[key] = value
    return data


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if (
        not text
        or text != text.strip()
        or re.search(r'[:#\n\r"\']', text)
        or text in ("null", "~", "true", "false")
        or re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)", text)
    ):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _save_profile(updates: dict, path: Path = PROFILE_PATH) -> None:
    """Merge *updates* into the YAML profile, preserving other keys."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    out: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("raw:"):
            # Drop the old raw block; the new one is appended below.
            seen.add("raw")
            i += 1
            while i < len(lines) and lines[i][:1] in (" ", "\t"):
                i += 1
            continue
        if (
            line[:1] not in (" ", "\t")
            and ":" in line
            and not stripped.startswith("#")
        ):
            key = line.split(":", 1)[0].strip()
            if key in updates and key != "raw":
                out.append(f"{key}: {_yaml_scalar(updates[key])}")
                seen.add(key)
                i += 1
                continue
        out.append(line)
        i += 1
    for key in ("port", "baud", "firmware"):
        if key in updates and key not in seen:
            out.append(f"{key}: {_yaml_scalar(updates[key])}")
            seen.add(key)
    for key, value in updates.items():
        if key not in seen and key != "raw":
            out.append(f"{key}: {_yaml_scalar(value)}")
            seen.add(key)
    if "raw" in updates:
        out.append("raw: |")
        raw = updates["raw"] if updates["raw"] is not None else ""
        for rawline in str(raw).splitlines() or [""]:
            out.append(f"  {rawline}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _resolve_port_baud(args) -> tuple[str, int | None]:
    profile = _load_profile()
    port = (
        args.port
        or getattr(args, "port_sub", None)
        or profile.get("port")
    )
    baud = (
        args.baud
        or getattr(args, "baud_sub", None)
        or profile.get("baud")
    )
    if not port:
        raise _UsageError(
            "no port given and none in profiles/mega-pro-marlin.yaml; "
            "use --port (e.g. --port COM3)"
        )
    if baud is None:
        return str(port), None
    try:
        baud = int(baud)
    except (TypeError, ValueError):
        raise _UsageError(f"bad baud rate: {baud!r}")
    return str(port), baud
    if not port:
        raise _UsageError(
            "no port given and none in profiles/mega-pro-marlin.yaml; "
            "use --port (e.g. --port COM3)"
        )
    try:
        baud = int(baud)
    except (TypeError, ValueError):
        raise _UsageError(f"bad baud rate: {baud!r}")
    return str(port), baud


def _allow_z(args) -> bool:
    return bool(
        getattr(args, "allow_z", False) or getattr(args, "allow_z_sub", False)
    )


def _lift_configured(profile=None) -> bool:
    """True once pen-down calibration is stored in the profile."""
    data = profile if profile is not None else _load_profile()
    return data.get("pen_down_z") is not None


SAFE_HOP_MM = 5.0  # auto Z-hop before every XY jog (user safety rule)


def _firmware_from_m115(text: str) -> str:
    for rawline in text.splitlines():
        line = rawline.strip()
        if not line:
            continue
        match = re.search(r"FIRMWARE_NAME\s*:\s*(.+)", line, re.IGNORECASE)
        if match:
            return match.group(1).strip()[:160]
    for rawline in text.splitlines():
        if rawline.strip():
            return rawline.strip()[:160]
    return "unknown"


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def _cmd_list_ports() -> int:
    print("Serial ports:")
    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())
    except Exception as exc:
        ports = []
        print(f"  (enumeration unavailable: {exc})")
    for port in ports:
        vidpid = "?:?"
        try:
            if port.vid is not None and port.pid is not None:
                vidpid = f"{port.vid:04X}:{port.pid:04X}"
        except AttributeError:
            pass
        description = getattr(port, "description", "") or ""
        print(f"  {port.device} vid:pid={vidpid} {description}".rstrip())
    if not ports:
        print("  (no ports found)")
    print(
        "warning: port enumeration may be empty on this host; "
        "prefer an explicit --port (e.g. --port COM3)."
    )
    return 0


class _Capture:
    """In-memory log sink capturing reply chatter per command."""

    def __init__(self):
        self.tx: list[str] = []
        self.rx: list[str] = []

    def write_tx(self, line):
        self.tx.append(line)

    def write_rx(self, line):
        self.rx.append(line)


def _cmd_probe(args) -> int:
    port, baud = _resolve_port_baud(args)
    candidates = [baud] if baud is not None else list(marlin_serial.TRY_BAUDS)
    session = None
    baud_used = None
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            session, baud_used = open_session(port, candidate)
        except SystemExit as exc:
            last_error = exc
            continue
        try:
            send_and_wait_ok(session, "M115", timeout=3.0)
        except TimeoutError as exc:
            last_error = exc
            _close_quietly(session)
            session = None
            print(f"  {port} @ {candidate}: silent, trying next baud...")
            continue
        break
    if session is None:
        raise _UsageError(
            f"no reply on {port} at {candidates}; "
            f"last error: {last_error}"
        )
    baud = baud_used
    responses: dict[str, str] = {}
    try:
        transcript = [f"# megapro probe {port} @ {baud}"]
        for cmd in PROBE_COMMANDS:
            check(cmd)
            cap = _Capture()
            resp = send_and_wait_ok(session, cmd, log=cap)
            chatter = [l for l in cap.rx if l.strip().lower() != "ok"]
            text = "\n".join(chatter) if chatter else ("" if resp is None else str(resp))
            responses[cmd] = text
            transcript.append(f">> {cmd}")
            transcript.append(f"<< {text}")
    finally:
        _close_quietly(session)
    transcript_text = "\n".join(transcript) + "\n"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"probe-{_safe_name(port)}_{baud}.txt"
    log_path.write_text(transcript_text, encoding="utf-8")
    firmware = _firmware_from_m115(responses.get("M115", ""))
    _save_profile(
        {"port": port, "baud": baud, "firmware": firmware, "raw": transcript_text}
    )
    print(f"port: {port}")
    print(f"baud: {baud}")
    print(f"firmware: {firmware}")
    print(f"transcript: {log_path}")
    return 0


def _cmd_jog(args) -> int:
    if args.x is None and args.y is None and args.z is None:
        raise _UsageError("jog needs at least one axis: --x/--y/--z in mm")
    allow_z = _allow_z(args)
    if args.z is not None and not allow_z:
        raise GuardReject("jog with --z requires --allow-z")
    port, baud = _resolve_port_baud(args)
    parts: list[str] = []
    if args.x is not None:
        parts.append(f"X{_fmt(args.x)}")
    if args.y is not None:
        parts.append(f"Y{_fmt(args.y)}")
    if args.z is not None:
        parts.append(f"Z{_fmt(args.z)}")
    move = "G0 " + " ".join(parts) + " F600"
    lift_ok = allow_z or _lift_configured()
    check("G91")
    check(move, allow_z=allow_z, lift_configured=lift_ok)
    check("G90")
    session, baud = open_session(port, baud)
    try:
        send_and_wait_ok(session, "G91", timeout=MOVE_TIMEOUT)
        try:
            if (args.x is not None or args.y is not None) and not args.draw:
                # Safety rule: lift before ANY travel move, and stay up.
                # Use --draw to move at current height (pen stays down).
                hop = f"G0 Z{SAFE_HOP_MM:g} F300"
                check(hop)  # positive hop always passes the guard
                send_and_wait_ok(session, hop, timeout=MOVE_TIMEOUT)
                print(f"z-hop first (safety rule): {hop} (staying up)")
            send_and_wait_ok(session, move, timeout=MOVE_TIMEOUT)
        finally:
            send_and_wait_ok(session, "G90", timeout=MOVE_TIMEOUT)
    finally:
        _close_quietly(session)
    print(f"jogged (relative): {move}")
    return 0


def _cmd_home(args) -> int:
    axis = (args.axis or "").upper().split()
    if not axis:
        raise _UsageError('home needs an axis, e.g. --axis "X Y"')
    allow_z = _allow_z(args)
    if "Z" in axis and not allow_z:
        raise GuardReject("homing Z requires --allow-z")
    line = "G28 " + " ".join(axis)
    check(line, allow_z=allow_z)
    port, baud = _resolve_port_baud(args)
    session, baud = open_session(port, baud)
    try:
        # Lift first: G28 XY drags the pen across the paper otherwise.
        send_and_wait_ok(session, "G91", timeout=MOVE_TIMEOUT)
        try:
            lift = "G0 Z10 F300"
            check(lift)
            send_and_wait_ok(session, lift, timeout=MOVE_TIMEOUT)
        finally:
            send_and_wait_ok(session, "G90", timeout=MOVE_TIMEOUT)
        send_and_wait_ok(session, line, timeout=HOME_TIMEOUT)
    finally:
        _close_quietly(session)
    print(f"homed: {line}")
    return 0


def _cmd_stream(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise _UsageError(f"file not found: {args.file}")
    port, baud = _resolve_port_baud(args)
    session, baud = open_session(port, baud)
    guard = functools.partial(check, lift_configured=_lift_configured())
    try:
        try:
            stream_file(session, str(path), guard_check=guard)
        except TypeError:
            # Tolerate transports whose guard parameter is positional-only
            # or still named ``guard``.
            stream_file(session, str(path), guard)
    finally:
        _close_quietly(session)
    print(f"streamed: {path}")
    return 0


def _send_without_wait(session, line: str) -> None:
    """Send *line* raw, without waiting for an ``ok``."""
    send_line = getattr(session, "send_line", None)
    if callable(send_line):
        send_line(line)
        return
    write = getattr(session, "write", None)
    if callable(write):
        write((line + "\n").encode("utf-8", errors="replace"))
        return
    send = getattr(session, "send", None)
    if callable(send):
        send(line)
        return
    raise _UsageError(
        "transport session has no raw send method; cannot send M112"
    )


def _cmd_console(args) -> int:
    """One persistent session, many commands: no per-command DTR reset."""
    port, baud = _resolve_port_baud(args)
    allow_z = _allow_z(args)
    lift_ok = allow_z or _lift_configured()
    session, baud = open_session(port, baud)
    print(f"console on {port} @ {baud} (type .quit to exit)")
    print("lines go verbatim; lift Z yourself before XY travel!")
    code = 0
    try:
        while True:
            try:
                raw = input("> ")
            except EOFError:
                break
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            if line in (".quit", "exit", ".exit"):
                break
            try:
                check(line, allow_z=allow_z, lift_configured=lift_ok)
            except GuardReject as exc:
                print(f"blocked: {exc}")
                continue
            try:
                cap = _Capture()
                result = send_and_wait_ok(session, line, timeout=MOVE_TIMEOUT,
                                          log=cap)
            except MarlinError as exc:
                print(f"firmware error: {exc}")
                continue
            except _link_errors() as exc:
                print(f"link lost: {exc}")
                code = 3
                break
            for rx in cap.rx:
                if rx.strip().lower() != "ok":
                    print(f"< {rx}")
            print(result)
    finally:
        _close_quietly(session)
    return code


def _cmd_move(args) -> int:
    """Absolute linear move (robot-style movel): G90 target + feedrate.

    Safety rule: XY travel auto-lifts +5mm first and stays up, unless
    --draw is given (pen stays at current height for strokes).
    """
    if args.x is None and args.y is None and args.z is None:
        raise _UsageError("move needs a target: --x/--y/--z in mm (absolute)")
    allow_z = _allow_z(args)
    if args.z is not None and not allow_z:
        raise GuardReject("move with --z requires --allow-z")
    port, baud = _resolve_port_baud(args)
    feed = args.feed or 1200.0
    parts: list[str] = []
    if args.x is not None:
        parts.append(f"X{_fmt(args.x)}")
    if args.y is not None:
        parts.append(f"Y{_fmt(args.y)}")
    if args.z is not None:
        parts.append(f"Z{_fmt(args.z)}")
    line = "G0 " + " ".join(parts) + f" F{_fmt(feed)}"
    lift_ok = allow_z or _lift_configured()
    check("G90")
    check(line, allow_z=allow_z, lift_configured=lift_ok)
    session, baud = open_session(port, baud)
    try:
        send_and_wait_ok(session, "G90", timeout=MOVE_TIMEOUT)
        if (args.x is not None or args.y is not None) and not args.draw:
            send_and_wait_ok(session, "G91", timeout=MOVE_TIMEOUT)
            try:
                hop = f"G0 Z{SAFE_HOP_MM:g} F300"
                check(hop)
                send_and_wait_ok(session, hop, timeout=MOVE_TIMEOUT)
                print(f"z-hop first (safety rule): {hop} (staying up)")
            finally:
                send_and_wait_ok(session, "G90", timeout=MOVE_TIMEOUT)
        send_and_wait_ok(session, line, timeout=MOVE_TIMEOUT)
    finally:
        _close_quietly(session)
    print(f"moved (absolute): {line}")
    return 0


def _cmd_estop(args) -> int:
    port, baud = _resolve_port_baud(args)
    session, baud = open_session(port, baud)
    try:
        _send_without_wait(session, "M112")
    finally:
        _close_quietly(session)
    print("emergency stop sent (M112)")
    return 0


# --------------------------------------------------------------------------
# Parser + entry point
# --------------------------------------------------------------------------

def _add_link_args(sub) -> None:
    """Accept --port/--baud/--allow-z after the subcommand too."""
    sub.add_argument(
        "--port", dest="port_sub", default=None,
        help="serial port (also accepted before the subcommand)",
    )
    sub.add_argument(
        "--baud", dest="baud_sub", type=int, default=None,
        help="baud rate (also accepted before the subcommand)",
    )
    sub.add_argument(
        "--allow-z", dest="allow_z_sub", action="store_true", default=False,
        help="permit Z motion (also accepted before the subcommand)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="megapro",
        description="MegaPro M0 Marlin pen plotter CLI",
    )
    parser.add_argument("--port", default=None, help="serial port (e.g. COM3)")
    parser.add_argument("--baud", type=int, default=None, help="baud rate")
    parser.add_argument(
        "--allow-z", action="store_true", default=False,
        help="permit Z motion/homing (locked by default)",
    )
    parser.add_argument(
        "--list-ports", action="store_true", default=False,
        help="list serial ports (best effort) and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    probe = sub.add_parser("probe", help="query firmware and save a transcript")
    _add_link_args(probe)
    probe.set_defaults(func=_cmd_probe)

    jog = sub.add_parser("jog", help="relative XY(Z) move at F600")
    _add_link_args(jog)
    jog.add_argument("--x", type=float, default=None, help="X delta in mm")
    jog.add_argument("--y", type=float, default=None, help="Y delta in mm")
    jog.add_argument("--z", type=float, default=None, help="Z delta in mm")
    jog.add_argument(
        "--draw", action="store_true", default=False,
        help="draw at current height: skip the safety Z-hop (pen stays down)",
    )
    jog.set_defaults(func=_cmd_jog)

    home = sub.add_parser("home", help="home axes with G28")
    _add_link_args(home)
    home.add_argument("--axis", default="X Y", help='axes, e.g. "X Y"')
    home.set_defaults(func=_cmd_home)

    stream = sub.add_parser("stream", help="stream a gcode file to the machine")
    _add_link_args(stream)
    stream.add_argument("file", help="gcode file to stream")
    stream.set_defaults(func=_cmd_stream)

    move = sub.add_parser(
        "move",
        help="absolute linear move to XYZ target (robot-style movel)",
    )
    _add_link_args(move)
    move.add_argument("--x", type=float, default=None, help="target X in mm")
    move.add_argument("--y", type=float, default=None, help="target Y in mm")
    move.add_argument("--z", type=float, default=None, help="target Z in mm")
    move.add_argument("--feed", type=float, default=1200.0, help="feedrate")
    move.add_argument(
        "--draw", action="store_true", default=False,
        help="move at current height: skip the safety Z-hop",
    )
    move.set_defaults(func=_cmd_move)

    estop = sub.add_parser("estop", help="send M112 immediately, no reply wait")
    _add_link_args(estop)
    estop.set_defaults(func=_cmd_estop)

    console = sub.add_parser(
        "console",
        help="persistent session: many commands, one open (no resets)",
    )
    _add_link_args(console)
    console.set_defaults(func=_cmd_console)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.list_ports:
            return _cmd_list_ports()
        func = getattr(args, "func", None)
        if func is None:
            parser.print_help()
            return 2
        return func(args) or 0
    except GuardReject as exc:
        print(f"error: guard rejected command: {exc}", file=sys.stderr)
        return 2
    except (_UsageError, _TransportMissing, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except _link_errors() as exc:
        print(f"error: link lost: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
