"""D1 回归：**画布所见 ≡ 机器所切**（祖先变换下逐位一致）。

缺陷（终审冷读实测，offscreen 真窗口）：渲染通路只施加叶子**自身**变换
（``canvas/items.py:94`` 只 ``setPos(item.pos)``），**从不施加祖先链**；而
导出/切割走 ``flatten_visible``（祖先链由 ``_apply_transform`` 逐层合成）。
两条路一旦分叉，就是「用户看到的」与「机器切的」不是同一个位置：

1. 编组后把**组容器**挪到 (60,20) ⇒ CUT x[70,140] y[30,70]，画布 M/N 纹丝不动；
2. 真 ``_paste`` 造两层容器（OUTER pos=(100,0)）⇒ CUT 与画布差正好一个祖先 pos；
3. 纯 UI 编组→Ctrl+C→Ctrl+V ⇒ 两个副本在画布上**完全重合**，机器却切两份分开的。

**为什么拿 ``unit_paths`` 当真值、而不是再调一次画布函数**：``unit_paths``
（``model.py``）是祖先链合成的**另一条代码路径** —— 画布侧把祖先链压成一个
3 点探针仿射、交给 Qt 复合，模型侧逐层调 ``_apply_transform``。两者独立，
比对才有意义；拿 ``gi.mapToParent()`` 或 ``_page_box`` 当真值是**自指**，
结构上不可能失败（本仓已因此栽过：``test_gui_layout_ancestor_chain_box.py``
的 docstring 记录了「同一支函数自指」的教训）。

**为什么按「矩形」图元比对**：``PathItem.boundingRect`` 返回
``model_item.bbox()``（本地 bbox 矩形，见 ``items.py:51``），故
``sceneBoundingRect`` 是**变换后的矩形**的 AABB。对角线这类图形它天然大于
折线本身的 AABB（那是 Qt 的既有语义，与本缺陷无关）。故：

- 主断言用**闭合矩形**图元（本地 bbox 紧致）⇒ 两者应**逐位相等**；
- 另有一条**独立**用例用旋转件断言包含关系 + 矩形 AABB 的精确值，
  把上面那条语义钉住，免得后人以为「旋转件也该逐位相等」而去改
  ``boundingRect``。
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets                        # noqa: E402
from PySide6.QtCore import QPointF, Qt                      # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.canvas.items import PathItem                # noqa: E402
from megapro.gui.layout.layout_page import LayoutPage, _item_to_json  # noqa: E402
from megapro.gui.layout.model import (                        # noqa: E402
    Item, Document, flatten_visible, iter_ancestors, iter_leaves,
    unit_paths,
)

#: 判定容差。Qt 的 QTransform 复合与 Python 浮点链在**同一 IEEE-754 双精度**
#: 上运算，但**运算次序**不同 ⇒ 末位 ulp 必然有别。1e-9 mm 比机器精度
#: （0.01mm 步距）小 7 个数量级，远严于任何物理误差，同时容纳 ulp 抖动。
TOL = 1e-9


# -- 真值侧（模型） ---------------------------------------------------------

def _leaf_page_bbox(doc):
    """每个叶子的**页面系** bbox —— 走 ``unit_paths``（祖先链合成的模型路径）。

    返回 ``[(key, (x0, y0, x1, y1)), ...]``，按**文档 DFS 前序**（稳定）。
    ``key`` = ``(name, 该名出现序号)``：粘贴副本与原件**同名**，故必须带序号
    才唯一 —— 否则「两份同名图元画布重合」这个反例会被配对逻辑提前吃掉，
    变成一条钉不住任何东西的用例。
    """
    out = []
    seen: dict[str, int] = {}
    for leaf, chain in iter_ancestors(doc.items):
        if leaf.children:
            continue
        paths = [p for p in unit_paths(leaf, chain) if len(p) >= 2]
        if not paths:
            continue
        n = seen.get(leaf.name, 0)
        seen[leaf.name] = n + 1
        xs = [x for p in paths for x, _ in p]
        ys = [y for p in paths for _, y in p]
        out.append(((leaf.name, n), (min(xs), min(ys), max(xs), max(ys))))
    return out


def _visible_pathitems(page):
    """场景里**可见**的 PathItem（与 ``make_gi`` 的可见性口径一致）。

    顺序 = ``page._scene_items``，它由 ``_rebuild_scene`` 按 ``iter_leaves``
    的 DFS 前序建立 ⇒ 与 :func:`_leaf_page_bbox` 的遍历序逐项对应。
    """
    from megapro.gui.layout.model import effectively_visible

    return [gi for gi in page._scene_items
            if effectively_visible(page.doc, gi.model_item)]


def _scene_bboxes(page):
    """画布**真正画出来**的包围盒（用户看到的）→ 与模型侧同键对齐。

    键的构造与 :func:`_leaf_page_bbox` 完全同款（同名带序号），两侧靠
    **同一个 DFS 前序**配对，不是靠「碰巧顺序一样」。
    """
    out = {}
    seen: dict[str, int] = {}
    for gi in _visible_pathitems(page):
        r = gi.sceneBoundingRect()
        name = gi.model_item.name
        n = seen.get(name, 0)
        seen[name] = n + 1
        out[(name, n)] = (r.left(), r.top(), r.right(), r.bottom())
    return out


def _cut_aabb(doc):
    """实际发往机器的切割范围（独立真值：``flatten_visible``）。"""
    xs = [x for p in flatten_visible(doc) for x, _ in p]
    ys = [y for p in flatten_visible(doc) for _, y in p]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _assert_close(got, want, what):
    for i, axis in enumerate("x0 y0 x1 y1".split()):
        assert abs(got[i] - want[i]) <= TOL, (
            f"{what}: {axis} 画布 {got[i]!r} vs 机器 {want[i]!r} "
            f"（差 {got[i] - want[i]:+.6g} mm）—— 画布画的和机器切的不是同一处")


def assert_canvas_matches_cut(page, why):
    """**核心判据**：每个可见 PathItem 的 sceneBoundingRect ≡ 它的页面几何。"""
    model = dict(_leaf_page_bbox(page.doc))
    scene = _scene_bboxes(page)
    assert set(scene) == set(model), (
        f"{why}: 可见 PathItem 集合与模型叶子集合不符 "
        f"（画布 {sorted(scene)} vs 模型 {sorted(model)}）")
    for key, box in sorted(scene.items()):
        _assert_close(box, model[key], f"{why} / 图元 {key[0]!r}#{key[1]}")


def assert_union_matches_cut(page, why):
    """整体判据：画布并集 ≡ ``flatten_visible`` 的切割 AABB（机器真正收到的）。"""
    scene = _scene_bboxes(page)
    assert scene, f"{why}: 画布上没有任何可见 PathItem"
    u = (min(b[0] for b in scene.values()), min(b[1] for b in scene.values()),
         max(b[2] for b in scene.values()), max(b[3] for b in scene.values()))
    _assert_close(u, _cut_aabb(page.doc), f"{why} / 整体")


# -- 造场景的工具 -----------------------------------------------------------

def _rect(name, x, y, w, h, z=0.0):
    """闭合矩形图元（本地 bbox 紧致 ⇒ sceneBoundingRect 与折线 AABB 逐位相等）。"""
    return Item(paths=[[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]],
                pos=(0.0, 0.0), name=name, z=z)


def _page_with(items):
    doc = Document()
    for it in items:
        doc.add(it)
    return doc


def _rebuild(page):
    page._rebuild_scene()
    page._after_change()


def _drag_via_mouse(view, p0_mm, p1_mm):
    """QTest 真事件拖拽（与 ``test_gui_layout_group.py`` 同款：mm → 视口 px）。"""
    from PySide6.QtTest import QTest

    def vp(x, y):
        return view.viewportTransform().map(QPointF(float(x), float(y))).toPoint()

    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=vp(*p0_mm))
    QTest.mouseMove(view.viewport(), vp(*p1_mm))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=vp(*p1_mm))


# -- ① 反例本身：组容器带非恒等变换时，画布必须跟着走 ----------------------

def test_group_container_offset_moves_canvas_to_where_it_is_cut():
    """**缺陷 1**：编组后把组容器挪到 (60,20) ⇒ 画布必须跟着挪。

    修复前实测：容器 pos=(60,20) 时 CUT x[70,140] y[30,70]，而画布上
    M 仍在 x[10,40] y[10,30]、N 仍在 x[50,80] y[50,50] —— **画布一动不动**。
    """
    page = LayoutPage()
    a = _rect("M", 10.0, 10.0, 30.0, 20.0, z=1.0)
    b = _rect("N", 50.0, 50.0, 30.0, 0.5, z=2.0)
    for it in (a, b):
        page.doc.add(it)
    g = page.doc.group_items([a, b])
    assert g is not None, "前置：编组应成功"
    _rebuild(page)

    g.pos = (60.0, 20.0)
    _rebuild(page)

    assert g.pos == (60.0, 20.0), "前置：组容器应带非零 pos（否则反例不成立）"
    assert_canvas_matches_cut(page, "组容器 pos=(60,20)")
    assert_union_matches_cut(page, "组容器 pos=(60,20)")

    cut = _cut_aabb(page.doc)
    assert cut[0] == pytest.approx(70.0) and cut[2] == pytest.approx(140.0), \
        f"前置：切割 AABB 应含祖先偏移，实得 {cut}"
    # 画布必须**真的**在切割位置上，而不是碰巧两组都在原点
    scene = _scene_bboxes(page)
    m0 = scene[("M", 0)]
    assert m0[0] == pytest.approx(70.0), \
        f"画布上的 M 应落在 x=70（=10+60），实得 {m0[0]} —— 画布没跟祖先走"


def test_three_level_nested_containers_from_real_paste():
    """**缺陷 2**：真 ``_paste`` 造三层容器，逐层偏移都要进画布。

    走真实 Ctrl+V 入口（``_paste`` 对剪贴板只 ``json.loads``、零校验），
    偏移只加在每层容器上 —— 画布与机器的差应当**逐层累加**。
    """
    page = LayoutPage()
    payload = [{
        "name": "L1", "pos": [30.0, 0.0], "z": 0.0,
        "children": [{
            "name": "L2", "pos": [20.0, 0.0], "z": 0.0,
            "children": [{
                "name": "L3", "pos": [10.0, 0.0], "z": 0.0,
                "children": [{
                    "name": "leaf", "pos": [0.0, 0.0], "z": 0.0,
                    "paths": [[(5.0, 5.0), (15.0, 5.0), (15.0, 15.0),
                               (5.0, 15.0), (5.0, 5.0)]],
                }],
            }],
        }],
    }]
    QtWidgets.QApplication.clipboard().setText(json.dumps(payload))
    page._paste()
    _rebuild(page)

    outer = page.doc.items[0]
    assert outer.name == "L1"
    # 粘贴的 dz=5 只加在根 ⇒ 根 pos=(35,5)，内两层原样
    assert outer.pos == (35.0, 5.0), f"前置：根容器 pos 应为 (35,5)，实得 {outer.pos}"

    assert_canvas_matches_cut(page, "三层嵌套（真 _paste）")
    assert_union_matches_cut(page, "三层嵌套（真 _paste）")

    cut = _cut_aabb(page.doc)
    # 5 + 10 + 20 + 30 = 65；y: 5 + 5 + 0 + 0 = 10（叶子自身起点 (5,5)）
    assert cut[0] == pytest.approx(70.0), f"切割 x0 应为 70，实得 {cut}"
    assert cut[1] == pytest.approx(10.0), f"切割 y0 应为 10，实得 {cut}"


def test_rotated_and_scaled_ancestor_chain_reaches_canvas():
    """祖先带**旋转 + 缩放**时画布也要跟上（不只是平移）。"""
    page = LayoutPage()
    leaf = _rect("leaf", 0.0, 0.0, 10.0, 5.0, z=0.0)
    page.doc.add(leaf)
    cont = Item(paths=[], pos=(40.0, 10.0), scale=1.5, angle_deg=30.0,
                name="C", z=0.0, children=[leaf])
    page.doc.remove(leaf)
    page.doc.add(cont)
    _rebuild(page)

    assert_canvas_matches_cut(page, "祖先 rot30 scale1.5")
    assert_union_matches_cut(page, "祖先 rot30 scale1.5")

    # 非平移 ⇒ 祖先 bbox 不等于原点平移，确保这条不是被恒等祖先误过的
    model = dict(_leaf_page_bbox(page.doc))[("leaf", 0)]
    assert model[0] != pytest.approx(leaf.bbox()[0]), "前置：本例不应退化成平移"


def test_mirrored_ancestor_reaches_canvas():
    """祖先带**镜像**时画布也要跟上（镜像数学只在 ``coords.mirror_scalar`` 一处）。"""
    page = LayoutPage()
    leaf = _rect("leaf", 0.0, 0.0, 20.0, 10.0, z=0.0)
    page.doc.add(leaf)
    cont = Item(paths=[], pos=(60.0, 30.0), name="C", z=0.0,
                children=[leaf], mirror_x=True)
    page.doc.remove(leaf)
    page.doc.add(cont)
    _rebuild(page)

    assert_canvas_matches_cut(page, "祖先 mirror_x")
    assert_union_matches_cut(page, "祖先 mirror_x")


# -- ② 纯 UI 路径：编组 → Ctrl+C → Ctrl+V ---------------------------------

def test_copy_pasted_group_copies_do_not_overlap_on_canvas():
    """**缺陷 3**：编组→Ctrl+C→Ctrl+V 后两个副本在画布上**不重合**。

    修复前实测：模型顶层面是 ``('组',(0,0))`` 与 ``('组',(5,5))``，而画布上
    两个副本的 sceneBoundingRect **逐位相同** —— 用户看到一份，机器切两份。
    """
    page = LayoutPage()
    a = _rect("M", 10.0, 10.0, 30.0, 20.0, z=1.0)
    b = _rect("N", 10.0, 40.0, 70.0, 10.0, z=2.0)
    for it in (a, b):
        page.doc.add(it)
    g = page.doc.group_items([a, b])
    assert g is not None
    _rebuild(page)

    for gi in _scene_bboxes(page).values() and _visible_pathitems(page):
        gi.setSelected(True)
    page._copy_selected()
    page._paste()
    _rebuild(page)

    assert len(page.doc.items) == 2, f"前置：应有原件 + 副本，实得 {len(page.doc.items)}"
    assert page.doc.items[0].pos != page.doc.items[1].pos, \
        "前置：两个组容器的 pos 应不同（副本有 dz 偏移），否则反例不成立"

    assert_canvas_matches_cut(page, "编组→复制→粘贴")
    assert_union_matches_cut(page, "编组→复制→粘贴")

    # 逐个副本比：同名图元在两份里画布位置必须不同
    scene = _scene_bboxes(page)
    m_boxes = sorted(b for k, b in scene.items() if k[0] == "M")
    assert len(m_boxes) == 2, f"前置：应有两个 M，实得 {m_boxes}"
    assert abs(m_boxes[0][0] - m_boxes[1][0]) > 1.0, \
        f"两个 M 副本在画布上重合于 x={m_boxes[0][0]} —— 用户只看到一份，机器切两份"


# -- ③ 拖动：回写后画布仍 ≡ 机器所切 --------------------------------------

def test_drag_inside_offset_group_keeps_canvas_equal_to_cut():
    """组内叶子拖动后，画布与机器仍逐位一致（回写没把坐标系搞混）。"""
    page = LayoutPage()
    page.snap_enabled = False          # 隔离吸附，测的是拖动回写本身
    leaf = _rect("leaf", 0.0, 0.0, 20.0, 10.0, z=0.0)
    page.doc.add(leaf)
    cont = Item(paths=[], pos=(60.0, 30.0), name="C", z=0.0, children=[leaf])
    page.doc.remove(leaf)
    page.doc.add(cont)
    _rebuild(page)

    before = dict(_leaf_page_bbox(page.doc))[("leaf", 0)]
    gi = _gi_of(page, leaf)
    assert gi is not None
    gi.setSelected(True)
    page._on_selection_changed()

    c = gi.sceneBoundingRect().center()
    _drag_via_mouse(page.view, (c.x(), c.y()), (c.x() + 12.0, c.y() + 7.0))

    after = dict(_leaf_page_bbox(page.doc))[("leaf", 0)]
    assert after != before, "前置：拖动应真的改了模型 pos（否则本用例空跑）"
    assert after[0] > before[0], f"前置：应向 +x 拖动，{before} -> {after}"

    assert_canvas_matches_cut(page, "组内拖动后")
    assert_union_matches_cut(page, "组内拖动后")


def test_gi_pos_stays_in_parent_frame_after_drag_in_offset_group():
    """``gi.pos()`` 必须仍是**模型 pos（父坐标系）**，不是页面坐标。

    这是回写的地基：``commit_move`` 拿 ``gi.pos()`` 写回 ``it.pos``
    （``items.py:143``）。若画布把祖先变换折进 ``pos()``，回写就会把
    「页面坐标」当成「父系坐标」写进模型 ⇒ 一次拖动就把图元甩到床外。
    """
    page = LayoutPage()
    page.snap_enabled = False
    leaf = _rect("leaf", 0.0, 0.0, 20.0, 10.0, z=0.0)
    page.doc.add(leaf)
    cont = Item(paths=[], pos=(60.0, 30.0), name="C", z=0.0, children=[leaf])
    page.doc.remove(leaf)
    page.doc.add(cont)
    _rebuild(page)

    gi = _gi_of(page, leaf)
    gi.setSelected(True)
    page._on_selection_changed()
    c = gi.sceneBoundingRect().center()
    _drag_via_mouse(page.view, (c.x(), c.y()), (c.x() + 10.0, c.y() + 6.0))

    model_pos = leaf.pos
    gi_pos = (gi.pos().x(), gi.pos().y())
    assert abs(gi_pos[0] - model_pos[0]) <= TOL and abs(gi_pos[1] - model_pos[1]) <= TOL, \
        (f"gi.pos()={gi_pos} 与模型 pos={model_pos} 不一致 —— "
         f"祖先变换被折进了 pos()，commit_move 回写会写错坐标系")
    # 页面坐标 ≠ 父系坐标（否则上面那条断言恒真，钉不住任何东西）
    page_pos = dict(_leaf_page_bbox(page.doc))[("leaf", 0)]
    assert abs(page_pos[0] - model_pos[0]) > 1.0, \
        "前置：本例的页面坐标应与父系坐标明显不同（祖先有 60mm 偏移）"

    # **同一个文档还要画对**：把「pos 保持父系语义」与「画布 ≡ 机器」绑在
    # 一条里 —— 单独断言前者的话，修复前的画布（完全不带祖先变换）也能过，
    # 那这条就钉不住任何东西（``gi.pos()`` 本来就等于模型 pos）。
    assert_canvas_matches_cut(page, "拖动后：pos 父系语义 + 画布跟祖先")
    assert_union_matches_cut(page, "拖动后：pos 父系语义 + 画布跟祖先")
    # 祖先偏移确实在画布上（不是碰巧两边都没偏移）
    r = gi.sceneBoundingRect()
    assert abs(r.left() - page_pos[0]) <= TOL, \
        (f"画布 x0={r.left()} 应等于页面 x0={page_pos[0]} —— "
         f"祖先的 60mm 偏移没进画布")


def _gi_of(page, item):
    return page._gi_for(item)


# -- ④ 不回归：无祖先 / 恒等祖先（产品主路径）逐位不变 -----------------------

def test_flat_document_without_ancestors_is_unchanged():
    """**不回归**：平面文档（产品主路径）画布仍 ≡ 机器。"""
    page = LayoutPage()
    for i, (x, y) in enumerate([(10.0, 10.0), (60.0, 20.0), (30.0, 80.0)]):
        page.doc.add(_rect(f"r{i}", x, y, 25.0, 15.0, z=float(i)))
    _rebuild(page)
    assert_canvas_matches_cut(page, "平面文档")
    assert_union_matches_cut(page, "平面文档")


def test_identity_ancestor_group_still_matches_cut():
    """**不回归**：``group_items`` 造的恒等容器（pos=(0,0)）下逐位一致。"""
    page = LayoutPage()
    a = _rect("a", 10.0, 10.0, 20.0, 20.0, z=1.0)
    b = _rect("b", 50.0, 50.0, 20.0, 20.0, z=2.0)
    for it in (a, b):
        page.doc.add(it)
    g = page.doc.group_items([a, b])
    assert g.pos == (0.0, 0.0), "前置：group_items 的容器应恒等"
    _rebuild(page)
    assert_canvas_matches_cut(page, "恒等祖先组")
    assert_union_matches_cut(page, "恒等祖先组")


def test_own_transform_still_lands_where_model_says():
    """**不回归**：叶子**自身**的 pos/scale/angle 仍逐位落到模型位置。"""
    page = LayoutPage()
    it = Item(paths=[[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0), (0.0, 0.0)]],
              pos=(37.0, 53.0), scale=1.7, angle_deg=25.0, name="own", z=0.0)
    page.doc.add(it)
    _rebuild(page)
    assert_canvas_matches_cut(page, "自身变换")
    assert_union_matches_cut(page, "自身变换")


# -- ⑤ 语义钉住：旋转件的 sceneBoundingRect 是「变换后矩形」的 AABB ---------

def test_rotated_item_bbox_is_transformed_rect_not_polyline_aabb():
    """把「旋转件下 ``sceneBoundingRect`` 语义」钉住，免得后人误改。

    ``PathItem.boundingRect`` 返回 ``model_item.bbox()``（**矩形**），故
    ``sceneBoundingRect`` = 变换后该矩形的 AABB。对角线图元它**必然大于**
    折线本身的 AABB —— 这是 Qt 侧既有语义，与 D1 无关，但必须显式钉住：
    否则会有人为了让「逐位一致」而去改 ``boundingRect``，连带改掉命中区域。
    """
    page = LayoutPage()
    diag = Item(paths=[[(0.0, 0.0), (20.0, 20.0)]], pos=(30.0, 30.0),
                angle_deg=45.0, name="diag", z=0.0)
    page.doc.add(diag)
    _rebuild(page)

    gi = _gi_of(page, diag)
    r = gi.sceneBoundingRect()
    model_paths = [p for p in unit_paths(diag, ()) if len(p) >= 2]
    xs = [x for p in model_paths for x, _ in p]
    ys = [y for p in model_paths for _, y in p]
    line_box = (min(xs), min(ys), max(xs), max(ys))

    assert r.width() > (line_box[2] - line_box[0]) + TOL, \
        ("旋转 45° 的对角线：变换后矩形 AABB 应严格大于折线 AABB —— "
         "若两者相等，说明 boundingRect 被改成了折线外接，命中区域语义已变")
    # 画布矩形仍须包含折线（不能小于机器要切的几何）
    assert r.left() <= line_box[0] + TOL and r.top() <= line_box[1] + TOL
    assert r.right() >= line_box[2] - TOL and r.bottom() >= line_box[3] - TOL
