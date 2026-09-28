"""B1 回归：**重追的几何坐标系必须与导入链一致**（FR-10 就地重追的隐藏前提）。

缺陷（f2225ec 实测复现）：:meth:`LayoutPage.retrace_image_item` 只把
``_svg_to_paths(svg)`` 的 **SVG y-down 原坐标**直接塞进
``RetraceImageCommand``，没走组导入契约的 ①②③（``paper_from_svg_ydown``
y 翻转 + ``place_at_anchor`` 锚点归位 + ``normalize_local`` 归一）。
后果：同参数重追后页面几何**整体上下翻转 + 平移**（导出的 SVG 亦上下颠倒）
—— 会切错位置。

**为什么原先 326 个测试全绿**：``test_gui_layout_retrace.py`` 的重追用例只
断言 (a) ``paths`` 变了 (b) pos/scale/angle 没变 (c) 画布尺寸变了，**没有
一条断言重追前后页面几何恒等**。所以本文件全部用例只盯一件事：**页面坐标系
下逐点恒等**（不是条数、不是 bbox、不是"看起来差不多"）。

同时钉住「三链一致」：画布 PathItem、导出 SVG、送作业 G-code 读的都是同一份
模型几何，故页面恒等 ⇒ 三者都恒等；这里各取一次快照做交叉印证。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 小工具 -----------------------------------------------------------------

def _synthetic_png(tmp_path, *, circles=((80, 150, 30), (170, 120, 34)),
                   size=(240, 288)):
    """合成样张：白底 + 若干深色实心圆（高对比边缘，Canny 必出多条骨架）。"""
    import numpy as np
    from PIL import Image

    h, w = size
    arr = np.full((h, w), 255, np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    for cx, cy, r in circles:
        arr[((yy - cy) ** 2 + (xx - cx) ** 2) < r * r] = 40
    p = tmp_path / "synthetic.png"
    Image.fromarray(arr).save(str(p))
    return str(p)


#: 重追时「点确定」返回的参数（= 入库时用的同一组 ⇒ 几何应逐点不变）
_SAME_PARAMS = ("canny", 160, False, 100.0, 50, 120)


def _accept_params(monkeypatch, params=_SAME_PARAMS):
    """把参数对话框换成「按给定值直接接受」（弹窗会挂起 offscreen）。"""
    import megapro.gui.layout.layout_page as L

    monkeypatch.setattr(L.LayoutPage, "_ask_image_params",
                        lambda self, **kw: params)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning",
                        staticmethod(lambda *a, **k: None))


def _make_image_item(page, src, *, params=_SAME_PARAMS, transform=None):
    """按「加图」链建一个图片图元（含 image_spec），可选带变换。"""
    import megapro.gui.layout.layout_page as L

    mode, threshold, use_multi, target_mm, low, high = params
    raw = L._svg_to_paths(page._trace_image(
        src, mode=mode, threshold=threshold, use_multi=use_multi,
        target_mm=target_mm, low=low, high=high))
    it = L._import_group([raw], name="图")[0]
    it.image_spec = page._image_spec(
        src, mode=mode, threshold=threshold, use_multi=use_multi,
        target_mm=target_mm, low=low, high=high)
    if transform:
        it.pos, it.scale, it.angle_deg = transform
    page._add_items([it])
    return it


def _page_paths(item) -> list:
    """页面坐标系折线快照（读时施加镜像/缩放/旋转/平移，祖先链已合成）。"""
    return [[(float(x), float(y)) for x, y in p]
            for p in item.transformed_paths()]


def _assert_identical(got, want, what):
    """逐点恒等断言（不用 approx：同一函数链重算应**逐位**相同）。"""
    assert len(got) == len(want), f"{what}: 折线条数 {len(got)} != {len(want)}"
    for i, (pa, pb) in enumerate(zip(got, want)):
        assert len(pa) == len(pb), f"{what}: 第 {i} 条点数 {len(pa)} != {len(pb)}"
        for j, (qa, qb) in enumerate(zip(pa, pb)):
            assert qa == qb, f"{what}: 第 {i} 条第 {j} 点 {qa} != {qb}"


def _canvas_paths(gi) -> list:
    """画布 QGraphicsPath 的折线（本地系；镜像已烘进笔迹）。"""
    path = gi.path()
    out, cur = [], []
    for i in range(path.elementCount()):
        el = path.elementAt(i)
        if el.isMoveTo():
            if cur:
                out.append(cur)
            cur = [(float(el.x), float(el.y))]
        elif el.isLineTo():
            cur.append((float(el.x), float(el.y)))
    if cur:
        out.append(cur)
    return out


# --- 核心回归 ---------------------------------------------------------------

def test_retrace_same_params_keeps_page_geometry_pointwise(tmp_path,
                                                           monkeypatch):
    """**本批核心**：同图同参数重追 ⇒ 页面坐标下折线集合**逐点恒等**。

    旧行为（缺 ①②③）下同参数重追会把两块几何的 y 质心 19.79/41.70 换成
    97.20/75.29 并整体平移 (+32.68,+56.21)mm —— 本用例必红。
    """
    import megapro.gui.layout.layout_page as L

    _accept_params(monkeypatch)
    lp = L.LayoutPage()
    it = _make_image_item(lp, _synthetic_png(tmp_path))
    before = _page_paths(it)
    assert len(before) >= 2, "样张应至少产出两条骨架，用例才有判别力"

    lp.retrace_image_item(it)

    _assert_identical(_page_paths(it), before, "同参数重追后页面几何")
    lp.deleteLater()


def test_retrace_with_transform_keeps_page_geometry_and_transform(
        tmp_path, monkeypatch):
    """pos/scale/angle ≠ 0 时同样逐点恒等，且三个变换量**一个字段都不动**。

    保住 FR-10 验收③（重追保持 pos/scale/angle）的同时钉住坐标系：变换量不变
    是必要条件，逐点恒等才是充分条件 —— 只断前者的话，把 ``paths`` 停在
    y-down 原坐标系照样全绿（旧行为正是这样溜过去的）。
    """
    import megapro.gui.layout.layout_page as L

    _accept_params(monkeypatch)
    lp = L.LayoutPage()
    it = _make_image_item(lp, _synthetic_png(tmp_path),
                          transform=((30.0, 40.0), 1.5, 10.0))
    before = _page_paths(it)
    keep = (it.pos, it.scale, it.angle_deg)

    lp.retrace_image_item(it)

    _assert_identical(_page_paths(it), before, "带变换重追后页面几何")
    assert (it.pos, it.scale, it.angle_deg) == keep
    lp.deleteLater()


def test_retrace_undo_restores_page_geometry_pointwise(tmp_path, monkeypatch):
    """重追可撤销：undo 后**页面几何**逐点回到旧折线（不只 local paths）。"""
    import megapro.gui.layout.layout_page as L

    _accept_params(monkeypatch)
    lp = L.LayoutPage()
    it = _make_image_item(lp, _synthetic_png(tmp_path),
                          transform=((12.5, 33.5), 2.0, 42.0))
    before = _page_paths(it)
    keep = (it.pos, it.scale, it.angle_deg)

    lp.retrace_image_item(it)
    assert lp._undo.count() > 0
    lp._undo.undo()

    _assert_identical(_page_paths(it), before, "undo 后页面几何")
    assert (it.pos, it.scale, it.angle_deg) == keep
    # redo 再来一遍仍恒等：命令里存的 new_paths 本身就得是归一后的坐标系
    # （旧行为下 redo 会把 y-down 原坐标再灌回去 —— 本断言即其红点）。
    lp._undo.redo()
    _assert_identical(_page_paths(it), before, "redo 后页面几何")
    lp.deleteLater()


def test_retrace_keeps_canvas_export_and_job_identical(tmp_path, monkeypatch):
    """画布笔迹 / 导出 SVG / 送作业 paths 三条链与重追前**逐点一致**。

    三者都读同一份模型几何，页面恒等必然传导；这里各取快照交叉印证，防止将来
    某一链自己偷偷再翻一次 y。
    """
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.export_svg import document_to_svg

    _accept_params(monkeypatch)
    lp = L.LayoutPage()
    it = _make_image_item(lp, _synthetic_png(tmp_path),
                          transform=((20.0, 25.0), 1.25, -7.5))
    gi = lp._gi_for(it)

    canvas_before = _canvas_paths(gi)
    svg_before = document_to_svg(lp.doc)
    job_before = [list(p) for p in lp.to_job_spec().paths_paper]

    lp.retrace_image_item(it)

    _assert_identical(_canvas_paths(gi), canvas_before, "画布笔迹")
    assert document_to_svg(lp.doc) == svg_before, "导出 SVG 内容变了"
    _assert_identical([list(p) for p in lp.to_job_spec().paths_paper],
                      job_before, "送作业 paths")
    lp.deleteLater()


def test_retrace_inside_group_keeps_page_geometry(tmp_path, monkeypatch):
    """图元编进组后重追：页面几何仍逐点恒等（组框 overlay / 祖先变换不受扰）。"""
    import megapro.gui.layout.layout_page as L
    from megapro.gui.layout.model import Item

    _accept_params(monkeypatch)
    lp = L.LayoutPage()
    it = _make_image_item(lp, _synthetic_png(tmp_path))
    other = Item(paths=[[(150.0, 150.0), (180.0, 150.0)]], name="伴生")
    lp._add_items([other])
    assert lp.doc.group_items([it, other]) is not None
    assert lp.doc.owner_of(it) is not None, "应已挂在容器下（祖先变换链存在）"
    before = _page_paths(it)

    lp.retrace_image_item(it)

    _assert_identical(_page_paths(it), before, "组内重追后页面几何")
    lp.deleteLater()


# --- 链条本身：重追 = 加图（不重写一遍几何） -------------------------------

def test_image_paths_to_paper_equals_import_chain():
    """``_image_paths_to_paper`` **就是**组导入契约①②③（单组）。

    钉死「两条链共用一份几何」：改动任一条的归一语义，另一条同步跟随；
    若有人把重追改回 ``_svg_to_paths`` 直塞（回到 y-down 原坐标），本用例与
    上面的页面恒等用例一起红。
    """
    import megapro.gui.layout.layout_page as L
    from megapro.gui.canvas.coords import paper_from_svg_ydown

    # y-down 原坐标的假折线（贴 SVG 下半区，能同时暴露漏翻转与漏归位）
    raw = [[(10.0, 30.0), (40.0, 30.0)], [(10.0, 60.0), (25.0, 62.0)]]

    got = L._image_paths_to_paper(raw)
    assert got == L._import_group([raw], name="图")[0].paths
    # 逐点等于「翻转 + bl 归位」：y 取 210−y（BED_H）后**整组**左下移到 (0,0)
    flipped = paper_from_svg_ydown(raw)
    y0 = min(y for p in flipped for _x, y in p)
    x0 = min(x for p in flipped for x, _y in p)
    for pg_new, pg_ref in zip(got, flipped):
        assert pg_new == [(x - x0, y - y0) for x, y in pg_ref]
    # 整组 bbox 左下确实落在 (0,0)，且与 y-down 原坐标**逐点不同** ——
    # 直接透传 raw（旧行为）会让 min=(10.0, 30.0) 而不是 (0.0, 0.0)。
    got_xs = [x for p in got for x, _y in p]
    got_ys = [y for p in got for _x, y in p]
    assert min(got_xs) == 0.0 and min(got_ys) == 0.0
    assert min(x for p in raw for x, _y in p) == 10.0
    assert min(y for p in raw for _x, y in p) == 30.0


def test_retrace_rejected_when_chain_yields_no_paths(tmp_path, monkeypatch):
    """归一链产出空 ⇒ 仍走「无线条」拒绝分支，**不 push** 空几何命令。

    归一化现在发生在入库链里，判空必须仍在同一处、且在 push 之前 —— 否则会
    往模型里塞一个 paths=[] 的空图元（画布上表现为图片凭空消失）。
    """
    import megapro.gui.layout.layout_page as L
    from PySide6 import QtWidgets

    monkeypatch.setattr(L.LayoutPage, "_ask_image_params",
                        lambda self, **kw: _SAME_PARAMS)
    monkeypatch.setattr(L.LayoutPage, "_trace_image",
                        lambda self, *a, **k: "<svg></svg>")
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    lp = L.LayoutPage()
    it = _make_image_item(lp, _synthetic_png(tmp_path))
    before = _page_paths(it)
    n0 = lp._undo.count()

    lp.retrace_image_item(it)

    assert lp._undo.count() == n0, "空结果不该进撤销栈"
    _assert_identical(_page_paths(it), before, "被拒后几何不变")
    lp.deleteLater()
