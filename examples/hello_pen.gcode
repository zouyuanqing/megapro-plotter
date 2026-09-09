; hello_pen.gcode - MegaPro M0 pen demo: 20 mm square
; absolute positioning, millimetres
G90
G21
; lift pen before travel
G1 Z1.0 F300
; move to start corner (0, 0)
G1 X0 Y0 F1200
; --- edge 1: (0,0) -> (20,0) ---
G1 Z0.0 F300
G1 X20 Y0 F1200
G1 Z1.0 F300
; --- edge 2: (20,0) -> (20,20) ---
G1 X20 Y0 F1200
G1 Z0.0 F300
G1 X20 Y20 F1200
G1 Z1.0 F300
; --- edge 3: (20,20) -> (0,20) ---
G1 X20 Y20 F1200
G1 Z0.0 F300
G1 X0 Y20 F1200
G1 Z1.0 F300
; --- edge 4: (0,20) -> (0,0) ---
G1 X0 Y20 F1200
G1 Z0.0 F300
G1 X0 Y0 F1200
G1 Z1.0 F300
; job done, wait for moves to finish
M400
