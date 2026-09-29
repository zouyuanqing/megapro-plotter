"""R5 回归：「组自带折线」不得入档 —— 它会被切、却不会被画。

缺陷（R5 对 A3 的对抗性复核，复现于本仓库）：``_paste`` 对剪贴板文本只
``json.loads``、**零校验**，而 ``_item_from_json`` 对每个节点同时还原
``paths`` 与 ``children``。于是可以灌进一个「既是组、又自带折线」的容器，
而这个形状是**渲染/切割不对称**的：

- 切割面：``flatten_visible`` / ``iter_units`` 把它当**一等单元**枚举并切
  （``tests/test_gui_layout.py::test_container_own_geometry_reaches_enumeration``
  显式钉住这个行为，不许改）；
- 渲染面：``canvas/undo_cmds.make_gi`` 对容器 ``return None`` ⇒ 场景里
  **没有它的 PathItem**。

端到端危害（真 ``_paste`` 入口实测）::

    场景 PathItem = ['可见kid']          ← 容器自身那条线不在画布上
    JobSpec.paths_paper = 2 条
    G-code = G0 X15 Y15 / G1 X190 Y15 / G1 Z17 F300    ← 机器照切照压
    runnable = True

即：一条用户**看不见、也点不到删**的线进了发往机器的 G-code。删光可见子项
后更糟 —— 容器退回普通图元，却因 ``RemoveItemsCommand`` 不重建场景而始终没有
PathItem，机器每次执行仍切（该半边属 canvas/，见文件末与 stuck）。

**本文件钉的是「入档口」这条契约**：这种载荷整单拒绝并给中文提示。选
「拒绝」而不是「静默丢掉自身折线」是因为后者同样要命 —— 用户以为那条线也粘上
了，机器却**切得比预期少**；静默地做错比报错严重。
"""

import dataclasses
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets                           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.job import ZMap, compile_job           # noqa: E402
from megapro.gui.layout.layout_page import (             # noqa: E402
    LayoutPage, OwnGeometryContainerError, _item_from_json)


# -- 载荷构造 ---------------------------------------------------------------

def _own_geometry_payload(own_y=0.0, child_y=35.0, name="幽灵容器"):
    """组自带折线：own 在 y=own_y、child 在 y=child_y（两者相差足够大）。"""
    return json.dumps([{
        "name": name, "z": 0.0, "pos": [10.0, 10.0],
        "paths": [[(0.0, own_y), (175.0, own_y)]],
        "children": [{"name": "可见kid", "z": 0.0,
                      "paths": [[(0.0, child_y), (175.0, child_y)]]}],
    }])


def _paste(text):
    """走**真实** ``_paste`` 入口，收集状态消息。"""
    lp = LayoutPage()
    msgs: list = []
    lp.status_message.connect(msgs.append)
    QtWidgets.QApplication.clipboard().setText(text)
    lp._paste()
    lp._sync_models()
    return lp, msgs


def _gcode(lp):
    spec = dataclasses.replace(lp.to_job_spec(), zmap=ZMap(30.0, 17.0))
    return compile_job(spec, dry_run=False).lines


def _y_coords(lines):
    """G-code 里出现过的全部 Y 坐标（画线会不会落到那条线上就看这个）。"""
    out = set()
    for ln in lines:
        for tok in ln.split():
            if tok.startswith("Y"):
                out.add(float(tok[1:]))
    return out


# -- ① 端到端：不得有「看不见却被切」的线进 G-code --------------------------

def test_own_geometry_container_never_reaches_the_gcode():
    """粘贴这种载荷 ⇒ G-code 里**没有**容器自身那条线的 Y。

    这是 R5 危害的正面契约。修复前实测：G-code 含 `G0 X15 Y15` / `G1 X190 Y15`
    与 `G1 Z17 F300`（真下压、runnable=True），而场景里只有 `可见kid`。
    """
    lp, _msgs = _paste(_own_geometry_payload())

    ys = _y_coords(_gcode(lp))
    # 粘贴会把内容偏移 +5，own 线 y=0 ⇒ 机器坐标 15；child y=35 ⇒ 50
    assert 15.0 not in ys, f"容器自身的折线仍被下发给机器：Y 坐标 {sorted(ys)}"
    assert not any(ln.startswith("G1 Z17") for ln in _gcode(lp)), \
        "整单被拒却仍有真下压动作"


def test_own_geometry_paste_yields_an_empty_job():
    """整单拒绝 ⇒ ``to_job_spec().paths_paper`` 为空（不是「切一部分」）。"""
    lp, _msgs = _paste(_own_geometry_payload())

    assert lp.to_job_spec().paths_paper == [], \
        f"被拒的载荷仍留下了要切的几何：{lp.to_job_spec().paths_paper}"


def test_scene_and_cut_set_agree_after_paste():
    """画布上看得见的图元集合 == 将被切的折线集合（渲染/切割不得再不对称）。

    这条把「不对称」本身钉成不变量：任何一条进了 JobSpec 却画不出来的折线
    都会让本条报红 —— 不只针对本缺陷的那一种形状。

    比的是**页面系包围盒**（``unit_page_bbox``）：``Item.paths`` 存局部坐标、
    ``pos`` 承载偏移，直接拿局部点去比 spec 的页面点会永远不相等（那种写法
    恒真、等于没断言）。
    """
    lp, _msgs = _paste(_own_geometry_payload())

    drawn_boxes = set()
    for gi in lp._scene_items:
        it = gi.model_item
        if not it.paths:
            continue
        x0, y0, x1, y1 = it.unit_page_bbox(())
        drawn_boxes.add(tuple(round(v, 6) for v in (x0, y0, x1, y1)))

    extra = []
    for p in lp.to_job_spec().paths_paper:
        xs = [x for x, _ in p]
        ys = [y for _, y in p]
        box = (round(min(xs), 6), round(min(ys), 6),
               round(max(xs), 6), round(max(ys), 6))
        if box not in drawn_boxes:
            extra.append(box)

    assert not extra, f"这些折线会被切却画不出来：{extra}"


def test_legitimate_paste_keeps_scene_and_cut_set_in_agreement():
    """同一不变量在**合法**载荷下也成立（证明上一条不是因「两边都空」而绿）。"""
    good = json.dumps([
        {"name": "散件A", "z": 0.0, "pos": [7.0, 3.0],
         "paths": [[(0.0, 0.0), (10.0, 0.0), (10.0, 0.0)]]},
        {"name": "散件B", "z": 1.0, "pos": [20.0, 11.0],
         "paths": [[(0.0, 0.0), (5.0, 5.0), (5.0, 5.0)]]},
    ])
    lp, _msgs = _paste(good)

    drawn = {tuple(round(v, 6) for v in lp._page_box(gi.model_item))
             for gi in lp._scene_items if gi.model_item.paths}
    for p in lp.to_job_spec().paths_paper:
        xs = [x for x, _ in p]
        ys = [y for _, y in p]
        box = (round(min(xs), 6), round(min(ys), 6),
               round(max(xs), 6), round(max(ys), 6))
        assert box in drawn, f"合法载荷下也有画不出来的折线：{box} ∉ {drawn}"
    assert drawn, "前置：这条用例要有东西可断言（场景空了就退化成恒真）"


# -- ② 拒绝必须是可见的、整单的、递归到任意深度的 ---------------------------

def test_refusal_is_announced_in_chinese_with_the_offending_name():
    """拒绝**必须出声**：中文提示且指名道姓，不能静默丢内容。"""
    lp, msgs = _paste(_own_geometry_payload(name="我的组"))

    assert msgs, "拒绝粘贴却没有给用户任何消息 —— 这正是 R5 的病根"
    joined = "".join(msgs)
    assert "我的组" in joined, f"提示未指名 offending 图元：{joined}"
    assert "组" in joined and "折线" in joined, f"提示未说明原因：{joined}"


def test_whole_paste_is_rejected_not_partially():
    """好图元 + 坏容器混在一单 ⇒ **整单拒绝**。

    半途而入会切得比用户预期少（少的就是好图元旁边那条看不见的线），
    那与「静默丢弃」同罪。
    """
    mixed = json.dumps([
        {"name": "好图元", "z": 0.0, "paths": [[(1.0, 1.0), (20.0, 1.0)]]},
        json.loads(_own_geometry_payload())[0],
    ])
    lp, msgs = _paste(mixed)

    assert msgs, "含非法载荷的一单没有被拒绝"
    assert lp.doc.items == [], \
        f"部分入档了（会切得比预期少）：{[i.name for i in lp.doc.items]}"


def test_nested_own_geometry_is_rejected_at_any_depth():
    """违规节点藏在**深层子项**里也必须被拒（还原是递归的，校验也得是）。"""
    deep = json.dumps([{
        "name": "外层", "z": 0.0,
        "children": [{
            "name": "中层", "z": 0.0,
            "children": [{
                "name": "深层坏容器", "z": 0.0,
                "paths": [[(0.0, 0.0), (50.0, 0.0)]],
                "children": [{"name": "k", "z": 0.0,
                              "paths": [[(0.0, 0.0), (10.0, 0.0)]]}],
            }],
        }],
    }])
    lp, msgs = _paste(deep)

    assert msgs, "深层违规载荷未被拒绝"
    assert "深层坏容器" in "".join(msgs)
    assert lp.doc.items == []


# -- ③ 不得误伤：合法载荷照常入档 -------------------------------------------

def test_legitimate_container_without_own_paths_still_pastes():
    """组**不带**自身折线（doc_import 的 wrap_group 就是这个形状）照常粘贴。"""
    good = json.dumps([{
        "name": "正常组", "z": 0.0, "pos": [5.0, 5.0], "paths": [],
        "children": [
            {"name": "a", "z": 0.0, "paths": [[(0.0, 0.0), (10.0, 0.0)]]},
            {"name": "b", "z": 1.0, "paths": [[(0.0, 20.0), (10.0, 20.0)]]},
        ],
    }])
    lp, msgs = _paste(good)

    assert not msgs, f"合法载荷被误拒：{msgs}"
    assert len(lp.doc.items) == 1
    assert lp.doc.items[0].name == "正常组"
    assert len(lp.doc.items[0].children) == 2


def test_ordinary_items_and_nested_containers_still_paste():
    """普通图元 + 多层嵌套组（无自身折线）一律照常。"""
    payload = json.dumps([
        {"name": "散件", "z": 0.0, "paths": [[(1.0, 1.0), (20.0, 1.0)]]},
        {"name": "A", "z": 0.0, "children": [
            {"name": "B", "z": 0.0, "children": [
                {"name": "C", "z": 0.0,
                 "paths": [[(0.0, 0.0), (10.0, 0.0), (10.0, 0.0)]]},
            ]},
        ]},
    ])
    lp, msgs = _paste(payload)

    assert not msgs, f"合法载荷被误拒：{msgs}"
    assert {i.name for i in lp.doc.items} == {"散件", "A"}


# -- ④ 复制不得复刻这种形状 ---------------------------------------------------

def test_duplicate_refuses_to_clone_an_own_geometry_container():
    """文档里若已存在这种形状，**复制**整单拒绝，而不是再克隆一个看不见却会切的。

    构造走模型层（``Item(paths=..., children=[...])``）以绕开入档口校验 ——
    那正是「入档口之外仍可能有此形状」的情形，复制不能把它扩散出去。
    """
    from megapro.gui.layout.model import Item

    lp = LayoutPage()
    cont = Item(paths=[[(0.0, 0.0), (30.0, 0.0)]], name="老容器",
                children=[Item(paths=[[(0.0, 0.0), (5.0, 0.0)]], name="k")])
    lp._add_items([cont])
    lp._sync_models()
    gi = lp._gi_for(cont.children[0])
    if gi is not None:
        gi.setSelected(True)

    msgs: list = []
    lp.status_message.connect(msgs.append)
    lp._duplicate()

    assert msgs, "复制这种容器没有出声"
    assert "组" in "".join(msgs)
    assert len(lp.doc.items) == 1, f"复制出了额外的容器：{[i.name for i in lp.doc.items]}"


# -- ⑤ 还原层本身的契约（不经 UI） -------------------------------------------

def test_item_from_json_raises_on_own_geometry_container():
    """底层还原函数就应当拒绝，不依赖调用方记得检查。"""
    payload = json.loads(_own_geometry_payload())[0]
    with pytest.raises(OwnGeometryContainerError):
        _item_from_json(payload, dz=0.0, z=0.0)
