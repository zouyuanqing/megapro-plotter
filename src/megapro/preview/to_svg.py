"""Preview: render pen toolpaths as a standalone SVG viewable in a browser.

Pen-down moves are blue, travel moves are red dashed, and the paper
outline is drawn as a black border.
"""

from __future__ import annotations

__all__ = ["toolpath_to_svg"]


def _fmt(v: float) -> str:
    s = f"{float(v):.3f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "", "-") else s


def toolpath_to_svg(polylines, path, width=210, height=210):
    """Write *polylines* to *path* as a standalone SVG; return the path."""
    polys = [list(p) for p in polylines if len(p) >= 2]
    xs = [0.0, float(width)] + [x for p in polys for x, y in p]
    ys = [0.0, float(height)] + [y for p in polys for x, y in p]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    sw = max(maxx - minx, maxy - miny, 1.0) / 400.0
    parts = [f'<rect x="0" y="0" width="{_fmt(width)}" height="{_fmt(height)}"'
             ' fill="white" stroke="black" stroke-width="' + _fmt(sw) + '"/>']
    cur = (0.0, 0.0)
    for p in polys:
        if tuple(cur) != tuple(p[0]):
            parts.append(f'<line x1="{_fmt(cur[0])}" y1="{_fmt(cur[1])}"'
                         f' x2="{_fmt(p[0][0])}" y2="{_fmt(p[0][1])}"'
                         ' stroke="red" stroke-width="' + _fmt(sw) + '" stroke-dasharray="'
                         + _fmt(4 * sw) + " " + _fmt(3 * sw) + '"/>')
        pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in p)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="blue"'
                     f' stroke-width="{_fmt(sw)}"/>')
        cur = p[-1]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="{_fmt(minx)} {_fmt(miny)} {_fmt(maxx - minx)} {_fmt(maxy - miny)}">\n'
           + "\n".join(parts) + "\n</svg>\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return str(path)
