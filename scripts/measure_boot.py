"""Measure how long after open a board takes to answer M115.

Usage: python scripts/measure_boot.py COM7 [baud] [budget_s]
Sends M115 every 2s until first reply; prints elapsed time of answer.
Read-only diagnostic (no motion).
"""

import sys
import time

sys.path.insert(0, "src")

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
baud = int(sys.argv[2]) if len(sys.argv) > 2 else 250000
budget = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

ser = serial.Serial(port, baud, timeout=1)
ser.dtr = False
ser.rts = False
ser.reset_input_buffer()
t0 = time.time()
answered = False
while time.time() - t0 < budget:
    ser.write(b"M115\n")
    t1 = time.time()
    while time.time() - t1 < 2.0:
        data = ser.readline()
        if data:
            print(f"{time.time() - t0:.1f}s <- {data[:120]!r}", flush=True)
            answered = True
            break
    if answered:
        break
    time.sleep(0.2)
print("ANSWERED" if answered else "NO-ANSWER", flush=True)
ser.close()
