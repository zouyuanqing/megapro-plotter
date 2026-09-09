"""End-to-end chain test against a live emulated Marlin over TCP serial.

No printer hardware required. A background thread speaks Marlin-flavored
replies (M115/M503/M119/M114 canned responses, `ok` per line) on a
localhost TCP port; the REAL transport (serial_for_url socket://) and the
REAL CLI main() run the full probe -> profile -> stream chain against it.
This proves the verification chain beyond unit fakes.
"""

import socket
import threading
from pathlib import Path

import pytest

serial = pytest.importorskip("serial")
from serial import serial_for_url

from megapro.cli import main as cli

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

FIRMWARE = ("FIRMWARE_NAME:Anycubic Mega Pro sim "
            "PROTOCOL_VERSION:1.0 MACHINE_TYPE:loopback")


class MarlinSim(threading.Thread):
    """Minimal Marlin replier; records every command line received."""

    def __init__(self):
        super().__init__(daemon=True)
        self.received = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(10)
        self.port = self._srv.getsockname()[1]

    def run(self):
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(10)
                buf = b""
                try:
                    while True:
                        chunk = conn.recv(256)
                        if not chunk:
                            break  # client went away; wait for next session
                        buf += chunk
                        while b"\n" in buf:
                            raw, buf = buf.split(b"\n", 1)
                            line = raw.decode("ascii", errors="replace").strip()
                            if not line:
                                continue
                            self.received.append(line)
                            cmd = line.split()[0].upper()
                            if cmd == "M112":
                                return
                            if cmd == "M115":
                                conn.sendall(f"{FIRMWARE}\nok\n".encode("ascii"))
                            elif cmd == "M503":
                                conn.sendall(b"echo:steps ok\nok\n")
                            elif cmd == "M119":
                                conn.sendall(b"ok X:open Y:open Z:open\n")
                            elif cmd == "M114":
                                conn.sendall(b"ok X:0 Y:0 Z:0\n")
                            else:
                                conn.sendall(b"ok\n")
                except OSError:
                    return

    def stop(self):
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture()
def sim():
    server = MarlinSim()
    server.start()
    yield server
    server.stop()


def _session_for(url):
    def fake_open(port, baud=None, log=None):
        ser = serial_for_url(url, baudrate=baud or 250000, timeout=2.0)
        return ser, baud or 250000

    return fake_open


def test_full_chain_probe_then_stream(monkeypatch, tmp_path, sim):
    """probe fills profile from live replies; stream acks every line."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "open_session",
                        _session_for(f"socket://127.0.0.1:{sim.port}"))
    assert cli.main(["probe", "--port", f"socket://127.0.0.1:{sim.port}"]) == 0
    profile = (tmp_path / "profiles" / "mega-pro-marlin.yaml").read_text()
    assert "Anycubic Mega Pro sim" in profile

    sent_before = len(sim.received)
    assert cli.main(["stream", "--port", f"socket://127.0.0.1:{sim.port}",
                     str(EXAMPLES / "hello_pen.gcode")]) == 0
    assert len(sim.received) > sent_before
    assert not any(w.split()[0].upper() in ("M104", "M140", "M3", "G6")
                   for w in sim.received)


def test_estop_over_live_link(monkeypatch, sim):
    monkeypatch.setattr(cli, "open_session",
                        _session_for(f"socket://127.0.0.1:{sim.port}"))
    assert cli.main(["estop", "--port", f"socket://127.0.0.1:{sim.port}"]) == 0
    assert sim.received and sim.received[-1] == "M112"
