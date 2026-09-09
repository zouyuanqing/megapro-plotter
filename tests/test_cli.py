"""CLI regression tests: flag order, session unpack, baud sweep, guards.

No hardware required — open_session / send_and_wait_ok are monkeypatched.
Covers the three bugs fixed during M0 bring-up.
"""

import pytest

from megapro.cli import main as cli
from megapro.transport.marlin_serial import MarlinError


class FakeSession:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(bytes(data))

    def readline(self):
        return b"ok\n"

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


def _ok_responder(mapping):
    def respond(session, line, timeout=5.0, log=None):
        if line in mapping:
            reply = mapping[line]
            if isinstance(reply, Exception):
                raise reply
            return reply
        return "ok"
    return respond


def test_port_flag_after_subcommand(monkeypatch, tmp_path):
    """--port given after `probe` must be accepted (bug: unrecognized args)."""
    monkeypatch.chdir(tmp_path)
    sessions = []

    def fake_open(port, baud=None, log=None):
        sessions.append((port, baud))
        return FakeSession(), baud or 250000

    monkeypatch.setattr(cli, "open_session", fake_open)
    monkeypatch.setattr(
        cli, "send_and_wait_ok",
        _ok_responder({"M115": "FIRMWARE_NAME:Test", "M503": "ok",
                       "M119": "ok", "M114": "ok"}),
    )
    assert cli.main(["probe", "--port", "COM7"]) == 0
    assert sessions and sessions[0][0] == "COM7"


def test_probe_baud_sweep_falls_back(monkeypatch, tmp_path):
    """Silent at 250000 must fall through to 115200 (bug: single-baud only)."""
    monkeypatch.chdir(tmp_path)
    tried = []

    def fake_open(port, baud=None, log=None):
        tried.append(baud)
        return FakeSession(), baud

    def respond(session, line, timeout=5.0, log=None):
        if tried[-1] == 250000 and line == "M115":
            raise TimeoutError("silent")
        return {"M115": "FIRMWARE_NAME:Test"}.get(line, "ok")

    monkeypatch.setattr(cli, "open_session", fake_open)
    monkeypatch.setattr(cli, "send_and_wait_ok", respond)
    assert cli.main(["probe", "--port", "COM7"]) == 0
    assert tried[0] == 250000
    profile = (tmp_path / "profiles" / "mega-pro-marlin.yaml").read_text()
    assert "115200" in profile


def test_probe_all_silent_is_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "open_session",
                        lambda p, b=None, log=None: (FakeSession(), b or 250000))

    def silent(session, line, timeout=5.0, log=None):
        raise TimeoutError("silent")

    monkeypatch.setattr(cli, "send_and_wait_ok", silent)
    assert cli.main(["probe", "--port", "COM7"]) == 2
    assert "no reply" in capsys.readouterr().err


def test_stream_blocked_line_never_written(monkeypatch, tmp_path):
    """M104 in a gcode file must abort with exit 2 and zero bytes on wire."""
    monkeypatch.chdir(tmp_path)
    gcode = tmp_path / "evil.gcode"
    gcode.write_text("G90\nG1 X10 F1200\nM104 S200\n", encoding="ascii")
    opened = []

    def fake_open(port, baud=None, log=None):
        sess = FakeSession()
        opened.append(sess)
        return sess, baud or 250000

    monkeypatch.setattr(cli, "open_session", fake_open)
    from megapro.transport import marlin_serial
    monkeypatch.setattr(cli, "send_and_wait_ok",
                        marlin_serial.send_and_wait_ok)
    assert cli.main(["stream", "--port", "COM7", str(gcode)]) == 2
    assert opened and all(b"M104" not in w for w in opened[0].written)


def test_jog_z_requires_allow_z(monkeypatch):
    """Z jog without --allow-z must fail before touching the port."""
    def boom(*a, **k):
        raise AssertionError("port must not be opened")

    monkeypatch.setattr(cli, "open_session", boom)
    assert cli.main(["jog", "--port", "COM7", "--z", "2"]) == 2


def test_estop_sends_m112(monkeypatch):
    monkeypatch.setattr(cli, "open_session",
                        lambda p, b=None, log=None: (FakeSession(), b or 250000))
    sessions = []
    orig = cli.open_session

    def spy(port, baud=None, log=None):
        sess, used = orig(port, baud, log=log)
        sessions.append(sess)
        return sess, used

    monkeypatch.setattr(cli, "open_session", spy)
    assert cli.main(["estop", "--port", "COM7"]) == 0
    assert any(b"M112" in w for w in sessions[0].written)


def test_home_defaults_to_xy_only(monkeypatch, tmp_path):
    """`home` with no --axis must emit exactly `G28 X Y` (never Z)."""
    monkeypatch.chdir(tmp_path)
    sent = []

    monkeypatch.setattr(cli, "open_session",
                        lambda p, b=None, log=None: (FakeSession(), b or 250000))
    monkeypatch.setattr(cli, "send_and_wait_ok",
                        lambda s, line, timeout=5.0, log=None: sent.append(line) or "ok")
    assert cli.main(["home", "--port", "COM7"]) == 0
    assert sent[-1] == "G28 X Y"  # lift prelude (G91/G0 Z10/G90) precedes it
    assert sent[0] == "G91" and "G0 Z10" in sent[1]


def test_console_roundtrip_and_quit(monkeypatch, tmp_path, capsys):
    """console keeps one session: M114 ok printed, .quit exits 0."""
    monkeypatch.chdir(tmp_path)
    opened = []

    def fake_open(port, baud=None, log=None):
        opened.append((port, baud))
        return FakeSession(), baud or 250000

    monkeypatch.setattr(cli, "open_session", fake_open)
    monkeypatch.setattr(cli, "send_and_wait_ok", lambda s, l, **k: "ok")
    inputs = iter(["M114", ".quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    assert cli.main(["console", "--port", "COM7"]) == 0
    assert len(opened) == 1  # exactly one open for the whole session
    assert "ok" in capsys.readouterr().out


def test_console_blocks_heater(monkeypatch, tmp_path, capsys):
    """console guards every line: M104 never sent, session continues."""
    monkeypatch.chdir(tmp_path)
    sess = FakeSession()
    monkeypatch.setattr(cli, "open_session",
                        lambda p, b=None, log=None: (sess, b or 250000))
    monkeypatch.setattr(cli, "send_and_wait_ok", lambda s, l, **k: "ok")
    inputs = iter(["M104 S200", "M114", ".quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    assert cli.main(["console", "--port", "COM7"]) == 0
    assert sess.written == []  # M104 blocked; M114 uses patched send
    out = capsys.readouterr().out
    assert "blocked" in out


def test_move_travel_hops_first(monkeypatch, tmp_path):
    """move to XY lifts +5 before travel and stays up."""
    monkeypatch.chdir(tmp_path)
    sent = []
    monkeypatch.setattr(cli, "open_session",
                        lambda p, b=None, log=None: (FakeSession(), b or 250000))
    monkeypatch.setattr(cli, "send_and_wait_ok",
                        lambda s, line, timeout=5.0, log=None: sent.append(line) or "ok")
    assert cli.main(["move", "--port", "COM7", "--x", "10", "--y", "20"]) == 0
    assert sent[0] == "G90"
    assert "G0 Z5" in sent[2]  # safety hop (after G91 mode switch)
    assert sent[-1].startswith("G0 X10 Y20")


def test_move_draw_skips_hop(monkeypatch, tmp_path):
    """move --draw goes straight to target at current height."""
    monkeypatch.chdir(tmp_path)
    sent = []
    monkeypatch.setattr(cli, "open_session",
                        lambda p, b=None, log=None: (FakeSession(), b or 250000))
    monkeypatch.setattr(cli, "send_and_wait_ok",
                        lambda s, line, timeout=5.0, log=None: sent.append(line) or "ok")
    assert cli.main(["move", "--port", "COM7", "--x", "10", "--draw"]) == 0
    assert not any("Z5" in line for line in sent)
    assert sent[-1].startswith("G0 X10")
