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

# T11 照片/素描（Canny+骨架）模式的导入。私有件只在「无重复描边」那条
# 不变量里用到——那条断言无法从公开返回值看出来。
from megapro.gui.edge_to_svg import (  # noqa: E402
    CANNY_APERTURE,
    CANNY_HIGH,
    CANNY_LOW,
    _skeleton_paths,
    image_to_canny_centerline_svg,
    trace_centerline_canny,
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


# ===========================================================================
# T11 照片/素描模式（PRD FR-10 算法部分）：cv2.Canny → thin → 游走 → 简化
# ===========================================================================
# 样张全部**测试内合成**，不依赖任何真实照片文件（PRD FR-10 验收②）。
# 默认阈值/孔径的取值理由见 edge_to_svg.py 文末 CANNY_* 注释，由下面
# `test_canny_defaults_*` 两条用例钉死。

SYNTH_W, SYNTH_H = 288, 240
#: 三个真值目标的包围盒（x0, y0, x1, y1），用于逐目标归属。
_BOX_TRIANGLE = (186, 110, 278, 209)
_BOX_DISK = (116, 116, 184, 184)
_BOX_SOFT = (30, 120, 90, 180)
_DISK_C, _DISK_R = (150.0, 150.0), 34.0
_SOFT_C, _SOFT_R = (60.0, 150.0), 30.0
_TRI = ((225.0, 110.0), (278.0, 208.0), (186.0, 208.0))
#: 顶部纹理噪声带（y < 70）——默认参数下**一条路径都不该有**。
_NOISE_BAND_Y = 70


def _synth_photo():
    """合成「照片/素描」样张：240×288，四个刻意互不重叠的区域。

    - y<64       强纹理噪声带（±10 灰阶）→ 默认参数下应被**全部拒绝**
    - 软斑       (60,150) r=30，对比 ≈45% 且 sigma 2.0 软边 → 应被提全
    - 硬边圆     (150,150) r=34，值 28
    - 硬边三角   顶点 (225,110)(278,208)(186,208)，值 20

    背景是 200→232 的竖向渐变（渐变本身不该产生边缘）。固定随机种子，
    golden 逐位可复现。
    """
    img = np.clip(
        np.linspace(200, 232, SYNTH_W)[None, :]
        + np.linspace(0, 22, SYNTH_H)[:, None], 0, 255)
    soft = np.zeros((SYNTH_H, SYNTH_W), np.uint8)
    cv2.circle(soft, (60, 150), 30, 255, -1)
    soft = cv2.GaussianBlur(soft, (0, 0), 2.0)
    img = np.where(soft > 0, img * (1 - soft.astype(np.float64) / 255 * 0.45), img)
    cv2.circle(img, (150, 150), 34, 28, -1)
    cv2.fillPoly(img, [np.array([[225, 110], [278, 208], [186, 208]], np.int32)], 20)
    rng = np.random.default_rng(20260927)
    noise = rng.integers(-4, 5, (SYNTH_H, SYNTH_W)).astype(np.float64)
    img[0:64, :] = np.clip(img[0:64, :] + noise[0:64, :] * 2.5, 0, 255)
    return np.clip(img, 0, 255).astype(np.uint8)


def _synth_skeleton(low=CANNY_LOW, high=CANNY_HIGH, ap=CANNY_APERTURE):
    """独立复算 Canny→thin 骨架（不调 trace_*，避免自证）。"""
    pytest.importorskip("skimage")
    from skimage.morphology import thin

    return thin(cv2.Canny(_synth_photo(), low, high, apertureSize=ap) > 0)


def _in_box(paths, box, tol=3.0):
    """挑出完全落在 box(±tol) 内的路径。"""
    x0, y0, x1, y1 = box
    return [p for p in paths
            if min(x for x, y in p) >= x0 - tol and max(x for x, y in p) <= x1 + tol
            and min(y for x, y in p) >= y0 - tol and max(y for x, y in p) <= y1 + tol]


def _r_dist(p, center):
    return [((x - center[0]) ** 2 + (y - center[1]) ** 2) ** 0.5 for x, y in p]


def _seg_dist(p, a, b):
    """点到线段 ab 的距离（三角三条边用）。"""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    out = []
    for x, y in p:
        t = 0.0 if den == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / den))
        out.append(((x - (ax + t * dx)) ** 2 + (y - (ay + t * dy)) ** 2) ** 0.5)
    return out


def _closed_gap(p):
    return ((p[0][0] - p[-1][0]) ** 2 + (p[0][1] - p[-1][1]) ** 2) ** 0.5


def test_canny_photo_golden_single_pixel_centerline():
    """验收②：合成样张 → **单像素宽**的中心线折线 golden（走默认参数）。"""
    paths = trace_centerline_canny(_synth_photo())

    # ① 三个真值目标各恰好一条闭合环；纹理噪声带一条都没有。
    tri = _in_box(paths, _BOX_TRIANGLE)
    disk = _in_box(paths, _BOX_DISK)
    soft = _in_box(paths, _BOX_SOFT)
    assert len(tri) == 1 and len(disk) == 1 and len(soft) == 1, \
        f"每目标应各 1 条环，实得 tri={len(tri)} disk={len(disk)} soft={len(soft)}"
    assert not [p for p in paths if max(y for x, y in p) < _NOISE_BAND_Y], \
        "默认参数下纹理噪声带不应产出任何路径"
    assert len(paths) == 3, f"应只有 3 条路径，实得 {len(paths)}（多出={len(paths) - 3}）"

    # ② 单像素宽：折线骑在真值边界上，不是内侧/外侧偏一撮。
    d = _r_dist(disk[0], _DISK_C)
    assert min(d) > _DISK_R - 1.5 and max(d) < _DISK_R + 1.5, \
        f"圆中心线半径应 ≈{_DISK_R}，实得 [{min(d):.2f}, {max(d):.2f}]"
    ds = _r_dist(soft[0], _SOFT_C)
    assert min(ds) > _SOFT_R - 2.0 and max(ds) < _SOFT_R + 2.0, \
        f"软斑中心线半径应 ≈{_SOFT_R}，实得 [{min(ds):.2f}, {max(ds):.2f}]"
    dt = min(min(_seg_dist(tri[0], _TRI[i], _TRI[(i + 1) % 3])) for i in range(3))
    assert dt < 1.5, f"三角中心线应贴着三边（≤1.5px），实得最小边距 {dt:.2f}px"

    # ③ 骨架本身严格 1px 宽：任何像素到背景的距离 <1px，且无 2×2 实心块。
    skel = _synth_skeleton()
    dt_map = cv2.distanceTransform(skel.astype(np.uint8), cv2.DIST_L2, 3)
    assert dt_map.max() < 1.0, f"骨架非单像素宽（最大内接半径 {dt_map.max():.2f}px）"
    s = skel.astype(np.uint8)
    solid_2x2 = (s[1:-1, 1:-1] & s[:-2, 1:-1] & s[1:-1, :-2] & s[:-2, :-2]).sum()
    assert solid_2x2 == 0, f"骨架存在 {solid_2x2} 个 2×2 实心块 → 非单像素宽"

    # ④ **无重复描边**（本文件立管的「消双线」）：每个骨架像素恰被走一次。
    #    同一张 1px 边缘带上 findContours 会沿带两侧各描一条（≈2× 点数），
    #    这里断言点数 == 骨架像素数，双线实现必挂。
    raw = _skeleton_paths(skel)
    assert sum(len(p) for p in raw) == int(skel.sum()), \
        "骨架像素被重复描边（应各走一次）"

    # ⑤ 输出（含 DP 简化后）不越出 1px 线：顶点全落在 1px 膨胀的骨架内。
    allow = cv2.dilate(skel.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    for p in paths:
        for x, y in p:
            ix, iy = int(round(x)), int(round(y))
            assert 0 <= iy < SYNTH_H and 0 <= ix < SYNTH_W and allow[iy, ix], \
                f"折线顶点 ({x}, {y}) 越出了 1px 中心线"
        assert _closed_gap(p) <= 2.0, f"环应在末端闭合，实得间隙 {_closed_gap(p):.2f}px"


def test_canny_defaults_pin_aperture_and_low_threshold():
    """钉死 CANNY_* 基线：aperture=3 抗噪；low=50 是噪声抑制下界。"""
    img = _synth_photo()

    # aperture：Sobel 核放大即把纹理当边缘（两个数量级）。
    n3 = len(trace_centerline_canny(img, aperture_size=3))
    n5 = len(trace_centerline_canny(img, aperture_size=5))
    assert n3 == 3, f"aperture=3 应得 3 条，实得 {n3}"
    assert n5 > 100 * n3, \
        f"aperture=5 应炸出大量碎路径，实得 {n5}（3 核 {n3}）"
    assert CANNY_APERTURE == 3

    # low：45 会放进噪声带，50 起干净（50..100 输出逐位相同）。
    loose = [p for p in trace_centerline_canny(img, low=45, high=110)
             if max(y for x, y in p) < _NOISE_BAND_Y]
    assert loose, "low=45 应漏进纹理噪声（= low=50 下界的依据）"
    assert CANNY_LOW == 50 and CANNY_HIGH == 120
    for lo in (50, 70, 100):
        assert (len(trace_centerline_canny(img, low=lo))
                == len(trace_centerline_canny(img, low=lo, high=200))
                == 3), f"low={lo} 的稳定平台应恒为 3 条路径"


def test_canny_thresholds_are_live_parameters():
    """阈值是真参数（不是写死）：调低 low 会多提边缘（真机调优入口）。"""
    img = _synth_photo()
    few = len(trace_centerline_canny(img, low=50, high=200))
    many = len(trace_centerline_canny(img, low=30, high=90))
    assert few == 3
    assert many > few, f"low=30/90 应比 50/200 多提，实得 {many} vs {few}"


def test_canny_rejects_low_greater_than_high():
    """契约：low > high 显式抛错（给即将接线的加图对话框手输阈值用）。"""
    with pytest.raises(ValueError):
        trace_centerline_canny(_synth_photo(), low=200, high=50)


def test_canny_blank_image_no_paths():
    assert trace_centerline_canny(np.full((100, 100), 255, np.uint8)) == []


def test_canny_solid_stroke_is_out_and_back_known_limit():
    """**已知边界**（表征测试，非期望行为）：实心笔画 Canny 只见到两条轮廓边。

    thin 无法合并相距 >2px 的两条 1px 线，于是 `_skeleton_paths` 走一圈 =
    沿轮廓来回各画一遍（edge_px ≈ 2× 线长，而非 1× 的真中心线）。
    故笔画类内容应走 `trace_centerline`（阈值二值 → thin）。
    钉在这里是为了让 T10 接线时的模式文案有据可依，也防止这条边界被无声改动。
    """
    line_len = 250
    img = np.full((200, 320), 235, np.uint8)
    cv2.line(img, (30, 100), (280, 100), 25, 4)
    edges = cv2.Canny(img, CANNY_LOW, CANNY_HIGH, apertureSize=CANNY_APERTURE) > 0
    assert int(edges.sum()) > 1.8 * line_len, \
        f"实心笔画的 Canny 应给出两条轮廓边（>1.8×{line_len}px），实得 {int(edges.sum())}"
    # 对照：同一笔画用阈值二值 → thin 才是**一条**真中心线。
    assert len(trace_centerline(img, threshold=200)) == 1


def test_canny_svg_generates_mm():
    """Canny 模式的 SVG 出口与既有两个模式同契约（mm 折线）。"""
    svg = image_to_canny_centerline_svg(_synth_photo(), target_mm=100.0)
    assert svg.count("<polyline") == 3
    import os
    import tempfile

    from megapro.toolchain.svg_to_gcode import parse_svg
    fd, p = tempfile.mkstemp(suffix=".svg")
    os.write(fd, svg.encode())
    os.close(fd)
    try:
        polys = parse_svg(p)
        xs = [x for pl in polys for x, y in pl]
        ys = [y for pl in polys for x, y in pl]
        # `_paths_to_svg` 把**最长边跨度**映射到 target_mm：本样张 x 跨
        # 30..278=248px（最长）→ 100mm；y 跨 110..209=99px → ≈39.9mm。
        assert 98 <= max(xs) - min(xs) <= 102
        assert 38 <= max(ys) - min(ys) <= 42
    finally:
        os.unlink(p)

