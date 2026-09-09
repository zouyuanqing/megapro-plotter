"""Transport tests: Marlin request/response plus the safety guard.

No hardware: all serial I/O goes through tests.fake_serial.FakeSerial.
``src`` is already on ``sys.path`` via tests/conftest.py, so no path hacks
here.
"""

import pytest

from tests.fake_serial import FakeSerial
from megapro.transport.marlin_serial import (
    MarlinError,
    send_and_wait_ok,
    settle_link,
)
from megapro.safety.guard import GuardReject
from megapro.safety.guard import check as guard_check


def guarded_send(ser, line, **kwargs):
    """guard.check + send_and_wait_ok: blocked lines never reach the wire."""
    guard_check(line, **kwargs)
    return send_and_wait_ok(ser, line)


def test_ok_first_try():
    ser = FakeSerial(script=[["ok"]])
    assert send_and_wait_ok(ser, "G28 X Y") == "ok"
    assert ser.written == [b"G28 X Y\n"]


def test_echo_then_ok():
    ser = FakeSerial(
        script=[["echo:Unknown command: \"M999\"", "T:25.1 /0.0 B:24.8 /0.0 @:0 B@:0", "ok"]]
    )
    assert send_and_wait_ok(ser, "M105") == "ok"
    assert ser.written == [b"M105\n"]


def test_resend_once_then_ok():
    ser = FakeSerial(script=[["Resend:1"], ["ok"]])
    assert send_and_wait_ok(ser, "N1 G28 X Y") == "resend"
    assert send_and_wait_ok(ser, "N1 G28 X Y") == "ok"
    assert ser.written == [b"N1 G28 X Y\n"] * 2


def test_timeout_maps_link_lost():
    ser = FakeSerial(mode="silent")
    with pytest.raises(TimeoutError):
        send_and_wait_ok(ser, "M105", timeout=0.2)


def test_guard_blocks_heater_laser():
    ser = FakeSerial(script=[["ok"]] * 4)
    for line in ("M104 S0", "M140 S0", "M3 S255", "G6 S128"):
        with pytest.raises(GuardReject):
            guarded_send(ser, line)
    assert ser.written == []


def test_z_home_refused_by_default():
    with pytest.raises(GuardReject):
        guard_check("G28 Z")
    guard_check("G28 Z", allow_z=True)  # passes: no exception
    guard_check("G28 X Y")  # XY homing never needs allow_z


def test_m110_resync():
    ser = FakeSerial(script=[["ok"]])
    assert send_and_wait_ok(ser, "M110 N0") == "ok"
    assert ser.written == [b"M110 N0\n"]


def test_settle_drains_flood_to_ok():
    ser = FakeSerial()
    ser._pending = [b"start\n", b"echo:V1.2.9\n",
                    b"echo:SD init fail\n", b"ok T:21.0 /0.0\n"]
    assert settle_link(ser, boot_timeout=2.0, idle_settle=0.2) is True
    assert ser.written == []  # write-free
    assert ser._pending == []


def test_settle_warm_idle_returns_false_fast():
    ser = FakeSerial(mode="silent")
    assert settle_link(ser, boot_timeout=2.0, idle_settle=0.2) is False


def test_settle_timeout_returns_false():
    ser = FakeSerial(mode="silent")
    assert settle_link(ser, boot_timeout=0.3, idle_settle=10.0) is False
