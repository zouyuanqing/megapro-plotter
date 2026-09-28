"""C3 回归：排版页五处「界面撒谎 / 静默吞操作」的诚实性契约。

用户拍板的裁决分两类，本文件分别钉：

- **要出声的**（1、4、5）：无选中点镜像、编组内图片双击被「进组」消费、
  Canny 阈值 low>high —— 这三处原本**静默**：操作被吞 / 要点确定才报错。
- **只要说清的**（2、3）：数学与语义都**不动**（PRD v1.3「不补偿」裁决，
  组镜像维持逐成员），但界面必须把「为什么数字会跳」「镜像到底作用在谁身上」
  写在用户看得见的地方，否则用户以为程序出错。

⚠ **本文件钉的是「界面上有没有这句话 / 有没有这次提示」，不是文案的逐字内容**
（除 Canny 提示行外）：措辞会迭代，契约是「用户不再被静默坑」。故断言一律
用 ``toolTip()`` / ``objectName`` / 信号，不做像素级、不比对整段文案。

几何那条（:func:`test_rotated_mirror_changes_aabb_but_not_shape`）反过来是
**守住「数学不动」**的：它证明 AABB 确实会跳、而图形边长与面积确实不变 ——
tooltip 里那几句话因此是真的，不是安慰话。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# -- 版面构造 --------------------------------------------------------------

def _page():
    from megapro.gui.layout.layout_page import LayoutPage

    return LayoutPage()


def _tri(cx=100.0, cy=100.0, size=10.0, *, angle=0.0, name="tri"):
    """**偏心**三角形（本地坐标，3 顶点 + 闭合），刻意不对称。

    为什么不能用等腰对称三角形：绕 bbox 中心镜像会把它的点集映回**同一个**
    矩形 ⇒ AABB 恒等 ⇒ 测不出「AABB 会跳」，且这不是实现对、是这个形状的
    几何恒等式。真实用户手里的图形大多不对称，旋转后镜像才真的挪 AABB。
    """
    from megapro.gui.layout.model import Item

    half = size / 2.0
    it = Item(paths=[[(cx - half, cy - half), (cx + half, cy - half),
                      (cx + half * 0.4, cy + half), (cx - half, cy - half)]],
              name=name)
    it.angle_deg = angle
    return it


def _poly_edge_lengths(paths) -> list[float]:
    out = []
    for poly in paths:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
            out.append(round(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 9))
    return sorted(out)


def _poly_area(paths) -> float:
    total = 0.0
    for poly in paths:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
            total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


# -- ① 无选中点镜像 ⇒ 必须出声（守卫：修复早于本文件） ----------------------

def test_mirror_without_selection_emits_status_hint():
    """无选中时点镜像 ⇒ 发中文状态消息，且**不动镜像标志**。

    旧行为是 ``if not sel: return`` —— 按钮被 Qt 自动翻成勾上、什么也没发生、
    状态栏一声不吭（守卫在 layout_page._toggle_mirror 里先回滚勾选态再提示）。
    """
    lp = _page()
    lp._add_items([_tri()])                 # 页面**有**图元，只是没选中
    seen: list = []
    lp.status_message.connect(seen.append)

    lp._toggle_mirror("h")

    assert seen, "无选中点镜像被静默吞掉了（用户不知道该先选中）"
    assert any("未选中" in s for s in seen), f"提示未说明原因：{seen}"
    assert lp.doc.items[0].mirror_x is False, "无选中不得改动任何镜像标志"
    assert not lp.btn_mirror["h"].isChecked(), "按钮勾选态必须回滚（无选中=全 False）"


# -- ③ 组镜像语义必须写在按钮上 --------------------------------------------

def test_mirror_button_tooltip_states_per_member_semantics():
    """镜像按钮 tooltip 必须写清：**逐个成员**各绕自身中心，不是整组刚性反射。

    实现的既定语义是逐成员（PRD FR-08 只定义 Item 级；改成整组刚性反射会突破
    v1.3「不补偿/仅标志」裁决），而这一点原先只写在 tests/test_gui_mirror.py 的
    docstring 里，界面零提示 —— 用户会以为镜像是对整个选中集合做的。
    """
    lp = _page()
    for axis in ("h", "v"):
        tip = lp.btn_mirror[axis].toolTip()
        assert "逐个成员" in tip, f"{axis} 轴 tooltip 未说明逐成员语义：{tip!r}"
        assert "刚性反射" in tip, f"{axis} 轴 tooltip 未排除整组刚性反射：{tip!r}"
        assert "各绕自身中心" in tip or "自身中心" in tip, \
            f"{axis} 轴 tooltip 未说明支点是自身中心：{tip!r}"


# -- ② 宽/高跳变必须在属性面板上说清 ----------------------------------------

def test_wh_spinboxes_explain_aabb_and_scale_baseline():
    """宽/高 tooltip 必须点明两件事：AABB 口径 + 它就是缩放基线。

    第二件尤其要紧：:meth:`LayoutPage._apply_size` 用 ``k = 输入值 / 当前 AABB``，
    数字跳过之后**用户改宽高会跟着变**；只说「数字会跳」而不说「输入值也以它
    为基准」是半个答案。
    """
    lp = _page()
    for name, sp in (("宽", lp.sp_w), ("高", lp.sp_h)):
        tip = sp.toolTip()
        assert tip, f"{name} 没有 tooltip"
        assert "AABB" in tip, f"{name} 未说明是轴对齐包围盒：{tip!r}"
        assert "镜像" in tip, f"{name} 未提镜像会让数字跳变：{tip!r}"
        assert "等比缩放" in tip and "基准" in tip, \
            f"{name} 未说明输入值以当前 AABB 为缩放基准：{tip!r}"


def test_rotated_mirror_changes_aabb_but_not_shape():
    """守住「数学不动」：镜像后 **AABB 变、图形本身不变**。

    这条是 ② 那几句 tooltip 的**前提**：若哪天几何真的变了/没变，tooltip 就在
    撒谎。刻意用 30°（非 90° 整数倍，PRD 说只有 90° 整数倍 AABB 守恒）。
    """
    lp = _page()
    it = _tri(angle=30.0)
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)

    before_box = lp._page_box(it)
    before_edges = _poly_edge_lengths(it.paths)
    before_area = _poly_area(it.paths)
    before_pos, before_ang, before_scale = it.pos, it.angle_deg, it.scale

    lp._toggle_mirror("h")

    after_box = lp._page_box(it)
    w_before = abs(before_box[2] - before_box[0])
    w_after = abs(after_box[2] - after_box[0])
    assert round(w_before, 6) != round(w_after, 6), \
        "30° 件镜像后 AABB 宽本应变化（不变化说明数学被改了，tooltip 就在撒谎）"
    # 图形本身：边长与面积逐项恒等 —— 这才是 tooltip 承诺的「没变大变小」
    assert _poly_edge_lengths(it.paths) == before_edges, "镜像改动了图形边长"
    assert round(_poly_area(it.paths), 6) == round(before_area, 6), "镜像改动了图形面积"
    # 数学裁决（PRD v1.3「不补偿」）：pos / angle / scale 一律不动。
    # 注意是「与镜像前相同」，不是某个具体数值 —— 本用例的路径坐标是写进
    # paths 的，pos 本来就是 (0,0)。
    assert it.pos == before_pos, "镜像补偿了 pos（违反不补偿裁决）"
    assert it.angle_deg == before_ang, "镜像补偿了 angle（违反不补偿裁决）"
    assert it.scale == before_scale, "镜像补偿了 scale（违反不补偿裁决）"
    assert it.mirror_x is True


def test_wh_spinbox_value_is_the_aabb_used_as_scale_baseline():
    """属性面板的宽/高显示值 == _apply_size 用的那个 AABB（口径真的对上了）。

    若哪天面板改显示别的量而 :meth:`_apply_size` 仍用 AABB，用户看到的数字和
    缩放行为就对不上；这条把两者钉在一起。
    """
    lp = _page()
    it = _tri(angle=30.0)
    lp._add_items([it])
    lp._gi_for(it).setSelected(True)
    lp._refresh_props()
    x0, y0, x1, y1 = lp._page_box(it)
    # 面板按 spinbox 自己的 decimals(2) 量化显示，故比对到 2 位 —— 比对的是
    # 「口径同一个 AABB」，不是「小数位一模一样」。
    assert round(lp.sp_w.value(), 2) == round(abs(x1 - x0), 2)
    assert round(lp.sp_h.value(), 2) == round(abs(y1 - y0), 2)


# -- ④ 编组内图片双击被「进组」消费 ⇒ 必须出声 ------------------------------

def _grouped_image_page():
    """组 G{img}：img 带 image_spec（图片件），已编组、未进组编辑。"""
    from megapro.gui.layout.model import Item

    lp = _page()
    img = Item(paths=[[(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 10.0)]],
               name="img", image_spec={"mode": "canny", "low": 50, "high": 120})
    other = Item(paths=[[(30.0, 10.0), (40.0, 10.0), (40.0, 20.0),
                         (30.0, 10.0)]], name="other")
    lp._add_items([img, other])
    cont = lp.doc.group_items([img, other], name="G")
    assert cont is not None
    return lp, img


def test_grouped_image_double_click_tells_user_to_double_click_again():
    """组内图片第 1 次双击（被进组消费）⇒ 提示「再双击一次可重追调参」。

    旧行为：第 1 次只进组、重追调用数 0、界面零提示 —— 用户得连双两次才知道
    能调参。提示必须点名「再双击一次」，否则等于没提示。
    """
    lp, img = _grouped_image_page()
    seen: list = []
    lp.status_message.connect(seen.append)

    consumed = lp.on_item_double_clicked(img)

    assert consumed is True, "组内子项的第 1 次双击应被进组消费"
    assert seen, "第 1 次双击被静默吞掉了（用户无从得知要再双一次）"
    assert any("再双击" in s for s in seen), f"提示未说明要再双击一次：{seen}"
    assert any("重追" in s for s in seen), f"提示未点名重追调参：{seen}"


def test_grouped_plain_item_double_click_also_announces_group_entry():
    """组内**非图片**双击也出声（进组本身是状态变化，不该无声）。"""
    from megapro.gui.layout.model import Item

    lp = _page()
    a = Item(paths=[[(0.0, 0.0), (5.0, 5.0)]], name="a")
    b = Item(paths=[[(5.0, 0.0), (9.0, 4.0)]], name="b")
    lp._add_items([a, b])
    assert lp.doc.group_items([a, b], name="G") is not None
    seen: list = []
    lp.status_message.connect(seen.append)

    assert lp.on_item_double_clicked(a) is True

    assert seen, "进组编辑无声发生"
    assert any("组编辑" in s for s in seen), f"提示未说明已进入组编辑：{seen}"


def test_top_level_double_click_is_not_announced_as_group_entry():
    """顶层图元双击**不进组**，故不应谎称进了组（守卫上面的提示别过度触发）。"""
    lp = _page()
    it = _tri()
    lp._add_items([it])
    seen: list = []
    lp.status_message.connect(seen.append)

    assert lp.on_item_double_clicked(it) is False, "顶层图元不该被进组消费"
    assert not any("组编辑" in s for s in seen), f"顶层双击谎称进组：{seen}"


# -- ⑤ Canny low>high ⇒ 对话框内即时提示、且不关闭 ---------------------------

def _drive_image_dialog(monkeypatch, *, mode_label, low, high, spec=None):
    """跑一次 :meth:`_ask_image_params`，在 ``exec()`` 里改值并交回控件。

    ``mode_label`` 用**下标文字**定位模式项；Canny 是 ``_IMAGE_MODES`` 的第三项。
    返回 ``(返回值, 控件字典)`` —— 用真 ``valueChanged`` 通路驱动，不直接调内部
    函数，所以测的是用户真的按下拉框/输数字会发生什么。
    """
    lp = _page()
    box: dict = {}

    def fake_exec(self):
        form = self.layout()
        lo = hi = None
        for spin in self.findChildren(QtWidgets.QSpinBox):
            label = form.labelForField(spin)
            if label is None:
                continue
            if "低阈" in label.text():
                lo = spin
            elif "高阈" in label.text():
                hi = spin
        assert lo is not None and hi is not None, "找不到 Canny 阈值输入框"
        combo = self.findChildren(QtWidgets.QComboBox)[0]
        for i in range(combo.count()):
            if combo.itemText(i) == mode_label:
                combo.setCurrentIndex(i)
        lo.setValue(low)
        hi.setValue(high)
        bb = self.findChildren(QtWidgets.QDialogButtonBox)[0]
        box.update(ok=bb.button(QtWidgets.QDialogButtonBox.Ok),
                   warn=self.findChild(QtWidgets.QLabel, "cannyThresholdWarn"),
                   cancel=bb.button(QtWidgets.QDialogButtonBox.Cancel))
        return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(QtWidgets.QDialog, "exec", fake_exec)
    return lp._ask_image_params(spec=spec), box


_CANNY = "照片/素描（Canny+骨架）"
_CENTER = "中心线（骨架，消双线）"


def _hint(box) -> str:
    """对话框当前的提示文案；**没有提示行**也算「没提示」。

    写成「缺行即空」而不是硬取 ``.text()`` 是刻意的：这三类用例是**防误伤**
    的守卫（合法阈值对 / low==high / 非 Canny 模式不得被拦），它们描述的行为
    在修复前后都成立。若因为「修复前压根没有提示行」而 AttributeError，它们就
    从守卫退化成凑数的红灯 —— 守卫必须新旧都绿，才说明修复没过头。
    """
    w = box.get("warn")
    return w.text() if w is not None else ""


def test_canny_low_gt_high_blocks_ok_with_chinese_hint(monkeypatch):
    """Canny 模式下 low>high ⇒ 当场给中文提示、**并置灰确定**（点了不关闭）。

    旧行为：对话框不校验，用户把低阈调得大于高阈，要点确定、被 _trace_image
    抛 ValueError 才弹「重追失败」—— 参数已经出去了。置灰确定即「不关闭、
    不丢已填参数」。
    """
    _ret, box = _drive_image_dialog(monkeypatch, mode_label=_CANNY, low=200, high=50)

    assert box["ok"] is not None, "找不到确定按钮"
    assert not box["ok"].isEnabled(), "low>high 时确定仍可点 ⇒ 对话框等于没校验"
    assert box["warn"] is not None, "找不到提示行"
    text = box["warn"].text()
    assert text, "low>high 没有给任何即时提示"
    assert "不能大于" in text, f"提示未说明约束：{text!r}"
    assert "200" in text and "50" in text, f"提示未回显实际阈值：{text!r}"
    assert box["cancel"].isEnabled(), "取消必须始终可用（逃生口不能被堵）"


def test_canny_valid_pair_keeps_ok_enabled_and_no_hint(monkeypatch):
    """合法阈值对（low<=high）⇒ 不拦、不提示（校验不能变成骚扰）。"""
    _ret, box = _drive_image_dialog(monkeypatch, mode_label=_CANNY, low=50, high=120)

    assert box["ok"].isEnabled(), "合法阈值对被误拦"
    assert _hint(box) == "", f"合法时不该有提示：{_hint(box)!r}"


def test_canny_equal_thresholds_allowed(monkeypatch):
    """low == high 是 Canny 的合法极值（单阈值），不得当成越界拦掉。"""
    _ret, box = _drive_image_dialog(monkeypatch, mode_label=_CANNY, low=0, high=0)

    assert box["ok"].isEnabled(), "low==high（含 0，0 是合法取值）被误拦"
    assert _hint(box) == ""


def test_non_canny_mode_does_not_block_on_threshold_order(monkeypatch):
    """非 Canny 模式：low/high 压根不参与产线，**不得**拿它拦「确定」。

    守卫上面那条的误伤：`_image_spec` 只在 canny 下消费 low/high，其余模式
    它们是惰性值。
    """
    _ret, box = _drive_image_dialog(monkeypatch, mode_label=_CENTER, low=200, high=50)

    assert box["ok"].isEnabled(), "非 Canny 模式被阈值顺序误伤（low/high 惰性）"
    assert _hint(box) == ""


def test_canny_hint_clears_when_switching_mode_away(monkeypatch):
    """从 Canny 切到别的模式 ⇒ 提示自动撤掉、确定恢复可用（提示不残留）。"""
    lp = _page()
    box: dict = {}

    def fake_exec(self):
        form = self.layout()
        lo = hi = None
        for spin in self.findChildren(QtWidgets.QSpinBox):
            label = form.labelForField(spin)
            if label is None:
                continue
            if "低阈" in label.text():
                lo = spin
            elif "高阈" in label.text():
                hi = spin
        combo = self.findChildren(QtWidgets.QComboBox)[0]
        for i in range(combo.count()):
            if combo.itemText(i) == _CANNY:
                combo.setCurrentIndex(i)
        lo.setValue(200)
        hi.setValue(50)
        bb = self.findChildren(QtWidgets.QDialogButtonBox)[0]
        ok = bb.button(QtWidgets.QDialogButtonBox.Ok)
        warn = self.findChild(QtWidgets.QLabel, "cannyThresholdWarn")
        assert not ok.isEnabled() and warn is not None and warn.text(), \
            "前置状态不对（low>high 应被拦并给提示）"
        for i in range(combo.count()):          # 切走
            if combo.itemText(i) == _CENTER:
                combo.setCurrentIndex(i)
        box.update(ok=ok, warn=warn)
        return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(QtWidgets.QDialog, "exec", fake_exec)
    lp._ask_image_params()

    assert _hint(box) == "", f"切走后提示残留：{_hint(box)!r}"
    assert box["ok"].isEnabled(), "切走后确定应恢复可用"
