"""Bisect DTR behavior: does the board answer M115 after a 3s settle?

Usage: python scripts/bisect_dtr.py COM7 [baud]
Modes: default (DTR untouched) vs dtr=False held. Prints what happens.
"""

import sys
import time

sys.path.insert(0, "src")

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
baud = int(sys.argv[2]) if len(sys.argv) > 2 else 250000


def attempt(label, set_dtr):
    ser = serial.Serial(port, baud, timeout=1)
    if set_dtr is not None:
        ser.dtr = False if set_dtr == "low" else True
        ser.rts = False
    time.sleep(3.0)
    ser.reset_input_buffer()
    ser.write(b"M115\n")
    t0 = time.time()
    heard = []
    while time.time() - t0 < 4.0:
        data = ser.readline()
        if data:
            heard.append(data)
            print(f"  [{label}] <- {data[:100]!r}", flush=True)
            if data.decode("ascii", "replace").strip().lower().startswith("ok"):
                break
    print(f"  [{label}] lines={len(heard)}", flush=True)
    ser.close()
    time.sleep(1.0)


print("mode=default (untouched):", flush=True)
attempt("default", None)
print("mode=dtr-low-held:", flush=True)
attempt("low", "low")
