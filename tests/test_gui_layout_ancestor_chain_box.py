"""R4 回归：组框矩形**必须真的带祖先变换**（旧实现宣称带、实际 chain 恒空）。

⚠ **本文件与既有 nested 回归的根本区别**：既有那两条
（``test_gui_canvas_group_nested`` / ``test_gui_canvas_group_overlay_nested``）
拿「画出来的框」去比 ``page._page_box(cont)`` —— **同一支函数自指**，结构上
不可能失败：`_page_box` 无论对错，画出来的框都会跟着对，16 条全绿而缺陷健在。
本文件一律拿**独立真值**比对：

- 几何真值 = :func:`flatten_visible`（拍平链，祖先变换由
  ``_apply_transform`` 逐层合成，与 ``_page_box`` 走的
  ``iter_ancestors`` + ``unit_page_bbox`` 是两条不同代码路径）；
- 断言的是**用户看到的框**（``overlay._frames`` 里 ``GroupFrameItem`` 的
  ``sceneBoundingRect``），不是再调一次 ``_page_box``。

**缺陷（R4 对 B2 声称的对抗性复核，复现于本仓库）**：``_page_box`` 原写成
``iter_units([item], visible_only=False)``，而该遍历从 ``[item]`` 起步、
**第一个 yield 就是 item 自身** ⇒ ``it is item`` 立刻命中、``chain`` 恒为
``()``。于是「组框矩形走 page_box（含祖先链）」这条**写进 docstring 与既有
回归注释**的保证从未成立。

走真实 UI 入口 ``_paste``（对剪贴板文本只 ``json.loads``、零校验，
``_item_from_json`` 对**容器**也还原 ``pos``）可经 UI 造出非恒等外层容器：
外层 pos=(100,0)（粘贴 dz=5 后 (105,5)）时，内层组框被画在 (0,0)，而
``flatten_visible`` 的实际切割 AABB 在 (105,5)…(115,45) —— **差 105mm**。

**为什么这不只是画框难看**：``_unit_bbox`` 是**对齐 / 分布 / 属性面板
X,Y / 等比缩放 k** 的唯一来源。bbox 错 ⇒ 用户「对齐」一个组时整组被平移到
错误位置 ⇒ **下一刀切错地方**。这是会动刀的那一类。
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets                           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.layout_page import LayoutPage    # noqa: E402
from megapro.gui.layout.model import flatten_visible     # noqa: E402


# -- 工具 ------------------------------------------------------------------

def _nested_json(outer_pos):
    """两层嵌套组 JSON：OUTER(容器) > INNER(容器) > a/b（叶子）。

    叶子各画一条水平线，跨度 10mm、间距 40mm ⇒ 组框 10×40，好区分。
    """
    return json.dumps([{
        "name": "OUTER", "pos": [outer_pos[0], outer_pos[1]], "z": 0.0,
        "children": [{
            "name": "INNER", "pos": [0.0, 0.0], "z": 0.0,
            "children": [
                {"name": "a", "z": 0.0,
                 "paths": [[(0.0, 0.0), (10.0, 0.0), (10.0, 0.0)]]},
                {"name": "b", "z": 1.0,
                 "paths": [[(0.0, 40.0), (10.0, 40.0), (10.0, 40.0)]]},
            ],
        }],
    }])


def _pasted(outer_pos):
    """走**真实** ``_paste`` 入口造出版面（Ctrl+V 那条路，零校验）。

    随后**勾选全部叶子**并让 Qt 真信号链刷新组框 —— 判据是「容器子树的叶子
    闭包 ⊆ 当前叶子选择集」，不选中就不会有任何组框（那会让本文件全部用例空跑）。
    """
    lp = LayoutPage()
    QtWidgets.QApplication.clipboard().setText(_nested_json(outer_pos))
    lp._paste()
    lp._sync_models()
    _select_all_leaves(lp)
    outer = lp.doc.items[0]
    return lp, outer, outer.children[0]


def _select_all_leaves(lp):
    """勾选全部叶子（判据要求叶子闭包 ⊆ 选择集，否则一个框都不画）。"""
    from megapro.gui.layout.model import iter_leaves

    for leaf in iter_leaves(lp.doc.items):
        gi = lp._gi_for(leaf)
        if gi is not None:
            gi.setSelected(True)


def _cut_aabb(doc):
    """实际会被切的几何范围（独立真值：拍平链，祖先变换逐层合成）。"""
    xs = [x for p in flatten_visible(doc) for x, _ in p]
    ys = [y for p in flatten_visible(doc) for _, y in p]
    return (round(min(xs), 6), round(min(ys), 6),
            round(max(xs), 6), round(max(ys), 6))


def _drawn_rects(lp):
    """overlay **真正画出来**的框（场景坐标）→ {容器名: (x0,y0,x1,y1)}。

    取 ``GroupFrameItem._rect``（画框时用的逻辑矩形）并映射到场景，**不是**
    ``sceneBoundingRect()``：后者含 :meth:`GroupFrameItem.boundingRect` 那圈
    ±2mm 画笔 halo（实测整体外扩 2mm，会把「框位对不对」变成「差 2mm」的
    噪音），也**不是**再调一次 ``_page_box`` —— 后者正是本文件要证伪的东西，
    拿它当真值就退化成自指。
    """
    out = {}
    for f in lp._group_overlay._frames:
        # mapToScene(QRectF) 返回 QPolygonF（轴对齐变换下等价，取其包围盒）
        r = f.mapToScene(f._rect).boundingRect()
        out[f.container.name] = (round(r.left(), 6), round(r.top(), 6),
                                 round(r.right(), 6), round(r.bottom(), 6))
    return out


def _refresh(lp):
    """走真选择信号链刷新组框（与用户点选同一条路）。"""
    lp._group_overlay.refresh(lp._page_box)


# -- ① 反例本身：内层框必须与被切位置重合 -----------------------------------

def test_nested_inner_frame_matches_where_it_will_actually_be_cut():
    """外层容器带 pos 时，**内层**组框必须与切割 AABB 重合。

    修复前实测：内层框 (0,0,10,40) vs 切割 (105,5,115,45)，差 105mm。
    """
    lp, outer, inner = _pasted((100.0, 0.0))
    _refresh(lp)

    assert outer.pos != (0.0, 0.0), "前置：外层容器应带非零 pos（否则反例不成立）"
    drawn = _drawn_rects(lp)
    assert "INNER" in drawn, f"内层组框没画出来：{drawn}"
    assert drawn["INNER"] == _cut_aabb(lp.doc), \
        (f"内层组框 {drawn['INNER']} 与实际被切位置 {_cut_aabb(lp.doc)} 不符"
         f" —— 祖先变换没带进框里")


def test_both_nested_frames_land_on_the_same_box():
    """两层框都应落在同一处（修复前它们分处两个坐标系）。

    这条比上一条更贴近用户观感：嵌套组画两个框，两框本该重合；旧实现里
    外层框跟着被切位置、内层框留在原点，肉眼可见「框和图对不上」。
    """
    lp, _outer, _inner = _pasted((100.0, 0.0))
    _refresh(lp)

    drawn = _drawn_rects(lp)
    assert len(drawn) == 2, f"应画两个组框，实得 {len(drawn)}：{drawn}"
    (o_name, o_box), (i_name, i_box) = sorted(drawn.items())
    assert o_box == i_box, \
        f"两层组框分处两地：{o_name}={o_box} vs {i_name}={i_box}"


def test_identical_outer_container_keeps_working():
    """**不回归**：外层恒等（pos=0）时两层框仍与切割位置一致。

    守的是「修祖先链没有把恒等场景弄坏」——恒等容器 chain=() 本来就对。
    """
    lp, _outer, _inner = _pasted((0.0, 0.0))
    _refresh(lp)

    drawn = _drawn_rects(lp)
    assert set(drawn) == {"OUTER", "INNER"}, f"组框集合不对：{drawn}"
    assert set(drawn.values()) == {_cut_aabb(lp.doc)}, \
        f"恒等嵌套下框应与切割位置重合：{drawn} vs {_cut_aabb(lp.doc)}"


# -- ② 顶层图元不受影响（绝大多数用户场景） ---------------------------------

def test_top_level_item_box_unchanged():
    """顶层图元（chain 恒空）走新路径结果不变 —— 不给普通文档带来回归。"""
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    it = Item(paths=[[(10.0, 12.0), (40.0, 30.0)]], name="solo")
    lp._add_items([it])
    lp._sync_models()

    x0, y0, x1, y1 = lp._page_box(it)
    assert (round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)) == \
        tuple(round(v, 6) for v in it.page_bbox()), \
        "顶层图元的 _page_box 应当等于裸 page_bbox（无祖先可带）"


def test_deeply_nested_chain_is_full_not_truncated():
    """三层嵌套 ⇒ 内层框必须带**全部**两层祖先变换，不是只带一层。"""
    payload = json.dumps([{
        "name": "L1", "pos": [100.0, 0.0], "z": 0.0,
        "children": [{
            "name": "L2", "pos": [20.0, 0.0], "z": 0.0,
            "children": [{
                "name": "L3", "pos": [5.0, 0.0], "z": 0.0,
                "children": [
                    {"name": "x", "z": 0.0,
                     "paths": [[(0.0, 0.0), (10.0, 0.0), (10.0, 0.0)]]},
                ],
            }],
        }],
    }])

    lp = LayoutPage()
    QtWidgets.QApplication.clipboard().setText(payload)
    lp._paste()
    lp._sync_models()
    _select_all_leaves(lp)
    _refresh(lp)

    drawn = _drawn_rects(lp)
    assert set(drawn) == {"L1", "L2", "L3"}, f"应画三个组框：{drawn}"
    cut = _cut_aabb(lp.doc)
    for name, box in drawn.items():
        assert box == cut, f"{name} 框 {box} 与切割位置 {cut} 不符"
    # 三层各偏移 100+20+5，粘贴 dz=5 只加根 ⇒ 内层实际 x0 = 100+20+5+5 = 130
    assert cut[0] == pytest.approx(130.0), f"切割 x0 应为 130，实得 {cut[0]}"


# -- ③ 会动刀的那一面：_unit_bbox 是对齐/缩放基线的唯一来源 ------------------

def test_unit_bbox_of_nested_group_is_where_it_cuts():
    """``_unit_bbox``（对齐/分布/属性面板/等比缩放 k 的来源）必须给出被切位置。

    这一条是本文件的安全面：bbox 错 ⇒ 用户「对齐」会把整组平移到别处 ⇒
    **下一刀切错地方**。修好框只解决观感，修好这里才解决后果。
    """
    lp, _outer, inner = _pasted((100.0, 0.0))
    lp._sync_models()

    box = lp._unit_bbox(inner)
    got = tuple(round(v, 6) for v in box)
    assert got == _cut_aabb(lp.doc), \
        (f"_unit_bbox(INNER)={got} 与实际被切位置 {_cut_aabb(lp.doc)} 不符"
         f" —— 对齐/分布会把它移到别处")
