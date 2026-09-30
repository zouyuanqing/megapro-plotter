"""R1–R4 回归：四条被对抗性复核推翻的修复，逐条钉住。

四条都在**纯 UI 路径**上可达、且都能让界面说出与机器要切的不一致的话：

**R1**（`canvas/handles.py` 的 ``pivot()`` 与 D1 的祖先变换不同框）
    D1 之后 ``sceneBoundingRect()`` 是页面系、``gi.pos()`` 仍是父系，而
    ``pivot()`` 取后者、被拿去和页面系的 ``scene_pos`` 一起算 ``r0/r1``
    与旋转角。实测组 pos=(100,30) 时支点误差 **104.40mm**、抓可见角点拖到
    两倍得 ``k=1.1497`` 而非 2.0；配套的 ``PathItem.page_to_parent`` 用
    ``mapFromParent`` 实现（把自身 R·S 与 pos 一起逆了），round-trip 六种
    场景全部返回 ``(0,0)`` —— 而钉它的三个 fixture 的 pos 恰好全为 (0,0)，
    断言空洞（pos=(0,0) 时怎么实现都过）。
    **已修**（handles + items 双侧）：单选支点走 ``page_origin()``、多选
    页面系结果经 ``page_to_parent``（真 A⁻¹，解析求逆）换回父系再写 pos。
    三个 fixture 的 pos 已改非零、祖先非恒等，round-trip 断言不再空洞。

**R2**（``_layout_out_of_bed`` 只在静默同步那条路上被填）
    那条路被「同步作业预览」复选框把守 ⇒ 用户**关掉**它（真控件、tooltip
    明写受支持）时整场会话停在 ``None`` ⇒ 作业页对「版面越界」零提示，
    而 anchor 已把落点搬走。修法：判据在**显示时现取**。

**R3**（``job_info`` 标签不换行，越界告知的可操作后半截被硬裁）
    D2-② 的告知只经这一个 QLabel 出去，1000px 窗口下整串需要 1188px、
    标签只分到 420px ⇒ 99 字里只有 35 字落像素，被切掉的恰是「超出多少」
    与「已挪到哪、照此执行」。

**R4**（``_align`` / ``_distribute`` 无条件 append 恒等命令）
    「本来就对齐」时几何零变化却仍 push（undoText「对齐」）⇒ 下一次
    Ctrl+Z 被它吃掉；本批新增的 ``_show_ok`` 还会把空转**播报成成功**。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets                        # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

_app = QApplication.instance() or QApplication([])

from megapro.gui.job import JobSpec, Placement               # noqa: E402
from megapro.gui.layout.layout_page import LayoutPage         # noqa: E402
from megapro.gui.layout.model import Item, iter_ancestors, unit_paths  # noqa: E402
from megapro.gui.main_window import MainWindow               # noqa: E402


# -- 工具 ------------------------------------------------------------------

def _rect(name, x, y, w, h, z=0.0, pos=(0.0, 0.0), **kw):
    return Item(paths=[[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]],
                pos=pos, name=name, z=z, **kw)


def _page(*items):
    lp = LayoutPage()
    lp.snap_enabled = False
    lp._add_items(list(items))
    lp._rebuild_scene()
    lp._after_change()
    return lp


def _select_all(lp):
    for gi in lp._scene_items:
        gi.setSelected(True)
    lp._on_selection_changed()


def _zmap(lp):
    return {it.name: it.z for it in
            __import__("megapro.gui.layout.model", fromlist=["iter_leaves"]).iter_leaves(lp.doc.items)}


class _Sig:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _StubWorker:
    def __init__(self):
        self._state = "READY"
        self.reqRunJob = _Sig()
        self.reqSendSequence = _Sig()
        self.reqSendLine = _Sig()

    def setPenState(self, *_a, **_k):
        pass


@pytest.fixture
def job_window(monkeypatch):
    """真 MainWindow（作业页已握一份排版作业），ZMessageBox 打桩防挂死。"""
    real_exec = QtWidgets.QMessageBox.exec
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self, *a, **k: 0)
    w = MainWindow()
    w._pen_down_z, w._cut_touch_z, w._safe_z = 17.0, 17.5, 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True
    w._refresh_gate_ui()
    w.layout_page._sync_job_cb.setChecked(False)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", real_exec)
    yield w
    w.close()


def _export_block(w):
    """在作业页放一个床内方块并「送去作业」（建立 _job_from_layout）。"""
    lp = w.layout_page
    lp._add_items([_rect("BLK", 0.0, 0.0, 20.0, 20.0, 1.0)])
    lp._rebuild_scene()
    lp._after_change()
    w._on_layout_export(lp.to_job_spec())
    return lp.doc.items[0]


def _move_out_of_bed(lp, blk, pos):
    """真 UI 路径改 pos（走撤销 push ⇒ indexChanged ⇒ 静默同步），不直接改字段。"""
    from megapro.gui.canvas.undo_cmds import MoveItemsCommand

    lp._undo.push(MoveItemsCommand(lp, [(blk, blk.pos, pos)]))
    lp._after_change()


# ============================================================================
# R1：handles 支点与 page_to_parent 的坐标系（已修，转正）
# ============================================================================

def _grouped_page():
    """组 pos=(100,30)（**非恒等平移祖先**），叶子 pos 非零。

    ⚠ 历史教训（F1）：本 fixture 旧版叶子 pos 恰好全为 (0,0) ⇒
    「``page_to_parent(page_origin()) == pos``」恒真、round-trip 断言空洞。
    现叶子 pos=(12,34)、sib pos=(5,5) —— pos=(0,0) 下巧合相等的实现
    （``mapFromParent``）在这里会给出 (0,0) 而非 (12,34)。
    组编辑态下单选叶子 ⇒ ``pivot()`` 走单选分支。
    """
    lp = _page(_rect("leaf", 0.0, 0.0, 10.0, 20.0, 1.0, pos=(12.0, 34.0)),
               _rect("sib", 0.0, 60.0, 10.0, 20.0, 2.0, pos=(5.0, 5.0)))
    g = lp.doc.group_items([lp.doc.items[0], lp.doc.items[1]])
    g.pos = (100.0, 30.0)
    lp._rebuild_scene()
    lp._after_change()
    leaf = lp.doc.items[0].children[0]
    lp._enter_group_edit(leaf)
    gi = lp._gi_for(leaf)
    gi.setSelected(True)
    lp._on_selection_changed()
    return lp, leaf, gi


def test_r1_handles_pivot_is_in_page_space():
    """单选支点必须落在**画出来的**本地原点上（页面系），不是 ``pos()``。

    错误实现下失败值：pivot = gi.pos() = (12,34)，画出来的原点 =
    A((12,34)) = (112,64)（组平移 (100,30)）—— 误差向量 (100,30)。
    """
    lp, _leaf, gi = _grouped_page()
    drawn_origin = gi.mapToParent(QtCore.QPointF(0.0, 0.0))
    pivot = lp._handles.pivot()
    assert abs(pivot.x() - drawn_origin.x()) < 1e-6 and \
        abs(pivot.y() - drawn_origin.y()) < 1e-6, (
            f"支点 {pivot.x():.2f},{pivot.y():.2f} 与画出来的原点 "
            f"{drawn_origin.x():.2f},{drawn_origin.y():.2f} 脱节 —— "
            f"缩放/旋转的 r0、角度全错")


def test_r1_drag_visible_corner_to_double_yields_k_two():
    """抓**可见角点**拖到两倍大小 ⇒ k 必须是 2.0，角点落在用户放的位置。

    手势按**用户意图**构造：抓住画出来的右下角（页面系 (122,84)），拖到
    「可见支点（画出来的本地原点 (112,64)）两倍距离」处 (132,104)。
    错误实现下失败值：支点停在父系 pos=(12,34) ⇒ r0=|(110,50)|、
    r1=|(120,70)| ⇒ k=1.1497（≠2.0），切割角点落在 (123.5,87.0)
    而非 (132,104) —— **机器按 1.15 倍切**。
    """
    import math

    lp, leaf, gi = _grouped_page()
    g = lp.doc.items[0]
    lp._handles.sync([gi])
    r = gi.sceneBoundingRect()
    near = (r.right(), r.bottom())                    # 页面系 (122,84)
    drawn = gi.page_origin()                          # 页面系 (112,64)
    far = (drawn.x() + 2 * (near[0] - drawn.x()),
           drawn.y() + 2 * (near[1] - drawn.y()))     # (132,104)
    pivot = lp._handles.pivot()
    r0 = math.hypot(near[0] - pivot.x(), near[1] - pivot.y())
    r1 = math.hypot(far[0] - pivot.x(), far[1] - pivot.y())
    k = r1 / r0
    assert abs(k - 2.0) < 1e-6, f"拖到两倍应得 k=2.0，实得 {k}"

    # 真 UI 手势走一遍（begin/update/end），模型必须兑现 k=2 且 pos 不被写歪
    h = lp._handles
    h.begin(h._handles[2], QtCore.QPointF(*near))
    h.update_drag(h._handles[2], QtCore.QPointF(*far))
    h.end(h._handles[2], QtCore.QPointF(*far))
    assert abs(leaf.scale - 2.0) < 1e-6, \
        f"model scale 应为 2.0，实得 {leaf.scale}（修复前 1.1497 —— 机器按 1.15 倍切）"
    assert abs(leaf.pos[0] - 12.0) < 1e-6 and abs(leaf.pos[1] - 34.0) < 1e-6, \
        f"单选缩放不该动 pos（父系字段），实得 {leaf.pos}"
    # 切割侧回读（flatten 同一条 unit_paths 合成链）：可见角点必须落用户放的位置
    from megapro.gui.layout.model import unit_paths as _up
    pts = [p for poly in _up(leaf, (g,)) for p in poly]
    cx, cy = max(p[0] for p in pts), max(p[1] for p in pts)
    assert abs(cx - far[0]) < 1e-6 and abs(cy - far[1]) < 1e-6, (
        f"切割角点 ({cx:.2f},{cy:.2f}) ≠ 用户放的 {far} "
        f"（错误实现给 (123.50,86.99)）")


def test_r1_multi_rotate_does_not_mix_frames():
    """多选旋转：写回的父系 pos 不得混进页面系支点（闭式泄漏必须为 0）。

    祖先平移 t=(30,20)，把手势转 +90°。**错误实现**把父系 pos 直接绕页面系
    支点转 ⇒ 相对正确值泄漏恰好 ``t − R(t)``（对抗复核闭式）：
    delta=+90° 时 = ``(t.x+t.y, t.y−t.x)`` = **(50,−10)**。
    期望值在测试内用裸数学独立算出（纯平移祖先下 A⁻¹(p)=p−t），
    不经 ``page_to_parent`` —— 不拿实现验证实现。

    ⚠ 断言改写说明：本用例旧版（xfail 期）断的是「多选支点 == 叶子 a 的
    页面原点」—— 与 handles.py 契约「多选支点 = 选择集并集中心」直接矛盾
    （本 fixture 两叶并集中心 (42.5,40) ≠ 叶子 a 原点 (35,20)，修好了也不
    成立）。多选支点的坐标系从来是对的（``selection_rect()`` 本就页面系），
    真正混系的是**写回** —— 故转正时换成上面这条闭式断言。
    """
    import math

    lp = _page(_rect("a", 0.0, 0.0, 20.0, 10.0, 1.0, pos=(5.0, 0.0)),
               _rect("b", 0.0, 30.0, 20.0, 10.0, 2.0, pos=(0.0, 15.0)))
    g = lp.doc.group_items([lp.doc.items[0], lp.doc.items[1]])
    g.pos = (30.0, 20.0)
    lp._rebuild_scene()
    lp._after_change()
    a, b = lp.doc.items[0].children
    lp._enter_group_edit(a)
    gis = [lp._gi_for(a), lp._gi_for(b)]
    for x in gis:
        x.setSelected(True)
    lp._on_selection_changed()
    assert a.pos != (0.0, 0.0) and b.pos != (0.0, 0.0), "空洞守卫：pos 必须非零"

    t = (30.0, 20.0)
    pivot = lp._handles.pivot()
    old_pos = {id(gi.model_item): gi.model_item.pos for gi in gis}
    rot = next(h for h in lp._handles._handles if h.kind == "rotate")
    # 把手转到 +90°：支点正右 → 支点正上
    start = QtCore.QPointF(pivot.x() + 50.0, pivot.y())
    end = QtCore.QPointF(pivot.x(), pivot.y() + 50.0)
    lp._handles.begin(rot, start)
    lp._handles.update_drag(rot, end)
    lp._handles.end(rot, end)

    rad = math.radians(90.0)
    c, s = math.cos(rad), math.sin(rad)
    for it in (a, b):
        op = old_pos[id(it)]
        # 正确值：页面系原点 op+t 绕支点转 90°，再换回父系（= 减 t）
        want = (pivot.x() + (op[0] + t[0] - pivot.x()) * c
                - (op[1] + t[1] - pivot.y()) * s - t[0],
                pivot.y() + (op[0] + t[0] - pivot.x()) * s
                + (op[1] + t[1] - pivot.y()) * c - t[1])
        leak = (it.pos[0] - want[0], it.pos[1] - want[1])
        assert abs(leak[0]) < 1e-6 and abs(leak[1]) < 1e-6, (
            f"{it.name}: pos={it.pos} 应为 {want}，泄漏 {leak} —— "
            f"错误实现恰好泄漏 (t.x+t.y, t.y−t.x) = (50,−10)")


# -- round-trip 矩阵：四种祖先 × 叶子自带变换（pos 全非零）------------------

_LEAF_POS = (12.0, 34.0)
_LEAF_OWN = dict(scale=1.5, angle_deg=30.0)

#: 祖先形态 → 容器 mutator（None = 顶层空链）。六种场景同一叶子
#: （pos=_LEAF_POS 非零、自带 scale=1.5/angle=30）。
_ANCESTORS = {
    "空链(顶层)": None,
    "平移(100,30)": lambda g: setattr(g, "pos", (100.0, 30.0)),
    "缩放2": lambda g: setattr(g, "scale", 2.0),
    "旋转90": lambda g: setattr(g, "angle_deg", 90.0),
    "镜像x": lambda g: setattr(g, "mirror_x", True),
    "组合平移+旋转+缩放": lambda g: (setattr(g, "pos", (100.0, 30.0)),
                                     setattr(g, "angle_deg", 90.0),
                                     setattr(g, "scale", 2.0)),
}


def _leaf_with_ancestor(mutator):
    leaf = _rect("leaf", 0.0, 0.0, 10.0, 20.0, 1.0, pos=_LEAF_POS, **_LEAF_OWN)
    sib = _rect("sib", 0.0, 60.0, 10.0, 20.0, 2.0, pos=(5.0, 5.0))
    lp = _page(leaf, sib)
    if mutator is not None:
        g = lp.doc.group_items([lp.doc.items[0], lp.doc.items[1]])
        mutator(g)
        lp._rebuild_scene()
        lp._after_change()
        leaf = lp.doc.items[0].children[0]
        lp._enter_group_edit(leaf)
    return lp, leaf, lp._gi_for(leaf)


@pytest.mark.parametrize("label", list(_ANCESTORS))
def test_r1_page_to_parent_round_trip_six_ancestor_kinds(label):
    """round-trip：``page_to_parent(page_origin()) == pos``，六种祖先形态。

    **空洞教训（F1）**：旧断言三个 fixture 的 pos 恰好全为 (0,0)，
    ``mapFromParent`` 实现下 ``page_to_parent(page_origin()) = (R·S)⁻¹·0
    = (0,0) = pos`` 恒真 —— 怎么实现都过。现 pos=(12,34) 非零、叶子自带
    scale=1.5/angle=30：错误实现在**全部六种**场景（含空链！）返回
    ``(0,0)``，断言以「期望 12/34、实得 0」具体失败 —— 实测见本轮探针。
    """
    lp, leaf, gi = _leaf_with_ancestor(_ANCESTORS[label])
    assert leaf.pos != (0.0, 0.0), "空洞守卫：round-trip 断言要求 pos 非零"
    # page_origin 仍是「本地图元本地原点的页面坐标」（D1 使能半边，不回归）
    drawn = gi.mapToParent(QtCore.QPointF(0.0, 0.0))
    assert abs(gi.page_origin().x() - drawn.x()) < 1e-9 and \
        abs(gi.page_origin().y() - drawn.y()) < 1e-9
    back = gi.page_to_parent(gi.page_origin())
    assert abs(back.x() - leaf.pos[0]) < 1e-6 and \
        abs(back.y() - leaf.pos[1]) < 1e-6, (
            f"[{label}] round-trip 应还原 pos={leaf.pos}，实得 "
            f"({back.x():.4f},{back.y():.4f}) —— mapFromParent 实现恒给 (0,0)")
    if _ANCESTORS[label] is None:
        # 空祖先链必须**恒等**（不只是 round-trip）：任意页面点原样返回
        q = QtCore.QPointF(37.5, -21.25)
        assert gi.page_to_parent(q) == q, \
            f"空链应恒等，实得 {gi.page_to_parent(q)}"


# ============================================================================
# R2：同步开关关掉时，作业页仍须拿到版面越床判据
# ============================================================================

def test_r2_notice_present_with_sync_switch_off(job_window):
    """复选框**关掉**时，anchor 洗白仍必须在作业页被说出来。

    修复前实测：``_layout_out_of_bed`` 停在 ``None``、job_info 无
    「排版版面越界」、``runnable=True violations=()``，机器切 (0,0)-(20,20)
    而用户排的是 (-40,10) —— **零提示**。
    """
    w = job_window
    lp = w.layout_page
    assert not lp._sync_job_cb.isChecked(), "前置：本用例专测开关**关掉**"

    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 10.0))

    assert lp._out_of_bed() is not None, "前置：排版页应报越界"

    w._job_spec = JobSpec(
        paths_paper=list(w._job_spec.paths_paper),
        source_name=w._job_spec.source_name,
        placement=Placement(mode="anchor", anchor="bl", target=(0.0, 0.0)))
    w._set_placement_ui("anchor")
    w._recompile()

    assert "排版版面越界" in w.job_info.text(), (
        f"开关关掉时作业页对「版面越界」零提示：{w.job_info.text()!r} —— "
        f"排版页明明报了 {lp._out_of_bed()[0]:.0f}mm")
    assert "按当前放置执行" in w.job_info.text(), \
        f"没告知「照此放置执行」：{w.job_info.text()!r}"


def test_r2_notice_is_not_stale_after_later_edit(job_window):
    """更糟的变体：判据**陈旧**比没有更误导（自信地报错数字）。

    修复前实测：开关先开（判据取到 40.0mm）→ 关掉 → 再拖到 -100mm，
    作业页仍报「最大超出 40.0mm」（少报 60mm）。
    """
    w = job_window
    lp = w.layout_page
    lp._sync_job_cb.setChecked(True)          # 先开，取一次判据
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    assert "40.0mm" in w.job_info.text(), "前置：此时应报 40.0mm"

    lp._sync_job_cb.setChecked(False)         # 关掉，后续编辑不再同步
    _move_out_of_bed(lp, blk, (-100.0, 0.0))
    w._recompile()
    truth = lp._out_of_bed()
    assert truth is not None and abs(truth[0] - 100.0) < 1e-6, \
        f"前置：真实越界量应为 100.0mm，实得 {truth}"
    assert "100.0mm" in w.job_info.text(), (
        f"作业页仍在报陈旧的 40.0mm：{w.job_info.text()!r} —— "
        f"真实越界量已是 {truth[0]:.0f}mm")


def test_r2_export_path_also_populates_verdict(job_window):
    """「送去作业」这条排版来源**也要**填判据（不只静默同步那条路）。"""
    w = job_window
    lp = w.layout_page
    lp._sync_job_cb.setChecked(False)
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    assert "排版版面越界" in w.job_info.text(), (
        f"export 路径没填判据：{w.job_info.text()!r}")


# ============================================================================
# R3：越界告知的可操作后半截不得被硬裁
# ============================================================================

def test_r3_job_info_wraps_the_notice(job_window):
    """告知串必须**能完整显示**（折行 + tooltip 兜底）。

    修复前实测：wordWrap=False、整串要 1188px 而标签只分到 420px ⇒ 99 字里
    只有 35 字落像素，「最大超出 40.0mm」「已移到」「按当前放置执行」全部被切。
    """
    w = job_window
    lp = w.layout_page
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    lbl = w.job_info
    txt = lbl.text()
    assert "排版版面越界" in txt, f"前置：本例应产生越界告知：{txt!r}"
    assert lbl.wordWrap() is True, "job_info 未开 wordWrap —— 长告知会被硬裁"
    # tooltip 兜底整串：任何窗口宽度下都能读到可操作那半句
    assert lbl.toolTip() == txt, "job_info 的 tooltip 未兜住整串告知"


def test_r3_notice_fits_after_wrap(job_window):
    """折行后控件**实际高度**必须够放下整串（渲染事实，不是字符数估算）。

    ⚠ 判据用 ``height()`` 与 ``heightForWidth()`` 的比较，**不用**「可见字数」
    估算：字符数与像素列不是线性关系，那样的估算在修复前也会给出误导性的
    「看起来没被裁」。这里比的是「控件实际拿到的�� vs 放满整串所需的高」。

    修复前实测：wordWrap=False 时整串要 1188px、标签只分到 420px ⇒ 硬裁，
    「最大超出 40.0mm」「已移到」「按当前放置执行」三样全不落像素。
    """
    w = job_window
    lp = w.layout_page
    blk = _export_block(w)
    _move_out_of_bed(lp, blk, (-40.0, 0.0))
    w._recompile()
    lbl = w.job_info
    assert lbl.wordWrap() is True, "前置：job_info 必须开 wordWrap"

    real_exec = QtWidgets.QMessageBox.exec
    try:
        QtWidgets.QMessageBox.exec = lambda self, *a, **k: 0
        w.resize(800, 800)
        w.show()
        QApplication.processEvents()
        QApplication.processEvents()
        for width in (200, 348, 640):
            lbl.setFixedWidth(width)
            QApplication.processEvents()
            QApplication.processEvents()
            need = lbl.heightForWidth(width)
            assert lbl.height() >= need, (
                f"宽 {width}px 时控件高 {lbl.height()} < 放满整串所需 {need}"
                f" —— 告知仍被裁掉（尾句是「已挪到哪、照此执行」那条可操作事实）")
    finally:
        QtWidgets.QMessageBox.exec = real_exec
        w.hide()


# ============================================================================
# R4：_align / _distribute 空转不得推恒等命令、不得播报假成功
# ============================================================================

def test_r4_align_noop_pushes_nothing_and_tells_truth():
    """本来就左对齐 ⇒ 几何零变化、**不推命令**、提示行说实话。"""
    lp = _page(_rect("a", 0.0, 0.0, 10.0, 10.0, 1.0),
               _rect("b", 0.0, 60.0, 5.0, 5.0, 2.0))
    _select_all(lp)
    from megapro.gui.layout.model import iter_leaves

    before = {it.name: it.pos for it in iter_leaves(lp.doc.items)}
    idx0 = lp._undo.index()

    lp._align("left")

    after = {it.name: it.pos for it in iter_leaves(lp.doc.items)}
    assert before == after, "左对齐不该动几何"
    assert lp._undo.index() == idx0, (
        f"空转仍推了一条命令（index {idx0}->{lp._undo.index()}、"
        f"undoText={lp._undo.undoText()!r}）—— 它会吃掉用户下一次 Ctrl+Z")
    msg = lp.status_label.text()
    assert "已对齐" not in msg, f"空转被播报成成功：{msg!r}"


def test_r4_noop_align_does_not_swallow_next_undo():
    """恒等命令吃 Ctrl+Z 的后果：置顶后空转左对齐，一次撤销必须撤掉置顶。

    修复前实测：一次 Ctrl+Z 后 z 纹丝不动（被空转命令吃掉），需两次。
    """
    lp = _page(_rect("a", 0.0, 0.0, 10.0, 10.0, 1.0),
               _rect("b", 0.0, 60.0, 5.0, 5.0, 2.0))
    _select_all(lp)
    lp._zorder("top")
    z_top = _zmap(lp)
    lp._align("left")            # 空转
    lp._undo.undo()              # 这一次必须撤掉置顶
    assert _zmap(lp) != z_top, "一次 Ctrl+Z 没有撤掉置顶（被空转命令吃掉）"


def test_r4_distribute_degenerate_pushes_nothing():
    """退化分布（三个零宽图元 ⇒ gaps=0）⇒ 几何零变化、不推命令、不报成功。"""
    lp = _page(_rect("d0", 0.0, 0.0, 0.0, 0.0, 0.0),
               _rect("d1", 0.0, 0.0, 0.0, 0.0, 1.0),
               _rect("d2", 0.0, 0.0, 0.0, 0.0, 2.0))
    _select_all(lp)
    from megapro.gui.layout.model import iter_leaves

    before = {it.name: it.pos for it in iter_leaves(lp.doc.items)}
    idx0 = lp._undo.index()

    lp._distribute("h")

    assert {it.name: it.pos for it in iter_leaves(lp.doc.items)} == before, \
        "退化分布不该动几何"
    assert lp._undo.index() == idx0, "退化分布仍推了恒等命令"
    assert "已分布" not in lp.status_label.text(), \
        f"退化分布被播报成成功：{lp.status_label.text()!r}"


def test_r4_real_align_still_works_and_reports_success():
    """**不回归**：真会动的对齐照旧推命令并播报成功。"""
    lp = _page(_rect("a", 0.0, 0.0, 10.0, 10.0, 1.0),
               _rect("b", 50.0, 60.0, 5.0, 5.0, 2.0))
    _select_all(lp)
    idx0 = lp._undo.index()

    lp._align("left")

    assert lp._undo.index() == idx0 + 1, "真移动必须推命令"
    assert "已对齐" in lp.status_label.text(), \
        f"真移动应播报成功：{lp.status_label.text()!r}"
    # 判据用**页面系** bbox（pos 是父系局部量，对齐改的是它，局部坐标不可比）
    xs = sorted(min(unit_paths(leaf, chain)[0][0]
                    for leaf, chain in iter_ancestors(lp.doc.items)
                    if leaf.name in ("a", "b")))
    assert abs(xs[0] - xs[1]) < 1e-6, f"左对齐后两个图元的页面 min-x 应相等，实得 {xs}"
