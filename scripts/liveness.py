"""Board liveness test: poll M105 continuously for 25s after open.

Usage: python scripts/liveness.py COM7 [baud]
Distinguishes 'slow boot' (answers start late, keep coming) from
'dying board' (answers stop after a few seconds -> power/crash issue).
"""

import sys
import time

sys.path.insert(0, "src")

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
baud = int(sys.argv[2]) if len(sys.argv) > 2 else 250000

ser = serial.Serial(port, baud, timeout=1)
ser.dtr = False
ser.rts = False
ser.reset_input_buffer()
t0 = time.time()
last_ok_at = None
while time.time() - t0 < 25.0:
    ser.write(b"M105\n")
    t1 = time.time()
    while time.time() - t1 < 1.5:
        data = ser.readline()
        if data:
            print(f"{time.time() - t0:5.1f}s <- {data[:90]!r}", flush=True)
            last_ok_at = time.time() - t0
            break
    time.sleep(0.5)
print(f"last answer at: {last_ok_at}", flush=True)
ser.close()
