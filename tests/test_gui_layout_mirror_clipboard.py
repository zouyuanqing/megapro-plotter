"""B3 回归：镜像标志（a）进剪贴板/副本 （b）在界面上看得见。

缺陷（f2225ec 实测复现）：``_item_to_json`` 的字段白名单里**没有**
``mirror_x``/``mirror_y``，``_item_from_json`` 也不还原 ⇒
:func:`_clone_item`（Ctrl+D）与 Ctrl+C/V 出来的副本 ``mirror=(False,False)``，
页面几何是**未镜像**的原件整体 +5,+5mm。机理决定了这不是「少个字段」而是
**几何整个反了**：镜像是 Item 上的两个 bool，``transformed_paths`` /
``local_paths`` 在**读取时**施加，``model.paths`` 始终是未镜像原形。错误几何
直达机器（JobSpec → compile_job），全程不抛错不告警。

同时修「状态不可见」：两个镜像按钮原本 ``checkable=False``（点完 ``isChecked()``
恒为 False）、属性栏也没有镜像字段 ⇒ 用户切了镜像没有任何视觉反馈。

本文件的断言口径是**页面几何逐点**（经 ``to_job_spec()``，即真正送进
compile_job 的那份数据），不是 flag 也不是 bbox。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


#: L 形（未镜像页面几何 (20,20)-(30,30)），水平镜像后是「反射」的那一支
_L_SHAPE = [[(20.0, 20.0), (30.0, 20.0), (30.0, 30.0)]]
_L_MIRRORED = [[(30.0, 20.0), (20.0, 20.0), (20.0, 30.0)]]
#: 副本/粘贴的根偏移（``_item_from_json(dz=5.0)``，只加在根上）
_DZ = 5.0


def _shift(paths, dz=_DZ):
    return [[(x + dz, y + dz) for x, y in p] for p in paths]


def _page(page):
    """真正送进作业链的页面几何（``to_job_spec``，即 compile_job 的输入）。"""
    return [list(p) for p in page.to_job_spec().paths_paper]


def _mk(page, name="L", paths=None):
    """加一个图元并选中它（返回 ``(item, PathItem)``）。"""
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(x, y) for x, y in p]
                     for p in (paths if paths is not None else _L_SHAPE)],
              name=name)
    page._add_items([it])
    gi = page._gi_for(it)
    gi.setSelected(True)
    return it, gi


def _extra(page, name="伴生"):
    """造个陪衬（编组需 ≥2 成员 / 多选混合场景用）。"""
    from megapro.gui.layout.model import Item

    it = Item(paths=[[(50.0, 50.0), (60.0, 50.0)]], name=name)
    page._add_items([it])
    return it


def _assert_same_shape(got, want, what):
    """逐点恒等（不只比条数/bbox）——几何反了正是本缺陷的形态。"""
    assert len(got) == len(want), f"{what}: 折线条数 {len(got)} != {len(want)}"
    for i, (pa, pb) in enumerate(zip(got, want)):
        assert len(pa) == len(pb), f"{what}: 第 {i} 条点数 {len(pa)} != {len(pb)}"
        for j, (qa, qb) in enumerate(zip(pa, pb)):
            assert qa == qb, f"{what}: 第 {i} 条第 {j} 点 {qa} != {qb}"


# --- 核心回归：Ctrl+D / Ctrl+V 保住镜像 ------------------------------------

def test_mirror_then_duplicate_keeps_flag_and_geometry():
    """镜像 → Ctrl+D ⇒ 副本 flag 相同、页面几何 = 镜像态 + (5,5)。

    旧行为：副本 ``mirror=(False,False)``、几何 = **未镜像**原件 +5,+5
    （本页即 ``_shift(_L_SHAPE)``）—— 本用例必红。
    """
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    it, _gi = _mk(page)
    page._toggle_mirror("h")
    assert (it.mirror_x, it.mirror_y) == (True, False)
    _assert_same_shape(_page(page), _L_MIRRORED, "镜像后的原件")

    page._duplicate()

    assert len(page.doc.items) == 2
    dup = page.doc.items[-1]
    assert (dup.mirror_x, dup.mirror_y) == (True, False), "副本镜像标志丢了"
    assert _page(page)[-1] == _shift(_L_MIRRORED)[0], "副本几何不是镜像态 + 偏移"
    # 判别力：旧行为下这一条拿到的是未镜像形
    assert _page(page)[-1] != _shift(_L_SHAPE)[0]
    page.deleteLater()


def test_mirror_then_copy_paste_keeps_flag_and_geometry():
    """镜像 → Ctrl+C / Ctrl+V ⇒ 粘贴件同样保住 flag 与镜像态几何。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    it, _gi = _mk(page)
    page._toggle_mirror("v")
    assert (it.mirror_x, it.mirror_y) == (False, True)
    mirrored = _page(page)[0]
    assert mirrored != _L_SHAPE[0], "竖直镜像后几何应变（原样即没翻）"

    page._copy_selected()
    page._paste()

    assert len(page.doc.items) == 2
    pasted = page.doc.items[-1]
    assert (pasted.mirror_x, pasted.mirror_y) == (False, True), "粘贴件镜像标志丢了"
    _assert_same_shape([_page(page)[-1]], [_shift([mirrored])[0]],
                       "粘贴件几何")
    page.deleteLater()


def test_both_axes_mirrored_survive_duplicate():
    """**两个轴都镜像**时副本仍双镜像（白名单漏键时两轴一起丢）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    it, _gi = _mk(page, paths=[[(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)]])
    page._toggle_mirror("h")
    page._toggle_mirror("v")
    assert (it.mirror_x, it.mirror_y) == (True, True)
    before = _page(page)[0]

    page._duplicate()

    dup = page.doc.items[-1]
    assert (dup.mirror_x, dup.mirror_y) == (True, True)
    _assert_same_shape([_page(page)[-1]], [_shift([before])[0]], "双镜像副本")
    page.deleteLater()


def test_group_copy_keeps_child_mirror():
    """容器递归序列化也带走**子项**的镜像标志（子项 json 也曾丢）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    leaf, _gi = _mk(page, name="c")
    page._toggle_mirror("v")
    assert leaf.mirror_y is True
    page.doc.group_items([leaf, _extra(page)], name="组")
    cont = page.doc.items[0]
    assert cont.children, "应已编组"

    d = L._item_to_json(cont)
    assert "mirror_y" in d["children"][0], "子项 json 缺 mirror_y"
    back = L._item_from_json(d, dz=0.0, z=0.0)
    assert back.children[0].mirror_y is True, "子项镜像标志往返丢失"
    page.deleteLater()


def test_legacy_clipboard_without_mirror_keys_pastes_unmirrored():
    """旧剪贴板内容（无 mirror_* 键）仍能粘贴，按未镜像处理（向后兼容）。"""
    import megapro.gui.layout.layout_page as L

    legacy = [{"paths": [[(0.0, 0.0), (1.0, 0.0)]], "pos": (2.0, 3.0),
               "scale": 1.0, "angle_deg": 0.0, "name": "旧", "z": 1.0}]
    it = L._item_from_json(legacy[0], dz=0.0, z=1.0)
    assert (it.mirror_x, it.mirror_y) == (False, False)
    assert it.pos == (2.0, 3.0) and it.name == "旧"


# --- 界面可见性：按钮勾选态 ≡ 模型标志 -------------------------------------

def test_mirror_buttons_are_checkable_and_track_selection():
    """按钮 checkable，且 ``isChecked()`` 随选中项的镜像标志变化。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    it, _gi = _mk(page)
    bh, bv = page.btn_mirror["h"], page.btn_mirror["v"]
    assert bh.isCheckable() and bv.isCheckable(), "镜像按钮应可勾选"
    assert (bh.isChecked(), bv.isChecked()) == (False, False)

    page._toggle_mirror("h")
    assert (it.mirror_x, it.mirror_y) == (True, False)
    assert (bh.isChecked(), bv.isChecked()) == (True, False), "按钮未反映镜像态"

    page._undo.undo()                       # 撤销 ⇒ 标志回 False
    assert (it.mirror_x, it.mirror_y) == (False, False)
    assert (bh.isChecked(), bv.isChecked()) == (False, False), "撤销后按钮仍是亮"
    page.deleteLater()


def test_mirror_button_click_drives_model():
    """点**按钮**（不是调方法）真的驱动模型 —— 钉住 checkable 接的那根线。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    it, _gi = _mk(page)
    page.btn_mirror["h"].click()
    assert it.mirror_x is True and it.mirror_y is False
    page.btn_mirror["h"].click()            # 再点一次 ⇒ 取消
    assert it.mirror_x is False
    page.deleteLater()


def test_mirror_button_without_selection_warns_and_keeps_unchecked():
    """无选中点镜像：给中文提示，且按钮**不留假勾选**（Qt 已自动翻过）。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    _mk(page)
    seen: list = []
    page.status_message.connect(seen.append)
    page.scene.clearSelection()

    page.btn_mirror["v"].click()

    assert seen, "无选中点镜像应给状态提示（不得静默）"
    assert any("未选中" in s for s in seen), f"提示文案不对：{seen}"
    assert page.btn_mirror["v"].isChecked() is False, "被拒的点击不该留下勾选"
    assert len(page.doc.items) == 1
    page.deleteLater()


def test_props_panel_shows_mirror_state():
    """属性栏有镜像状态（只读指示）：单选四态 + 多选混合 + 无选中。"""
    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    it, _gi = _mk(page)
    assert page.lb_mirror.text() == "无"
    page._toggle_mirror("h")
    assert page.lb_mirror.text() == "水平"
    page._toggle_mirror("v")
    assert page.lb_mirror.text() == "水平+垂直"
    page._undo.undo()
    assert page.lb_mirror.text() == "水平"

    # 多选混合
    other = _extra(page)
    page._gi_for(other).setSelected(True)
    assert page.lb_mirror.text() == "混合"
    # 无选中
    page.scene.clearSelection()
    assert page.lb_mirror.text() == "无"
    assert it.mirror_x is True      # 指示器只读，不改模型
    page.deleteLater()


def test_mirror_label_is_read_only():
    """指示器**只读**：不提供编辑入口（切换只走工具条按钮的既有命令）。"""
    from PySide6 import QtWidgets

    import megapro.gui.layout.layout_page as L

    page = L.LayoutPage()
    assert not isinstance(page.lb_mirror, (QtWidgets.QCheckBox,
                                            QtWidgets.QLineEdit)), \
        "镜像状态应是只读指示，不该是可编辑控件"
    assert "镜像" in page.lb_mirror.toolTip()
    page.deleteLater()
