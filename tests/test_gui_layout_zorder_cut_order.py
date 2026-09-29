"""R7 回归：置顶/置底的**跨单元相对序**必须由文档 z 决定，不许由 Qt 决定。

缺陷（R7 对 C2 的对抗性复核，复现于本仓库）：:meth:`LayoutPage._zorder` 的
top/bottom 分支按 ``for unit, _ in self._selected_units()`` 的**迭代顺序**分配
游标（``cursor += 1``），而 :meth:`_selected_units` 走
``scene.selectedItems()`` —— **返回顺序无保证**。同一场景跨进程实测 12 次出现
5 种次序，收敛成**两个不同的结果**::

    units=['G','M2'] ⇒ M0=51, M1=52, M2=53 ⇒ 切割 ['X','M0','M1','M2']
    units=['M2','G'] ⇒ M0=52, M1=53, M2=51 ⇒ 切割 ['X','M2','M0','M1']

两条都满足 C2 的三条断言（连续段 + 不与未选中交叠 + 组内相邻），所以那 35 条
回归抓不住它——被刻意标注为「会变的量」而放开的那个量，**恰好就是发往机器的
切割次序**（``flatten_visible`` → ``JobSpec.paths_paper`` → ``gcode_for_*``
顺序发射）。对切纸机而言，穿过同一片叠料的两刀先后是可观测的切割结果。

**本文件怎么把随机变确定**：不靠「多跑几次进程看会不会分叉」（那是概率），
而是**在进程内把 Qt 的返回顺序显式换成两种**（monkeypatch
``_selected_units``），断言两种输入给出**逐位相同**的 z 与切割次序。这样测试
既确定又能精确咬住缺陷：修好前第二种顺序立刻分叉。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication               # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.layout_page import LayoutPage     # noqa: E402
from megapro.gui.layout.model import (Item, flatten_visible,  # noqa: E402
                                      iter_leaves)


# -- 场景（与 tests/test_gui_layout_zorder_and_jobsync.py 的 _mixed_page 同形） --

def _L(x, z, name):
    """一条水平线：x 决定几何位置与身份，z 决定层序。"""
    return Item(paths=[[(float(x), 0.0), (float(x) + 5.0, 0.0)]], name=name, z=z)


def _mixed_page():
    """X 散件 z=50（未选中）+ 组 G{M0(1), M1(2)} + 散件 M2(3)。"""
    lp = LayoutPage()
    items = {n: _L(x, z, n) for n, x, z in
             (("X", 0, 50), ("M0", 10, 1), ("M1", 20, 2), ("M2", 30, 3))}
    for it in items.values():
        lp._add_items([it])
    g = lp.doc.group_items([items["M0"], items["M1"]], name="G")
    assert g is not None
    lp._sync_models()
    return lp, items, g


def _cut_order(doc):
    """拍平（=切割）次序里的图元名。"""
    out = []
    xs = {round(p[0][0], 6) for p in flatten_visible(doc)}
    for it in iter_leaves(doc.items):
        if round(it.paths[0][0][0], 6) in xs:
            out.append(it.name)
    return [n for n in out if n in {"X", "M0", "M1", "M2"}]


def _zs(doc):
    return {it.name: it.z for it in iter_leaves(doc.items)}


def _force_unit_order(monkeypatch, lp, order):
    """把 :meth:`_selected_units` 的返回顺序钉死成 ``order``（含组名 G）。

    真实场景里这个顺序由 ``scene.selectedItems()`` 决定、跨进程会跳；这里把
    两种都出现过的次序各自变成一次确定性输入。
    """
    real = lp._selected_units()
    by_name = {u.name: (u, c) for u, c in real}
    missing = [n for n in order if n not in by_name]
    assert not missing, f"场景里没有单元 {missing}，实有 {sorted(by_name)}"
    monkeypatch.setattr(type(lp), "_selected_units",
                        lambda self, _o=order, _b=by_name:
                        [_b[n] for n in _o])


# 两种都真实出现过的 Qt 返回次序
_ORDERS = {
    "组在前": ["G", "M2"],
    "散件在前": ["M2", "G"],
}


# -- ① 核心：跨单元相对序与 Qt 返回序无关 -----------------------------------

@pytest.mark.parametrize("order", sorted(_ORDERS))
def test_top_lays_units_out_in_pre_existing_z_order(order, monkeypatch):
    """置顶后各单元的**先后**必须等于选中前的 z 先后（G 在 M2 之下 → 仍在先）。

    混选置顶的两条契约在这里合流：选中集占一段连续 z（既有断言），**且**单元
    之间的先后由文档决定。
    """
    lp, items, _g = _mixed_page()
    for n in ("M0", "M1", "M2"):
        lp._gi_for(items[n]).setSelected(True)
    before = _cut_order(lp.doc)
    assert before[-2:] == ["M0", "M2"] or before.index("M0") < before.index("M2"), \
        f"前置：G 应在 M2 之下，实得 {before}"

    _force_unit_order(monkeypatch, lp, _ORDERS[order])
    lp._zorder("top")

    z = _zs(lp.doc)
    # G（min z=1）在先、M2（z=3）在后 ⇒ G 拿到更低的 z，仍切在 M2 之前
    assert z["M0"] < z["M1"] < z["M2"], \
        f"置顶后组内/跨单元的先后被 {order} 打乱：{z}"
    assert z["X"] < z["M0"], "置顶后仍须整体高于未选中项"
    cut = _cut_order(lp.doc)
    assert cut.index("M0") < cut.index("M2"), \
        f"切割次序被 {order} 打乱（先切 M2 再切组）：{cut}"


def test_top_result_is_identical_whatever_qt_returns(monkeypatch):
    """**两种 Qt 返回序 ⇒ 逐位相同的 z 与切割次序**（把随机变确定的那一条）。"""
    results = {}
    for order in _ORDERS:
        lp, items, _g = _mixed_page()
        for n in ("M0", "M1", "M2"):
            lp._gi_for(items[n]).setSelected(True)
        _force_unit_order(monkeypatch, lp, _ORDERS[order])
        lp._zorder("top")
        results[order] = (_zs(lp.doc), _cut_order(lp.doc))
        monkeypatch.undo()

    a, b = results["组在前"], results["散件在前"]
    assert a == b, (
        f"同一场景两次启动发往机器的切割次序不同：\n"
        f"  组在前   → z={a[0]} cut={a[1]}\n"
        f"  散件在前 → z={b[0]} cut={b[1]}")


@pytest.mark.parametrize("order", sorted(_ORDERS))
def test_bottom_lays_units_out_in_pre_existing_z_order(order, monkeypatch):
    """置底对称：先到的单元拿更低的 z，相对层序与选中前一致。"""
    lp, items, _g = _mixed_page()
    for n in ("M0", "M1", "M2"):
        lp._gi_for(items[n]).setSelected(True)

    _force_unit_order(monkeypatch, lp, _ORDERS[order])
    lp._zorder("bottom")

    z = _zs(lp.doc)
    assert z["M0"] < z["M1"] < z["M2"], \
        f"置底后组内/跨单元先后被 {order} 打乱：{z}"
    assert z["M0"] < z["X"], "置底后仍须整体低于未选中项"
    assert _cut_order(lp.doc).index("M0") < _cut_order(lp.doc).index("M2")


# -- ② 既有契约不得被这次排序破坏 -------------------------------------------

def test_group_still_occupies_one_contiguous_block(monkeypatch):
    """排序只改**单元之间**的先后，组内仍是一段连续 z（FR-06 整组连续）。"""
    lp, items, _g = _mixed_page()
    for n in ("M0", "M1", "M2"):
        lp._gi_for(items[n]).setSelected(True)
    _force_unit_order(monkeypatch, lp, ["M2", "G"])
    lp._zorder("top")

    z = _zs(lp.doc)
    assert len({z["M0"], z["M1"]}) == 2, f"组内 z 交叠：{z}"
    assert z["X"] < z["M0"] < z["M1"] < z["M2"], f"选中集不是一段连续且高于未选中的 z：{z}"


def test_group_internal_spacing_preserved(monkeypatch):
    """组内**间距**不变（原来是 1，相对次序也要保持）。"""
    lp, items, _g = _mixed_page()
    for n in ("M0", "M1", "M2"):
        lp._gi_for(items[n]).setSelected(True)
    before = _zs(lp.doc)
    _force_unit_order(monkeypatch, lp, ["M2", "G"])
    lp._zorder("top")

    z = _zs(lp.doc)
    assert (z["M1"] - z["M0"]) == pytest.approx(before["M1"] - before["M0"]), \
        f"组内间距被改变：{before} → {z}"


def test_single_unit_selection_unchanged(monkeypatch):
    """只选一组（无混选）时，排序无对象，行为与从前一致。"""
    lp, items, g = _mixed_page()
    for n in ("M0", "M1"):
        lp._gi_for(items[n]).setSelected(True)
    lp._zorder("top")

    z = _zs(lp.doc)
    assert z["M0"] < z["M1"], f"组内先后颠倒：{z}"
    assert z["M1"] > z["X"], f"单组置顶应整体高于未选中的 X：{z}"
    assert z["M2"] == 3.0, f"未选中的 M2 不该被动：{z}"


def test_up_down_paths_untouched(monkeypatch):
    """上移/下移各按自身 z 平移 ±1，不受本次排序影响（游标为 None 分支）。"""
    lp, items, _g = _mixed_page()
    for n in ("M0", "M1", "M2"):
        lp._gi_for(items[n]).setSelected(True)
    before = _zs(lp.doc)

    _force_unit_order(monkeypatch, lp, ["M2", "G"])   # 故意给「反」的次序
    lp._zorder("up")

    z = _zs(lp.doc)
    assert z["M0"] == pytest.approx(before["M0"] + 1)
    assert z["M1"] == pytest.approx(before["M1"] + 1)
    assert z["M2"] == pytest.approx(before["M2"] + 1)
    assert z["X"] == before["X"], "上移不应动未选中项"
