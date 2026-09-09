"""Serial transport for Marlin (M0: ASCII, no line numbers/checksums)."""

import sys
import time

import serial

DTR_WAIT = 2.5  # Mega resets on serial open; bootloader window

TRY_BAUDS = (250000, 115200)

_MEGA_VID, _MEGA_PID = 0x2341, 0x0042


class MarlinError(Exception):
    """Firmware reported an error (Error: / !!) or a resend storm."""


class RawLog:
    """Append-only text log of TX/RX lines with monotonic timestamps."""

    def __init__(self, path):
        self.path = path

    def _append(self, marker, line):
        with open(self.path, "a", encoding="ascii", errors="replace") as f:
            f.write("%.3f %s %s\n" % (time.monotonic(), marker, line))

    def write_tx(self, line):
        self._append(">", line)

    def write_rx(self, line):
        self._append("<", line)


def settle_link(ser, boot_timeout=25.0, idle_settle=8.0, log=None):
    """Best-effort post-open settle; never fatal, returns True if an ok seen.

    Covers three open states: cold boot flood (drains to first ok),
    bootloader silence (keeps waiting through quiet periods), and warm
    idle (returns False after sustained quiet so commanding can proceed —
    Marlin is silent when idle and answers on demand).
    """
    start = time.monotonic()
    deadline = start + boot_timeout
    quiet_after = start + idle_settle
    old_timeout = getattr(ser, "timeout", None)
    try:
        try:
            ser.timeout = 0.3
        except Exception:
            pass
        empties = 0
        while True:
            now = time.monotonic()
            if now >= deadline:
                return False
            raw = ser.readline()
            if not raw:
                empties += 1
                if empties >= 3 and now >= quiet_after:
                    return False
                continue
            empties = 0
            text = raw.decode("ascii", errors="replace").strip()
            if log is not None:
                log.write_rx(text)
            if text.lower().startswith("ok"):
                return True
    finally:
        if old_timeout is not None:
            try:
                ser.timeout = old_timeout
            except Exception:
                pass


def open_link(port, baud, timeout=2.0, serial_factory=None):
    """Open a serial link; wait out the DTR reset, then flush input.

    DTR/RTS are driven low right after open: several 8-bit boards
    (observed on a CP210x-attached Trigorilla) sit in reset while DTR
    is asserted and stay silent until it is released.
    """
    maker = serial_factory or serial.Serial
    try:
        ser = maker(port, baud, timeout=timeout)
    except Exception as e:
        print("marlin_serial: error: cannot open %s (%s). "
              "Close Arduino Monitor / other serial tools and retry."
              % (port, e), file=sys.stderr)
        raise SystemExit(2)
    try:
        ser.dtr = False
    except Exception:
        pass
    try:
        ser.rts = False
    except Exception:
        pass
    time.sleep(DTR_WAIT)
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    settle_link(ser)
    return ser


def find_printer():
    """Best-effort scan for a Mega2560 (VID:PID 2341:0042); None if unsure."""
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if getattr(p, "vid", None) == _MEGA_VID \
                    and getattr(p, "pid", None) == _MEGA_PID:
                return p.device
    except Exception:
        pass
    return None


def send_and_wait_ok(ser, line, timeout=5.0, log=None):
    """Send one ASCII line; wait for ok / resend / Error. Returns "ok"."""
    if log is not None:
        log.write_tx(line)
    ser.write((line + "\n").encode("ascii"))
    deadline = time.monotonic() + timeout
    while True:
        raw = ser.readline()
        if not raw:
            if time.monotonic() >= deadline:
                raise TimeoutError("no 'ok' for %r within %.1fs" % (line, timeout))
            continue
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            if time.monotonic() >= deadline:
                raise TimeoutError("no 'ok' for %r within %.1fs" % (line, timeout))
            continue
        low = text.lower()
        if low.startswith("resend"):
            if log is not None:
                log.write_rx(text)
            return "resend"
        if low.startswith("ok"):
            if log is not None:
                log.write_rx(text)
            return "ok"
        if low.startswith("error:") or text.startswith("!!"):
            if log is not None:
                log.write_rx(text)
            raise MarlinError(text)
        # Unsolicited chatter (echo:, T:, etc.): log only, keep waiting.
        if log is not None:
            log.write_rx(text)
        if time.monotonic() >= deadline:
            raise TimeoutError("no 'ok' for %r within %.1fs" % (line, timeout))


def sync_lineno(ser, log=None):
    """Best-effort line-number reset (M110 N0); errors ignored silently."""
    try:
        send_and_wait_ok(ser, "M110 N0", log=log)
    except Exception:
        pass


def open_session(port=None, baud=None, log=None):
    """Resolve port/baud, open the link, return (ser, baud_used)."""
    if port is None:
        port = find_printer()
        if port is None:
            print("marlin_serial: error: no printer found. "
                  "Pass --port COMx explicitly.", file=sys.stderr)
            raise SystemExit(2)
    if baud is not None:
        return open_link(port, baud), baud
    last = None
    for b in TRY_BAUDS:
        try:
            return open_link(port, b), b
        except SystemExit as e:
            last = e  # try next baud
    raise last if last is not None else SystemExit(2)


def stream_file(ser, path, guard_check, log=None, timeout=30.0):
    """Send a gcode file line by line; returns lines_sent count."""
    with open(path, "r", encoding="ascii", errors="replace") as f:
        cmds = []
        for raw in f.read().splitlines():
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            cmds.append(line)
    total = len(cmds)
    for i, line in enumerate(cmds, 1):
        guard_check(line)  # raises on blocked lines
        if send_and_wait_ok(ser, line, timeout=timeout, log=log) == "resend":
            # Re-send once; a second resend aborts the stream.
            if send_and_wait_ok(ser, line, timeout=timeout, log=log) == "resend":
                raise MarlinError("resend storm on %r" % line)
        print("%d/%d" % (i, total), flush=True)
    return total
