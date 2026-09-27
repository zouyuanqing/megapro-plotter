"""M4 多页管理（FR-09）—— 纯逻辑 + 离屏画布。

核心契约：
- **当前页门面逐位不变**：``Document(items=[...])`` 等既有构造/消费点全部
  原样可用（``tests/test_gui_layout.py`` 全部 Document 级用例不改字通过）。
- 页增/删/复制/切换/重排；场景随当前页重建。
- **单栈 + 命令页归属**（PRD §11-2 定稿）：跨页撤销改在**归属页**上，
  且执行完把视图页还原成用户按 Ctrl+Z 时所在的页。
- 导出/送作业/越界预检**按当前页**，单 SVG 契约不破。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _line(x0, y0, x1, y1, *, name="L", z=0.0):
    from megapro.gui.layout.model import Item

    return Item(paths=[[(float(x0), float(y0)), (float(x1), float(y1))]],
                name=name, z=z)


# --- 门面逐位不变（纯逻辑） ---------------------------------------------------

def test_single_page_document_is_unchanged():
    """默认单页：构造/门面/拍平与「无页」时代的语义逐位相同。"""
    from megapro.gui.layout.model import BED_H, BED_W, Document, flatten_visible

    d = Document()
    assert d.page_count == 1 and d.current == 0
    assert d.items == []
    assert d.bed_w == BED_W and d.bed_h == BED_H
    a = _line(0, 0, 10, 0, name="a", z=1)
    d.add(a)
    assert d.items == [a]
    assert d.top_z() == 1.0
    assert flatten_visible(d) == [[(0.0, 0.0), (10.0, 0.0)]]


def test_document_items_kwarg_still_supported():
    """既有 ``Document(items=[...])`` 关键字构造逐位可用（测试与页面都依赖）。"""
    from megapro.gui.layout.model import Document, flatten_visible

    a, b = _line(0, 0, 1, 0, name="a"), _line(5, 5, 6, 6, name="b")
    d = Document(items=[a, b])
    assert d.page_count == 1
    assert d.items == [a, b]           # 身份（非相等）判定
    assert d.page.items == [a, b]
    assert len(flatten_visible(d)) == 2


def test_items_property_is_live_reference_to_current_page():
    """``doc.items`` 是当前页的**活引用**（改它即改当前页，不是拷贝）。"""
    from megapro.gui.layout.model import Document

    d = Document()
    a = _line(0, 0, 1, 0, name="a")
    d.items.append(a)
    assert d.page.items == [a]
    d.add_page()
    d.switch_page(1)
    assert d.items == []               # 新页空
    d.items.append(_line(2, 2, 3, 3, name="b"))
    assert d.page.items and d.page.items[0].name == "b"
    d.switch_page(0)
    assert [i.name for i in d.items] == ["a"]


def test_facade_delegates_group_and_enumeration():
    """组/枚举门面全部走当前页（编组、contains、items_visible、owner_of）。"""
    from megapro.gui.layout.model import Document

    d = Document(items=[_line(0, 0, 1, 0, name="a"), _line(2, 0, 3, 0, name="b")])
    a, b = d.items
    cont = d.group_items([a, b], name="G")
    assert cont is not None and d.owner_of(a) is cont
    assert d.contains(a) and d.contains(cont)
    assert len(d.items_visible()) >= 2
    assert d.ungroup(cont) and d.owner_of(a) is None


# --- 页管理（纯逻辑） --------------------------------------------------------

def test_add_switch_and_page_isolation():
    """加页/切页后各页内容互不可见（门面只看当前页）。"""
    from megapro.gui.layout.model import Document, flatten_visible

    d = Document(items=[_line(0, 0, 10, 0, name="A")])
    p1 = flatten_visible(d)
    d.add_page()
    assert d.current == 0              # add_page 默认不切换
    d.switch_page(1)
    assert flatten_visible(d) == []    # 新页空
    d.add(_line(0, 0, 5, 5, name="B"))
    assert flatten_visible(d) != []
    d.switch_page(0)
    assert flatten_visible(d) == p1    # 回到原页，几何逐位一致


def test_duplicate_page_deep_copies_items():
    """复制页 = 深拷贝整页图元树（两页图元**身份互不共享**）。"""
    from megapro.gui.layout.model import Document, flatten_visible

    a = _line(0, 0, 10, 0, name="A")
    d = Document(items=[a])
    idx = d.duplicate_page(0)
    assert idx == 1
    clone = d.pages[1].items[0]
    assert clone is not a                      # 深拷贝，非同一对象
    assert clone.paths == a.paths              # 几何相同
    assert clone.pos == a.pos
    # 改副本不影响原页
    from megapro.gui.layout.model import flatten_visible

    clone.pos = (99.0, 99.0)
    assert d.pages[0].items[0].pos == (0.0, 0.0)   # 原页未受影响
    # 两页拍平此刻**不同**（证明身份互不共享、修改不串页）
    assert flatten_visible(Document(pages=[d.pages[0]])) == \
        [[(0.0, 0.0), (10.0, 0.0)]]
    assert flatten_visible(Document(pages=[d.pages[1]])) == \
        [[(99.0, 99.0), (109.0, 99.0)]]


def test_remove_page_keeps_at_least_one():
    """删页；最后一页不可删（返回 False，不清空文档）。"""
    from megapro.gui.layout.model import Document

    d = Document(items=[_line(0, 0, 1, 0, name="A")])
    d.add_page()
    d.add_page()
    assert d.page_count == 3
    d.remove_page(1)
    assert d.page_count == 2
    d.remove_page(0)
    assert d.page_count == 1
    assert d.remove_page(0) is False          # 最后一页保底
    assert d.page_count == 1


def test_move_page_reorders_and_follows_current():
    """页重排：页序变化，且当前页跟着它走（内容不丢）。"""
    from megapro.gui.layout.model import Document

    d = Document()
    for name in "ABC":
        d.add_page(name=name)
    assert [pg.name for pg in d.pages] == ["页1", "A", "B", "C"]  # 默认页 + 3
    d.switch_page(2)                            # 当前 B
    assert d.page.name == "B"
    d.move_page(0, 3)                           # 默认页 移到末尾
    assert [pg.name for pg in d.pages] == ["A", "B", "C", "页1"]
    assert d.current == 1                       # B 跟着从 2 移到 1
    assert d.page.name == "B"


def test_page_of_finds_item_across_pages():
    """``page_of`` 跨页身份查找（undo 页归属的底座）。"""
    from megapro.gui.layout.model import Document

    a = _line(0, 0, 1, 0, name="A")
    d = Document(items=[a])
    d.add_page()
    d.switch_page(1)
    b = _line(0, 0, 2, 0, name="B")
    d.add(b)
    assert d.page_of(a) == 0
    assert d.page_of(b) == 1
    assert d.page_of(_line(0, 0, 9, 9, name="X")) is None


# --- 导出/送作业按当前页（离屏） ---------------------------------------------

def test_export_and_job_spec_use_current_page():
    """导出与 ``to_job_spec`` 取**当前页**（单 SVG 契约不破）。"""
    from megapro.gui.layout.layout_page import LayoutPage
    from megapro.gui.layout.export_svg import document_to_svg
    from megapro.toolchain.svg_to_gcode import parse_svg

    lp = LayoutPage()
    lp._add_items([_line(0, 0, 10, 0, name="A")])
    p0_job = lp.to_job_spec().paths_paper
    lp._on_page_add()  # 切到新页
    assert lp.to_job_spec().paths_paper == []          # 当前页空
    lp._add_items([_line(0, 0, 4, 4, name="B")])
    assert len(lp.to_job_spec().paths_paper) == 1      # 只有 B

    # 导出亦按当前页
    lp._on_page_tab_changed(0)
    svg0 = document_to_svg(lp.doc)
    lp._on_page_tab_changed(1)
    svg1 = document_to_svg(lp.doc)
    assert svg0 != svg1
    assert len(parse_svg_from(svg1)) == 1               # p1 只有一条
    assert len(parse_svg_from(svg0)) == 1               # p0 也只有一条（不同几何）
    lp.deleteLater()


def parse_svg_from(svg_text):
    """把 SVG 字符串写到临时文件再 parse（复用 toolchain 解析入口）。"""
    import tempfile
    from pathlib import Path

    from megapro.toolchain.svg_to_gcode import parse_svg

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False,
                                     mode="w", encoding="utf-8") as fh:
        fh.write(svg_text)
        p = fh.name
    try:
        return parse_svg(p)
    finally:
        Path(p).unlink(missing_ok=True)


def test_bounds_precheck_uses_current_page():
    """越界预检按**当前页**（别的页的超床图元不污染当前页判定）。

    越界预检的循环体吃 ``iter_units(self.doc.items, ...)``，而 ``doc.items``
    是当前页门面 ⇒ 天然只看当前页。本用例从枚举面断言这一点（不发模态框）。
    """
    from megapro.gui.canvas.coords import BED_W
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    lp._on_page_add()  # 切到 p1
    far = _line(0, 0, 10, 0, name="far")
    far.pos = (BED_W + 100.0, 0.0)  # x 超 210
    lp._add_items([far])
    assert any(it is far for it in lp.doc.items_visible())   # p1 看得见
    lp._on_page_tab_changed(0)                              # 切到空页 p0
    assert all(it is not far for it in lp.doc.items_visible())  # p0 看不见超床图元
    lp.deleteLater()


# --- 场景随页重建（离屏） ----------------------------------------------------

def test_scene_rebuilds_on_page_switch():
    """切页 → 场景全量重建，与当前页内容一致。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a = _line(0, 0, 10, 0, name="A")
    lp._add_items([a])
    assert len(lp._scene_items) == 1
    lp._on_page_add()  # 新空页
    assert len(lp._scene_items) == 0         # 空页 → 场景清空
    b = _line(0, 0, 5, 5, name="B")
    lp._add_items([b])
    assert len(lp._scene_items) == 1
    lp._on_page_tab_changed(0)
    assert len(lp._scene_items) == 1
    assert lp._scene_items[0].model_item is a  # 回到 p0 看到 A
    lp.deleteLater()


def test_scene_skips_containers_rebuilds_leaves():
    """重建按 ``iter_leaves``：容器不建 PathItem，只建叶子（M1/M2 契约延续）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a = _line(0, 0, 10, 0, name="a")
    b = _line(2, 0, 3, 0, name="b")
    lp._add_items([a, b])
    lp.doc.group_items([a, b], name="G")
    lp._on_page_add()
    lp._on_page_tab_changed(0)  # 触发重建
    assert len(lp._scene_items) == 2         # 两条叶子
    assert all(gi.model_item is not lp.doc.items[0] for gi in lp._scene_items)
    lp.deleteLater()


# --- 撤销页归属（FR-09 / §11-2：单栈 + 命令页归属） --------------------------

def test_undo_across_pages_acts_on_owning_page():
    """跨页撤销：在**归属页**上改模型，且还原用户所在视图页。

    这是单栈 + 页归属的核心保证：在 p1 按 Ctrl+Z 撤销 p0 上的一次编辑，
    必须改 p0（不是当前的 p1），执行完视图回到 p1。
    """
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a = _line(0, 0, 10, 0, name="A")
    lp._add_items([a])               # 命令归属 p0
    lp._on_page_add()                # 切到 p1
    assert lp.doc.current == 1
    lp._undo.undo()                  # 在 p1 上撤销 p0 的添加
    assert lp.doc.pages[0].items == []      # p0 真的被撤销
    assert lp.doc.pages[1].items == []      # p1 本就空，未被误改
    assert lp.doc.current == 1               # 视图页还原为 p1
    lp.deleteLater()


def test_redo_after_cross_page_undo_targets_owning_page():
    """跨页撤销后 redo 也回**归属页**（不串页）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a = _line(0, 0, 10, 0, name="A")
    lp._add_items([a])               # 归属 p0
    lp._on_page_add()                # 切到 p1
    lp._undo.undo()
    assert lp.doc.pages[0].items == []
    lp._undo.redo()                   # 重做回 p0
    assert len(lp.doc.pages[0].items) == 1
    assert lp.doc.pages[1].items == []
    lp.deleteLater()


def test_each_page_edits_undo_independently():
    """每页独立编辑、撤销互不串页（交替编辑两条命令，逐条退）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    a = _line(0, 0, 10, 0, name="A")
    lp._add_items([a])               # p0: 加 A
    lp._on_page_add()                # 切 p1
    b = _line(0, 0, 5, 5, name="B")
    lp._add_items([b])               # p1: 加 B
    assert len(lp.doc.pages[0].items) == 1
    assert len(lp.doc.pages[1].items) == 1
    lp._undo.undo()                  # 退「p1 加 B」
    assert len(lp.doc.pages[1].items) == 0
    assert len(lp.doc.pages[0].items) == 1   # p0 不受影响
    lp._undo.undo()                  # 退「p0 加 A」
    assert len(lp.doc.pages[0].items) == 0
    lp.deleteLater()


# --- 页签 UI（离屏） --------------------------------------------------------

def test_page_tab_bar_reflects_pages():
    """页签条与当前页一致（数量/当前下标）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    assert lp._page_bar.count() == 1
    lp._on_page_add()
    assert lp._page_bar.count() == 2
    assert lp._page_bar.currentIndex() == lp.doc.current
    lp._on_page_del()
    assert lp._page_bar.count() == 1
    lp.deleteLater()


def test_page_bar_last_page_delete_guarded():
    """UI 层「－」删最后一页被拒（至少一页）。"""
    from megapro.gui.layout.layout_page import LayoutPage

    lp = LayoutPage()
    msgs: list[str] = []
    lp.status_message.connect(msgs.append)
    lp._on_page_del()  # 最后一页
    assert lp.doc.page_count == 1
    assert any("至少保留一页" in m for m in msgs)
    lp.deleteLater()
