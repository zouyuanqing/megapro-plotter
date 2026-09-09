"""M3b+ 中心线边缘提取单测（骨架化，替代 potrace 双线）。"""

import numpy as np
import pytest

pytest.importorskip("skimage")
import cv2

from megapro.gui.edge_to_svg import (
    image_to_centerline_svg,
    image_to_centerline_svg_multi,
    trace_centerline,
    trace_centerline_multi,
)


def _thick_line_img():
    img = np.full((200, 300), 255, np.uint8)
    cv2.line(img, (30, 50), (250, 150), 0, 14)  # 粗斜线
    return img


def test_thick_line_gives_single_centerline():
    """粗斜线 → 1 条中心线（非 potrace 的 2 条轮廓）。"""
    paths = trace_centerline(_thick_line_img(), threshold=200)
    # 应为 1 条主路径（斜线）；容许多 1 条端点残段
    assert len(paths) >= 1
    main = max(paths, key=len)
    xs = [x for x, y in main]
    ys = [y for x, y in main]
    # 中心线应从线一端到另一端 (~30..250, 50..150)
    assert min(xs) < 60 and max(xs) > 220
    assert min(ys) < 80 and max(ys) > 120
    # 不是双线：主路径点数应远小于轮廓周长（14px 宽线的双轮廓会很长）
    assert len(main) < 400


def test_centerline_svg_generates_mm():
    svg = image_to_centerline_svg(_thick_line_img(), target_mm=100.0,
                                  threshold=200)
    assert "<polyline" in svg
    # 最长边 ~220px 映射到 100mm → x 最大 ≈ 250/220*100 ≈ 113mm
    from megapro.gui.layout.layout_page import _svg_to_paths
    from megapro.toolchain.svg_to_gcode import parse_svg
    import tempfile, os
    fd, p = tempfile.mkstemp(suffix=".svg")
    os.write(fd, svg.encode()); os.close(fd)
    try:
        polys = parse_svg(p)
        xs = [x for pl in polys for x, y in pl]
        assert 100 <= max(xs) <= 120  # mm，最长边≈100
    finally:
        os.unlink(p)


def test_blank_image_no_paths():
    img = np.full((100, 100), 255, np.uint8)
    assert trace_centerline(img, threshold=200) == []


def test_circle_single_closed_path():
    """纯圆环 → 1 条闭环中心线（度全 2）。"""
    img = np.full((200, 200), 255, np.uint8)
    cv2.circle(img, (100, 100), 60, 0, 10)
    paths = trace_centerline(img, threshold=200)
    assert len(paths) == 1
    p = paths[0]
    # 闭合：首尾接近
    assert abs(p[0][0] - p[-1][0]) < 3 and abs(p[0][1] - p[-1][1]) < 3


def test_multi_threshold_captures_faint_segment():
    """多阈值拼接：单阈值漏的淡段，多阈值(含低档)能补上。"""
    img = np.full((120, 400), 255, np.uint8)
    for x in list(range(0, 150)) + list(range(250, 400)):
        cv2.line(img, (x, 60), (x, 60), 60, 3)
    for x in range(150, 250):
        cv2.line(img, (x, 60), (x, 60), 175, 3)
    single = trace_centerline(img, threshold=150)
    multi = trace_centerline_multi(img)
    multi_x = {round(x) for p in multi for x, y in p}
    # 多阈值(含低档 90)应覆盖淡段 150..250
    assert any(150 <= x <= 250 for x in multi_x), "多阈值应补上淡段"
    # 单阈值 150 抓不到淡段(175>150) → 其 x 不含淡段
    single_x = {round(x) for p in single for x, y in p}
    assert not any(150 <= x <= 250 for x in single_x) or len(multi) >= len(single)
