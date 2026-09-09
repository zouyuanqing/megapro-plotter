"""M1a vector toolchain: SVG -> sorted polylines -> Marlin pen G-code (stdlib only)."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

__all__ = ["parse_svg", "nearest_neighbor_sort", "emit_gcode", "svg_file_to_gcode"]

_TOL = 0.05  # curve-flattening tolerance, mm
_IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TOK = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

def _fmt(v: float) -> str:
    s = f"{float(v):.3f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "", "-") else s

def _mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2, a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)

def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)

def _parse_transform(s):
    m = _IDENT
    for name, args in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", s or ""):
        nums = [float(v) for v in _NUM.findall(args)]
        if name == "translate":
            m = _mul(m, (1, 0, 0, 1, nums[0] if nums else 0.0, nums[1] if len(nums) > 1 else 0.0))
        elif name == "scale":
            sx = nums[0] if nums else 1.0
            m = _mul(m, (sx, 0, 0, nums[1] if len(nums) > 1 else sx, 0, 0))
        elif name == "matrix" and len(nums) >= 6:
            m = _mul(m, tuple(nums[:6]))
        elif name == "rotate" and nums:
            a = math.radians(nums[0])
            cos_a, sin_a = math.cos(a), math.sin(a)
            rot = (cos_a, sin_a, -sin_a, cos_a, 0, 0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                rot = _mul((1, 0, 0, 1, cx, cy),
                           _mul(rot, (1, 0, 0, 1, -cx, -cy)))
            m = _mul(m, rot)
        elif name == "skewX" and nums:
            m = _mul(m, (1, 0, math.tan(math.radians(nums[0])), 1, 0, 0))
        elif name == "skewY" and nums:
            m = _mul(m, (1, math.tan(math.radians(nums[0])), 0, 1, 0, 0))
        else:
            raise ValueError(f"unsupported SVG transform: {name}({args})")
    return m

def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = math.hypot(dx, dy)
    return math.hypot(p[0] - a[0], p[1] - a[1]) if den == 0 else \
        abs(dx * (a[1] - p[1]) - dy * (a[0] - p[0])) / den

def _mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

def _flat_cubic(out, p0, p1, p2, p3, depth=0):
    if max(_seg_dist(p1, p0, p3), _seg_dist(p2, p0, p3)) <= _TOL or depth >= 12:
        out.append(p3)
        return
    q0, q1, q2 = _mid(p0, p1), _mid(p1, p2), _mid(p2, p3)
    r0, r1 = _mid(q0, q1), _mid(q1, q2)
    s = _mid(r0, r1)
    _flat_cubic(out, p0, q0, r0, s, depth + 1)
    _flat_cubic(out, s, r1, q2, p3, depth + 1)

def _flat_quad(out, p0, p1, p2, depth=0):
    if _seg_dist(p1, p0, p2) <= _TOL or depth >= 12:
        out.append(p2)
        return
    q0, q1 = _mid(p0, p1), _mid(p1, p2)
    s = _mid(q0, q1)
    _flat_quad(out, p0, q0, s, depth + 1)
    _flat_quad(out, s, q1, p2, depth + 1)

def _flat_arc(out, x1, y1, rx, ry, phi_deg, large, sweep, x2, y2):
    if (x1, y1) == (x2, y2):
        return
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        out.append((x2, y2))
        return
    phi = math.radians(phi_deg % 360.0)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den > 0 else 0.0
    sgn = -1.0 if large == sweep else 1.0
    cxp, cyp = sgn * co * rx * y1p / ry, -sgn * co * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x1 + x2) / 2, sp * cxp + cp * cyp + (y1 + y2) / 2
    ux, uy, vx, vy = (x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry
    t1 = math.atan2(uy, ux)
    dt = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    if sweep and dt < 0:
        dt += 2 * math.pi
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - _TOL / max(rx, ry))))
    n = max(1, int(math.ceil(abs(dt) / step))) if step > 0 else 1
    for k in range(1, n + 1):
        t = t1 + dt * k / n
        ct, st = math.cos(t), math.sin(t)
        out.append((cx + rx * cp * ct - ry * sp * st, cy + rx * sp * ct + ry * cp * st))

def _parse_path_d(d):
    toks = _TOK.findall(d or "")
    polys, cur, pos, sub = [], [], (0.0, 0.0), (0.0, 0.0)
    cmd, rel, first_m, prev_cmd, prev_c2, prev_q = None, False, True, None, None, None
    i, n = 0, len(toks)
    def line_to(p):
        nonlocal pos, prev_cmd
        cur.append(p)
        pos, prev_cmd = p, None
    def cubic_to(p1, p2, p3):
        nonlocal pos, prev_cmd, prev_c2
        _flat_cubic(cur, pos, p1, p2, p3)
        pos, prev_cmd, prev_c2 = p3, "C", p2
    def quad_to(p1, p2):
        nonlocal pos, prev_cmd, prev_q
        _flat_quad(cur, pos, p1, p2)
        pos, prev_cmd, prev_q = p2, "Q", p1
    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v
    def pt():
        x, y = num(), num()
        return (pos[0] + x, pos[1] + y) if rel else (x, y)
    def close():
        nonlocal cur, pos
        if cur:
            if math.hypot(cur[-1][0] - sub[0], cur[-1][1] - sub[1]) > 1e-9:
                cur.append(sub)
            polys.append(cur)
            cur = []
        pos = sub
    need = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}
    while i < n:
        t = toks[i]
        if t.isalpha():
            i += 1
            up = t.upper()
            if up not in need and up != "Z":
                cmd = None
                continue
            cmd, rel = up, t.islower()
            if up == "M":
                first_m = True
            elif up == "Z":
                close()
                prev_cmd = None
            continue
        if cmd is None:
            i += 1
            continue
        j = i
        while j < n and not toks[j].isalpha():
            j += 1
        if j - i < need[cmd]:
            i = j
            continue
        if cmd == "M":
            p = pt()
            if first_m:
                if cur:
                    polys.append(cur)
                    cur = []
                sub = p
                cur.append(p)
                pos, first_m = p, False
            else:
                line_to(p)
        elif cmd == "L":
            line_to(pt())
        elif cmd == "H":
            v = num()
            line_to((pos[0] + v if rel else v, pos[1]))
        elif cmd == "V":
            v = num()
            line_to((pos[0], pos[1] + v if rel else v))
        elif cmd == "C":
            cubic_to(pt(), pt(), pt())
        elif cmd == "S":
            c1 = (2 * pos[0] - prev_c2[0], 2 * pos[1] - prev_c2[1]) if prev_cmd == "C" else pos
            cubic_to(c1, pt(), pt())
        elif cmd == "Q":
            quad_to(pt(), pt())
        elif cmd == "T":
            q = (2 * pos[0] - prev_q[0], 2 * pos[1] - prev_q[1]) if prev_cmd == "Q" else pos
            quad_to(q, pt())
        elif cmd == "A":
            rx, ry, rot, large, sweep = num(), num(), num(), int(num()), int(num())
            p = pt()
            _flat_arc(cur, pos[0], pos[1], rx, ry, rot, large, sweep, p[0], p[1])
            pos, prev_cmd = p, None
    if cur:
        polys.append(cur)
    return polys

def _attr(el, name, default=0.0):
    m = _NUM.search(el.get(name) or "")
    return float(m.group(0)) if m else default

def _circle_steps(r):
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - _TOL / r)))
    return max(8, int(math.ceil(2 * math.pi / step))) if step > 0 else 8

def _round_rect(x, y, w, h, rx, ry):
    pts = [(x + rx, y), (x + w - rx, y)]
    r = min(rx, ry)
    k = max(2, int(math.ceil((math.pi / 2) / (2 * math.acos(max(-1.0, min(1.0, 1 - _TOL / r)))))))
    corners = ((x + w - rx, y + ry, -90, x + w, y + h - ry), (x + w - rx, y + h - ry, 0, x + rx, y + h),
               (x + rx, y + h - ry, 90, x, y + ry), (x + rx, y + ry, 180, None, None))
    for cx, cy, a0, nx, ny in corners:
        for s in range(1, k + 1):
            a = math.radians(a0 + 90 * s / k)
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
        if nx is not None:
            pts.append((nx, ny))
    pts.append(pts[0])
    return pts

def _shape_polys(el, tag):
    if tag == "path":
        return _parse_path_d(el.get("d"))
    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in _NUM.findall(el.get("points") or "")]
        pts = [(nums[k], nums[k + 1]) for k in range(0, len(nums) - 1, 2)]
        if tag == "polygon" and len(pts) >= 2 and pts[0] != pts[-1]:
            pts.append(pts[0])
        return [pts] if len(pts) >= 2 else []
    if tag == "line":
        return [[(_attr(el, "x1"), _attr(el, "y1")), (_attr(el, "x2"), _attr(el, "y2"))]]
    if tag == "rect":
        x, y, w, h = _attr(el, "x"), _attr(el, "y"), _attr(el, "width"), _attr(el, "height")
        if w <= 0 or h <= 0:
            return []
        rx = min(_attr(el, "rx", _attr(el, "ry")), w / 2)
        ry = min(_attr(el, "ry", rx), h / 2)
        if rx <= 0 or ry <= 0:
            return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
        return [_round_rect(x, y, w, h, rx, ry)]
    if tag == "circle":
        r = _attr(el, "r")
        if r <= 0:
            return []
        cx, cy, k = _attr(el, "cx"), _attr(el, "cy"), _circle_steps(r)
        return [[(cx + r * math.cos(2 * math.pi * s / k), cy + r * math.sin(2 * math.pi * s / k))
                 for s in range(k + 1)]]
    if tag == "ellipse":
        rx, ry = _attr(el, "rx"), _attr(el, "ry")
        if rx <= 0 or ry <= 0:
            return []
        cx, cy, k = _attr(el, "cx"), _attr(el, "cy"), _circle_steps(max(rx, ry))
        return [[(cx + rx * math.cos(2 * math.pi * s / k), cy + ry * math.sin(2 * math.pi * s / k))
                 for s in range(k + 1)]]
    return None

def _dedup(pts):
    out = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-9:
            out.append((float(p[0]), float(p[1])))
    return out

def _walk(el, ctm, out):
    if not isinstance(el.tag, str):
        return
    m = _mul(ctm, _parse_transform(el.get("transform")))
    polys = _shape_polys(el, el.tag.rsplit("}", 1)[-1])
    for p in polys or []:
        p = _dedup([_apply(m, x, y) for x, y in p])
        if len(p) >= 2:
            out.append(p)
    for child in el:
        _walk(child, m, out)

def parse_svg(path):
    """Read *path* and return a list of polylines (lists of (x, y) in mm)."""
    out: list = []
    _walk(ET.parse(path).getroot(), _IDENT, out)
    return out

def nearest_neighbor_sort(polylines):
    """Greedy travel sort from (0, 0); each polyline may be reversed."""
    rem = [list(p) for p in polylines if len(p) >= 2]
    ordered, cur = [], (0.0, 0.0)
    while rem:
        best = None
        for idx, p in enumerate(rem):
            d0 = math.hypot(cur[0] - p[0][0], cur[1] - p[0][1])
            d1 = math.hypot(cur[0] - p[-1][0], cur[1] - p[-1][1])
            cand = (d1, idx, True) if d1 < d0 else (d0, idx, False)
            if best is None or cand[0] < best[0]:
                best = cand
        p = rem.pop(best[1])
        ordered.append(p[::-1] if best[2] else p)
        cur = ordered[-1][-1]
    return ordered

def emit_gcode(polylines, pen_up=1.0, feed_xy=1200, feed_z=300):
    """Render *polylines* as pen G-code using the dialect header."""
    from megapro.dialect.marlin import header_lines
    lines = [h for h in header_lines() if h.split()[0] in ("G90", "G21", "G91")]
    fz, fxy = _fmt(feed_z), _fmt(feed_xy)
    for poly in polylines:
        if not poly:
            continue
        lines.append(f"G0 X{_fmt(poly[0][0])} Y{_fmt(poly[0][1])}")
        lines.append(f"G1 Z{_fmt(0)} F{fz}")
        lines.extend(f"G1 X{_fmt(x)} Y{_fmt(y)} F{fxy}" for x, y in poly)
        lines.append(f"G1 Z{_fmt(pen_up)} F{fz}")
    lines.append("M400")
    return "\n".join(lines) + "\n"

def svg_file_to_gcode(path, pen_up=1.0, feed_xy=1200, feed_z=300, sort=True):
    """Parse *path*, optionally travel-sort, and return pen G-code."""
    polys = parse_svg(path)
    if sort:
        polys = nearest_neighbor_sort(polys)
    return emit_gcode(polys, pen_up=pen_up, feed_xy=feed_xy, feed_z=feed_z)
