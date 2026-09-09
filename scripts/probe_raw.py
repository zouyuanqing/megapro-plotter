"""Raw serial interrogation: boot listen + M115 across baud rates.

Usage: python scripts/probe_raw.py COM7 [baud ...]
No writes except a single M115 per baud. Read-only diagnostic.
"""

import sys
import time

sys.path.insert(0, "src")

from megapro.transport.marlin_serial import open_link

DEFAULT_BAUDS = (9600, 19200, 38400, 57600, 76800, 115200, 230400, 250000)


def interrogate(port, baud, listen_s=2.0, ask_s=3.0):
    try:
        ser = open_link(port, baud)
    except SystemExit:
        print(f"{baud}: open-fail")
        return
    ser.timeout = 1.0
    print(f"{baud}: opened, listening {listen_s}s for boot chatter...")
    t0 = time.time()
    boot = []
    while time.time() - t0 < listen_s:
        data = ser.readline()
        if data:
            boot.append(data)
            print(f"  boot: {data[:100]!r}")
    if not boot:
        print("  boot: (silence)")
    ser.write(b"M115\n")
    print("  -> M115 sent, reading replies...")
    t0 = time.time()
    heard = False
    while time.time() - t0 < ask_s:
        data = ser.readline()
        if not data:
            continue
        heard = True
        print(f"  <- {data[:120]!r}")
        if data.decode("ascii", "replace").strip().lower().startswith("ok"):
            break
    if not heard:
        print("  <- (silence)")
    ser.close()


if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
    bauds = [int(a) for a in sys.argv[2:]] or list(DEFAULT_BAUDS)
    for baud in bauds:
        interrogate(port, baud)
