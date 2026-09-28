"""排版/制作页（Qt）—— CAD/PS 式编辑 + 工具 + 属性 + 导入/导出。

能力：
- 添加：文字（字体可选、可双击重编）、图片（中心线/轮廓）、SVG、Word/Excel、绘制图元
- 编辑：框选/Ctrl多选/Ctrl+A、删除、撤销/重做、复制粘贴、箭头微调、
        层序（置顶/置底/上移/下移）、对齐/分布、W/H 数值
- 画布：mm 网格、缩放控件、标尺、网格/对象吸附、旋转缩放手柄
- 导出：另存为 SVG / 送去执行（按 z 排序、跳过隐藏）

坐标契约（阶段 2 y-up 统一，docs/preview-layout-blueprint.md §2.1/§2.2）：
- 模型 Item 存**纸面 mm y-up**（原点左下 0..210）；**场景 ≡ 纸面**（同值），
  paint 直画无翻转。y 翻转只存在于 ``canvas/view_transform.py``（视图层
  唯一负比例尺）与 SVG 互换层（``coords.paper_from_svg_ydown`` 等）。
- 鼠标坐标一律 ``mm_from_view`` 浮点（PaperView 已保证）。
- 拖动结束经 :class:`~megapro.gui.canvas.undo_cmds.MoveItemsCommand`（手势
  token）回写 model 并进撤销栈；数值定位走九宫格锚点（默认 bl），
  多选按选择集 bbox 锚点整体平移/等比缩放（不塌缩）。
- 组导入契约 :func:`_import_group`：整组 ``paper_from_svg_ydown`` →
  **组级一次** ``place_at_anchor`` → 逐 Item ``normalize_local``；严禁逐
  Item 归位（Word/Excel 相对布局必须保持）。
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.coords import (
    BED_H,
    BED_W,
    anchor_point,
    machine_from_paper,
    paper_from_svg_ydown,
    place_at_anchor,
)
from megapro.gui.canvas.group_overlay import GroupOverlay
from megapro.gui.canvas.handles import SelectionHandles
from megapro.gui.canvas.items import PathItem
from megapro.gui.canvas.paper_scene import PaperScene
from megapro.gui.canvas.paper_view import PaperView
from megapro.gui.canvas.rulers import RulerWidget
from megapro.gui.canvas.snap import SnapEngine, snap_candidate_paths
from megapro.gui.canvas.undo_cmds import (
    AddItemsCommand,
    ChangeItemPropsCommand,
    ClearCommand,
    EditTextCommand,
    GroupCommand,
    MoveItemsCommand,
    RemoveItemsCommand,
    RetraceImageCommand,
    UngroupCommand,
    make_gi,
    new_gesture_token,
)
from megapro.gui.layout.export_svg import document_to_svg
from megapro.gui.layout.model import (
    DetachInfo, Document, Item, effectively_visible, iter_leaves, iter_units,
    normalize_local,
)

from megapro.gui.text_to_svg import (
    find_cjk_font, list_fonts, text_outline_svg, text_singleline_svg,
)

_DATA_DIR = Path(__file__).resolve().parents[4] / "data"

#: 绘制工具
TOOL_SELECT = "select"
TOOL_LINE = "line"
TOOL_RECT = "rect"
TOOL_CIRCLE = "circle"
TOOL_POLY = "poly"
TOOL_PENCIL = "pencil"


def _svg_to_paths(svg: str) -> list:
    """把 SVG 字符串解析为 polylines（经临时文件走 toolchain parse_svg）。

    名称保留（doc_import / tests/test_gui_edge.py / test_gui_layout_window.py
    依赖，§2.2 评审 missing #6）。输出为 SVG y-down 数值 —— 入库前须走
    组导入契约（:func:`_import_group` / :func:`_group_paths_to_paper`）。
    """
    from megapro.toolchain.svg_to_gcode import parse_svg

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False,
                                     mode="w", encoding="utf-8") as fh:
        fh.write(svg)
        p = fh.name
    try:
        return parse_svg(p)
    finally:
        Path(p).unlink(missing_ok=True)


def _group_paths_to_paper(
    paths_svg_list, *, anchor: str = "bl", target: tuple[float, float] = (0.0, 0.0),
) -> list:
    """组导入契约几何段（§2.2）：①整组 paper_from_svg_ydown ②组级一次 place_at_anchor。

    ①SVG y-down → 纸面 y-up（格式编解码，保相对布局）；
    ②对**整组拼接 bbox** 一次归位（严禁逐 Item 归位 —— 段落/表格会塌到原点）。
    返回与输入等长的逐图元纸面 y-up 折线组。
    """
    flipped = [paper_from_svg_ydown(pg) for pg in paths_svg_list]
    flat = [p for pg in flipped for p in pg]
    placed = place_at_anchor(flat, anchor, target)
    out: list = []
    i = 0
    for pg in flipped:
        out.append(placed[i:i + len(pg)])
        i += len(pg)
    return out


def _import_group(
    paths_svg_list, *, name, anchor: str = "bl",
    target: tuple[float, float] = (0.0, 0.0),
) -> list[Item]:
    """完整组导入契约：①翻转 ②组级一次归位 ③逐 Item normalize_local。

    单图元 = 组长度 1。``name`` 为 str 时单图元原样用作名称、多图元加 ``:i``
    后缀；也可传与组等长的名称序列。③只做局部重锚（页面几何不变）。
    """
    groups = _group_paths_to_paper(paths_svg_list, anchor=anchor, target=target)
    if isinstance(name, str):
        names = [name if len(groups) == 1 else f"{name}:{i + 1}"
                 for i in range(len(groups))]
    else:
        names = list(name)
    items: list[Item] = []
    for i, paths in enumerate(groups):
        it = Item(
            paths=[[(float(x), float(y)) for x, y in p] for p in paths],
            pos=(0.0, 0.0),
            name=(names[i] if i < len(names) else f"item:{i + 1}"),
            z=float(i),
        )
        normalize_local(it)
        items.append(it)
    return items


def _image_paths_to_paper(paths_svg) -> list:
    """图片线条的**入库几何** = 组导入契约 ①②③ 在「长度 1 组」上的等价物。

    导入（:meth:`LayoutPage._add_image` → :func:`_import_group`）与就地重追
    (:meth:`LayoutPage.retrace_image_item`) **必须走同一个函数**。理由：重追
    只换 ``paths``、不碰 pos/scale/angle（FR-10 验收③），所以它写进去的
    ``paths`` 必须与当初**导入时逐点同一坐标系**；少走 ①②③（y 翻转 + 锚点
    归位）的话，新折线停在 SVG y-down 原坐标系，同参数重追也会整体上下翻转
    + 平移（会切错位置）。本函数直接复用 :func:`_import_group` 而非重写一遍
    几何，两条链因此**不可能各自漂移**。
    """
    return _import_group([paths_svg], name="image")[0].paths


def _pos_for_anchor(
    item: Item, target: tuple[float, float], anchor: str = "bl",
) -> tuple[float, float]:
    """九宫格锚点绝对定位：``pos = target − R(θ)S(s)·anchor_offset``（§2.2）。"""
    ax, ay = anchor_point(item.bbox(), anchor)
    s = item.scale
    rad = math.radians(item.angle_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    ox, oy = ax * s, ay * s
    return (target[0] - (ox * cos - oy * sin), target[1] - (ox * sin + oy * cos))


def _union_bbox(boxes) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


# --- 剪贴板序列化（FR-06 递归化） ------------------------------------------
#
# 契约：容器**连子树**一起序列化/克隆（``children`` 键），叶子格式与既有平面
# JSON **逐字段兼容**（``paths``/``pos``/``scale``/``angle_deg``/``name``/``z``/
# ``text_spec``）—— 旧剪贴板内容仍可粘贴。``visible``/``locked`` 一并带走。
# ``mirror_x``/``mirror_y`` **同样必须带走**：镜像是读取时施加的标志（不在
# ``paths`` 里），漏了它就不是「少个字段」而是**副本几何整个反了**（镜像件
# 被还原成未镜像件，错误坐标直达机器，全程不抛错）。
# 组内相对布局在粘贴后保持（各子项 pos 相对不变，只对**容器根**加偏移），
# 故偏移只施加在根，不逐层累加（逐层会双重偏移）。


def _item_to_json(item: Item) -> dict:
    d = {"paths": item.paths, "pos": item.pos, "scale": item.scale,
         "angle_deg": item.angle_deg, "name": item.name, "z": item.z,
         "text_spec": item.text_spec, "image_spec": item.image_spec,
         "visible": item.visible, "locked": item.locked,
         "mirror_x": bool(item.mirror_x), "mirror_y": bool(item.mirror_y)}
    if item.children:
        d["children"] = [_item_to_json(ch) for ch in item.children]
    return d


def _item_from_json(d: dict, *, dz: float, z: float) -> Item:
    """剪贴板 JSON → Item（递归）。``dz`` 偏移与 ``z`` **只施加在根上**。

    根的 z 强制取 ``z``（副本/粘贴须落在新最上层 —— 与既有平面 JSON 的
    ``z=top_z+1`` 行为逐位一致）；子项各自保留**序列化的原 z**（组内相对
    层序保持）。偏移只加根不逐层累加（逐层会双重偏移）。
    ``mirror_*`` 缺键（旧剪贴板内容）默认 False —— 旧内容本就无镜像概念。
    """
    pos = d.get("pos", (0.0, 0.0))
    kids = [_item_from_json(c, dz=0.0, z=float(c.get("z", 0.0)))
            for c in d.get("children", [])]
    return Item(
        paths=[[(float(x), float(y)) for x, y in p]
               for p in d.get("paths", [])],
        pos=(float(pos[0]) + dz, float(pos[1]) + dz),
        scale=float(d.get("scale", 1.0)),
        angle_deg=float(d.get("angle_deg", 0.0)),
        name=d.get("name", "item"),
        z=float(z),
        visible=bool(d.get("visible", True)),
        locked=bool(d.get("locked", False)),
        mirror_x=bool(d.get("mirror_x", False)),
        mirror_y=bool(d.get("mirror_y", False)),
        text_spec=copy.deepcopy(d.get("text_spec")),
        image_spec=copy.deepcopy(d.get("image_spec")),
        children=kids,
    )


def _clone_item(item: Item, *, dz: float, z: float) -> Item:
    """:func:`_item_from_json` 的对象版（副本深拷贝，偏移只加在根）。"""
    return _item_from_json(_item_to_json(item), dz=dz, z=z)


def _restack_above(item: Item, top: float) -> float:
    """把 ``item`` 的**整棵子树**抬到 ``top`` 之上，返回抬完后的 top。

    「落在最上层」这条策略由**调用点**（:meth:`LayoutPage._paste` /
    :meth:`LayoutPage._duplicate`）施加，而不是让 :func:`_item_from_json` 去改
    子项的 z —— 那对会把它从「忠实的序列化/反序列化对」变成「顺带改层序的地方」
    （且子项 z 保留是既有冻结契约）。这里按**叶子**平移：

    - :func:`~megapro.gui.layout.model.flatten_visible` 只按**叶子** z 排序，
      **容器 z 不参与** ⇒ 只抬容器 z 等于没抬，副本会掉回原有图元之下（实测：
      容器 z=102 而成员 z=1/2，拍平序被压到 TOP 之下）。
    - 平移量 = ``top + 1 − 最低叶子 z``，故**最低成员**也严格高于 ``top``；
      组内相对次序（谁在上）**原样保持**（统一平移，不逐项重排）。
    - 散件（无 children）行为与旧实现逐位一致：它自己的叶子抬到 ``top + 1``。
    """
    leaves = list(iter_leaves([item]))
    if not leaves:
        return top
    delta = (top + 1.0) - min(leaf.z for leaf in leaves)
    if delta:
        for leaf in leaves:
            leaf.z = float(leaf.z) + delta
    return max(top, max(leaf.z for leaf in leaves))


class LayoutPage(QtWidgets.QWidget):
    """排版页：信号 export_requested(JobSpec) 供 MainWindow 送去作业。

    导出链（阶段 3）：:meth:`to_job_spec` = ``flatten_visible(doc)`` +
    ``Placement(mode='preserve')``（版面坐标即工件坐标，排版直传不归位）；
    「另存为 SVG」走同一 :func:`document_to_svg` 格式写盘。
    """

    export_requested = QtCore.Signal(object)  # JobSpec（纯逻辑对象）
    #: 排版页**静默**同步作业预览（JobSpec）——与 export_requested 的区别只在
    #: 消费侧：那个 handler 会 setCurrentIndex(0) 把用户踢回控制页并刷控制台，
    #: 页切换这种高频轻量动作不能走它（PRD §10.1-8 多页切换同步作业预览）。
    job_sync_requested = QtCore.Signal(object)
    status_message = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc = Document()
        self._scene_items: list[PathItem] = []
        self._applying = False
        self._tool = TOOL_SELECT
        self._preview_item = None
        self._draw_start = None
        self._poly_pts: list[QtCore.QPointF] = []
        self.snap_pitch = 1.0
        self.snap_enabled = True
        #: 数值定位九宫格锚点（§4.2 默认 bl）
        self._pos_anchor = "bl"
        #: 数值输入手势 token（同字段连续改值合并为一条撤销，评审 low #6）
        self._prop_token: int | None = None
        self._prop_sender = None
        self._undo = QtGui.QUndoStack(self)
        self._build_ui()
        self._refresh_page_bar()  # 建首个页签（默认单页）
        self._refresh_props()
        self._refresh_undo_actions()

    # -- 供 undo 命令调用 --------------------------------------------------

    def _gi_for(self, item: Item) -> PathItem | None:
        for gi in self._scene_items:
            if gi.model_item is item:
                return gi
        return None

    def _remove_item_obj(self, item: Item,
                         info: list | None = None) -> "DetachInfo | None":
        """摘除一个图元（顶层或组内叶子）及其场景项。

        **时序 = 先模型后场景**（M1 评审裁决）：``doc.remove`` 是组感知的、不会
        抛异常（抛异常会让 ``QUndoStack`` 半污染 —— 命令已入栈而模型未改，
        Ctrl+Z 后几何翻倍），故可安全先摘模型。``info`` 传入 list 时把归属回执
        追加进去（undo 复原组结构用，见 :class:`RemoveItemsCommand`）。
        """
        det = self.doc.remove(item)
        gi = self._gi_for(item)
        if gi is not None:
            self.scene.removeItem(gi)
            if gi in self._scene_items:
                self._scene_items.remove(gi)
        if info is not None:
            info.append(det)
        return det

    def _sync_gi(self, item: Item) -> None:
        """model → 场景（场景 ≡ 纸面 y-up，**无翻转**）—— 场景同步唯一入口。"""
        gi = self._gi_for(item)
        if gi is None:
            return
        gi.apply_model_state()
        gi.setZValue(item.z)
        gi.setVisible(effectively_visible(self.doc, item))
        gi.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, not item.locked)
        gi.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, not item.locked)
        gi.update()

    def _after_change(self) -> None:
        """任何编辑后：刷新属性/手柄/组框。"""
        self._refresh_props()
        self._handles.sync(self._selected())
        self._group_overlay.refresh(self._page_box)

    def _page_box(self, item: Item) -> tuple[float, float, float, float]:
        """model.Item → **页面系** bbox（含祖先链，M1 缺口 #1 的正解）。

        越界预检 / 组框 overlay / 对齐 / 分布 / 多选数值一律走本函数，不吃裸
        ``item.page_bbox()``（后者不走祖先链 → 组内叶子在非恒等容器下报局部坐标）。
        """
        for it, chain in iter_units([item], visible_only=False):
            if it is item:
                return item.unit_page_bbox(chain)
        return item.page_bbox()

    # -- 组：选择语义（FR-03 / FR-05） --------------------------------------

    def _owner_container(self, item: Item) -> Item | None:
        """图元所属的**最外层**容器（顶层图元返回 None）。

        祖先链必须**从文档根**查（``iter_ancestors(doc.items)``）—— 从
        ``[item]`` 查只会遍历 item 自己的子树、拿不到容器的上下文。
        """
        from megapro.gui.layout.model import iter_ancestors

        for it, anc in iter_ancestors(self.doc.items):
            if it is item:
                return anc[-1] if anc else None
        return None

    def _group_siblings(self, item: Item) -> list[Item]:
        """与 ``item`` 同属一个容器的兄弟（普通态点选时一并选中的集合）。

        顶层图元 = 自身（无组）。组编辑态下调用方须自行限定为叶子操作单元。
        """
        owner = self._owner_container(item)
        if owner is None:
            return [item]
        return list(owner.children)

    def _expand_group_selection(self) -> None:
        """普通态：选中集合里任一组成员 ⇒ 选中**整组**（FR-03 选择语义）。

        「点组内子项先选中整组」：对每个选中的图元取同容器的兄弟并全选。
        **组编辑态不做**（双击进组后操作单元 = 叶子，FR-05 选择态二分）。
        """
        if self._group_overlay.editing is not None:
            return
        sel = self._selected()
        if not sel:
            return
        wanted: list[Item] = []
        for gi in sel:
            it = gi.model_item
            wanted.extend(self._group_siblings(it))
        if not wanted:
            return
        ids = {id(it) for it in wanted}
        for gi in self._scene_items:
            want = id(gi.model_item) in ids
            if gi.isSelected() != want:
                gi.setSelected(want)

    def _enter_group_edit(self, item: Item) -> None:
        """双击组内子项 ⇒ 进组编辑态（FR-03「双击进组选子项」）。"""
        owner = self._owner_container(item)
        self._group_overlay.set_editing(owner)
        self._group_overlay.refresh(self._page_box)

    def on_item_double_clicked(self, item: Item) -> bool:
        """双击回调（FR-03「双击进组选子项」）：组内子项 ⇒ 进组编辑态。

        返回 True 表示已消费（不再走文字重编）。顶层图元/组编辑态内的子项
        返回 False，由 :class:`PathItem` 继续走文字重编等既有回调。
        """
        if self._group_overlay.editing is not None:
            return False
        owner = self._owner_container(item)
        if owner is None:
            return False
        self._enter_group_edit(item)
        return True

    def _exit_group_edit(self) -> None:
        self._group_overlay.set_editing(None)
        self._group_overlay.refresh(self._page_box)

    def _group_selected_items(self) -> list[Item]:
        """当前选择对应的**组选中 = 叶子集**（FR-05 v1.2 裁决）。

        容器不建 PathItem、也不在选择集里，故选择集天然就是叶子集；组级
        移动/缩放/旋转由此走既有多选机制（``handles.py`` 多选等比公式、
        :meth:`_apply_pos` 多选整体平移），**不新增容器变换渲染通路**。
        """
        return [gi.model_item for gi in self._selected()]

    def _selected_units(self) -> list[tuple[Item, Item | None]]:
        """**操作单元**（FR-05 v1.3 选择态二分），**按身份去重**。

        普通态：整组按**容器**（其 bbox 走 :meth:`_page_box`），组外散件按
        自身 —— 普通态混选只能是「整组 + 组外散件」。
        组编辑态：操作单元 = 叶子 ``(叶子, None)``。

        **去重是必需的，不是优化**（M2 评审阻塞项）：组选中 = 叶子集，故
        N 个成员的组会经 :meth:`_selected` 产出 **N 条**同容器的单元。若不去重：
        - ``_distribute`` 的除数 ``len(boxes) - 1`` 被重复项灌大 ⇒ gaps 偏小、
          每个重复项又按不同 target 各写一次绝对 pos（``ChangeItemPropsCommand``
          是 setattr 绝对赋值、最后一条生效）⇒ **整组被平移一个非零量**。
          实跑：A(y=0)/B(y=100) + 组 G{C(y=50),D(y=60)} 三单元全选，
          ``units=['B','G','G','A']`` ⇒ C 50→66.67、D 60→76.67，而正确分布
          应是**零位移**（lo=0、hi=100、gaps=50，target 0/50/100 与现 y0 吻合）。
        - 每个成员也会被写多次 pos（多余命令项）。
        对齐/分布的 min/max/union 不受重复项影响（min/max 幂等），故该缺陷
        **只**在 ``_distribute`` 显形 —— 但根因在此处统一修，各消费方同口径。

        返回 ``(item, None)`` 里的 None 占位是「叶子附带其容器引用」的扩展位；
        当前实现两者都为 None 以保持调用侧简洁。
        """
        editing = self._group_overlay.editing
        out: list[tuple[Item, Item | None]] = []
        seen: set[int] = set()
        for gi in self._selected():
            it = gi.model_item
            unit = it if editing is not None else (
                self._owner_container(it) or it)
            if id(unit) in seen:  # 同一容器只算一个操作单元
                continue
            seen.add(id(unit))
            out.append((unit, None))
        return out

    def _unit_bbox(self, unit: Item) -> tuple[float, float, float, float]:
        """操作单元的 bbox：组 = 容器页面系 bbox，散件/叶子 = 自身页面系 bbox。"""
        return self._page_box(unit)

    def group_selected(self) -> None:
        """编组（FR-03）。成员 = 当前选择的**组单元**去重后的图元。"""
        members = self._group_members_for_grouping()
        if len(members) < 2:
            self.status_message.emit("编组需选中至少两个图元")
            return
        cmd = GroupCommand(self, members, name=f"组{len(members)}")
        self._undo.push(cmd)
        if cmd.container is None:
            self.status_message.emit("无法编组：选择里包含组的祖先")
            return
        cont = cmd.container
        for it in members:
            gi = self._gi_for(it)
            if gi is not None:
                gi.setSelected(False)
        self._after_group_promote(cont)

    def _group_members_for_grouping(self) -> list[Item]:
        """编组成员 = 选择集里的图元本身（含整组时用整组的**全部叶子**）。

        组选中 = 叶子集（FR-05）：选中整组后编组，成员就是那些叶子（把组
        套进组才用容器本身）—— 故取选择集的并集（去重、按身份）。
        """
        return list(dict.fromkeys(self._group_selected_items()))

    def _after_group_promote(self, container: Item) -> None:
        """编组后：整组重新选中（组选中 = 叶子集）+ 刷新组框。"""
        for leaf in iter_leaves([container]):
            gi = self._gi_for(leaf)
            if gi is not None:
                gi.setSelected(True)
        self._after_change()

    def ungroup_selected(self) -> None:
        """解组（FR-03）：对当前选区里的**每个容器**执行一次解组。"""
        conts = []
        for it in self._group_selected_items():
            owner = self._owner_container(it)
            if owner is not None and owner not in conts:
                conts.append(owner)
        if not conts:
            self.status_message.emit("请选中组内图元后解组")
            return
        # 一个容器一条命令；逆序执行使下标回退互不干扰
        for cont in reversed(conts):
            self._undo.push(UngroupCommand(self, cont))
        for it in self._group_selected_items():
            gi = self._gi_for(it)
            if gi is not None:
                gi.setSelected(True)
        self._after_change()

    # 注：**拖组 = 一条可撤销命令**不需要本文件新增代码 —— 组选中 = 叶子集时，
    # Qt 同步移动全部选中项，``PathItem.commit_move``（``canvas/items.py``）按
    # 「gi 与 model 已漂移」把整组叶子收进**同一条** ``MoveItemsCommand``。
    # 组级缩放/旋转同理：``SelectionHandles`` 的多选等比公式（``scale_i' =
    # k·scale_i``、``pos_i' = A + k(pos_i − A)``）已按选择集工作，选择集就是
    # 组叶子集 ⇒ 一条 ``ChangeItemPropsCommand``。二者都是**零新增渲染通路**。

    def _refresh_undo_actions(self) -> None:
        if hasattr(self, "_undo_act"):
            self._undo_act.setText(f"撤销 {self._undo.undoText()}".strip())
            self._redo_act.setText(f"重做 {self._undo.redoText()}".strip())

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_page_bar())
        self.scene = PaperScene(self)
        self.view = PaperView(self.scene, self)
        self.view.set_tool(TOOL_SELECT)
        self._handles = SelectionHandles(self.scene, self.view, self)
        self._group_overlay = GroupOverlay(self.scene, self.view, self)
        self.view.viewChanged.connect(self._sync_snap_pitch)
        self.view.fit()
        self._sync_snap_pitch()
        self.scene.selectionChanged.connect(self._on_selection_changed)
        # 画布 + 标尺（上/左）
        canvas_box = QtWidgets.QGridLayout()
        self._ruler_h = RulerWidget(self.view, "h")
        self._ruler_v = RulerWidget(self.view, "v")
        canvas_box.addWidget(self._ruler_h, 0, 1)
        canvas_box.addWidget(self._ruler_v, 1, 0)
        canvas_box.addWidget(self.view, 1, 1)
        canvas_box.setRowStretch(1, 1)
        canvas_box.setColumnStretch(1, 1)
        root.addLayout(canvas_box, 1)
        root.addWidget(self._build_props())

    def _build_page_bar(self) -> QtWidgets.QWidget:
        """页签条（FR-09）：页签 + 增/删/复制/左移/右移 + 作业预览同步开关。

        页签是 ``QTabBar``（点即切换；**不要**用 QTabWidget 的页面栈 ——
        场景是**一个**扁平场景，页切换靠 :meth:`_rebuild_scene`，不是换 QWidget）。
        同步开关与页签同属「页行为」，故并排放在同一行。
        """
        bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self._page_bar = QtWidgets.QTabBar(bar)
        self._page_bar.currentChanged.connect(self._on_page_tab_changed)
        row.addWidget(QtWidgets.QLabel("页:"))
        row.addWidget(self._page_bar, 1)
        for text, fn in (("＋", self._on_page_add), ("－", self._on_page_del),
                         ("复制", self._on_page_dup), ("◀", self._on_page_left),
                         ("▶", self._on_page_right)):
            b = QtWidgets.QPushButton(text)
            b.setFixedWidth(52 if len(text) > 1 else 30)
            b.clicked.connect(fn)
            row.addWidget(b)
        # 页切换同步作业预览（PRD §10.1-8）。默认**开** —— 本来就要同步，做成
        # 可选是让步；关掉即退回「只有点『送去作业』才刷新」的老行为。
        self._sync_job_cb = QtWidgets.QCheckBox("同步作业预览", bar)
        self._sync_job_cb.setChecked(True)
        self._sync_job_cb.setToolTip(
            "切页后把当前页版面静默同步到作业页（不切页、不刷控制台）；\n"
            "关闭后切页不再刷新作业页，需要手动点『送去作业』。")
        row.addWidget(self._sync_job_cb)
        return bar

    def _refresh_page_bar(self) -> None:
        """页签与当前页对齐（用 ``blockSignals`` 避免触发切页副作用）。"""
        bar = getattr(self, "_page_bar", None)
        if bar is None:
            return
        bar.blockSignals(True)
        try:
            while bar.count() > 0:  # QTabBar 无 clear()，逐个 removeTab
                bar.removeTab(0)
            for i, page in enumerate(self.doc.pages):
                bar.addTab(f"{i + 1} {page.name}")
            bar.setCurrentIndex(self.doc.current)
        finally:
            bar.blockSignals(False)

    # -- 页管理（FR-09） ----------------------------------------------------

    def _on_page_tab_changed(self, index: int) -> None:
        if self.doc.switch_page(index):
            self._rebuild_scene()
            self._after_change()
            self._maybe_emit_job_sync()

    def _maybe_emit_job_sync(self) -> None:
        """当前页**确实切换**后，按开关把版面静默同步到作业页（PRD §10.1-8）。

        走 :attr:`job_sync_requested` 而非 :attr:`export_requested`：后者在
        MainWindow 侧会 ``setCurrentIndex(0)`` 把用户踢出排版页并刷一行控制台，
        而页切换是高频轻量动作。开关默认开，勾选状态读 :attr:`_sync_job_cb`。

        ⚠ 范围：只接**页切换**这一个触发点。镜像/移动/增/删等编辑是否也同步
        作业预览是尚未拍板的产品决定，故 ``_after_change`` 链**不**发本信号。
        """
        cb = getattr(self, "_sync_job_cb", None)
        if cb is not None and not cb.isChecked():
            return
        self.job_sync_requested.emit(self.to_job_spec())

    def _on_page_add(self) -> None:
        idx = self.doc.add_page(at=self.doc.current + 1)
        self._refresh_page_bar()
        self._page_bar.setCurrentIndex(idx)
        self._rebuild_scene()
        self._after_change()

    def _on_page_del(self) -> None:
        if not self.doc.remove_page():
            self.status_message.emit("至少保留一页")
            return
        self._refresh_page_bar()
        self._page_bar.setCurrentIndex(self.doc.current)
        self._rebuild_scene()
        self._after_change()

    def _on_page_dup(self) -> None:
        idx = self.doc.duplicate_page()
        if idx is None:
            return
        self._refresh_page_bar()
        self._page_bar.setCurrentIndex(idx)
        self._rebuild_scene()
        self._after_change()

    def _on_page_left(self) -> None:
        self._move_page(self.doc.current - 1)

    def _on_page_right(self) -> None:
        self._move_page(self.doc.current + 1)

    def _move_page(self, to: int) -> None:
        """页重排：模型层改序后**重挂当前页**（页内容随之移动，场景全量重建）。"""
        if not self.doc.move_page(self.doc.current, to):
            return
        self._refresh_page_bar()
        self._page_bar.setCurrentIndex(self.doc.current)
        self._rebuild_scene()
        self._after_change()

    def _rebuild_scene(self) -> None:
        """场景全量重建 = 清场景 → 按当前页逐叶子 ``make_gi``（FR-09 页切换）。

        渲染单元仍是 :func:`iter_leaves`（容器不建 PathItem，M1/M2 契约），
        只吃**当前页**（``doc.items`` 已是当前页门面）。

        **组编辑态在这里复位**（页切换 / 加页 / 删页 / 复制页 / 页重排五个操作的
        共同汇流点）：``GroupOverlay.editing`` 指的是**某个具体容器**，换页后它
        属于上一页，而 ``_expand_group_selection`` / ``on_item_double_clicked`` /
        :meth:`_selected_units` 全都拿它当哨兵 ⇒ 不复位的话，新页里「点组内子项
        选不中整组 / 双击进不去组 / 组框不画 / 拖动缩放对齐分布层序复制全按叶子
        语义走」—— 整组编辑语义在下一页**静默失效**。editing 只当哨兵用（容器
        对象从不被解引用），故不会崩，只会一直错下去。

        复位本身是用户预期内的（换页本就换了内容），故不发状态提示。
        """
        self._group_overlay.set_editing(None)   # 顺带清掉上一页残留的组框
        for gi in list(self._scene_items):
            self.scene.removeItem(gi)
        self._scene_items.clear()
        for leaf in iter_leaves(self.doc.items):
            make_gi(self, leaf)
        self._refresh_page_bar()

    def _build_toolbar(self) -> QtWidgets.QWidget:
        """工具条：QToolBar（自带溢出「»」），避免按钮过多撑宽窗口。"""
        tb = QtWidgets.QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(16, 16))

        def add_btn(text, fn, checkable=False):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            b.clicked.connect(fn)
            tb.addWidget(b)
            return b

        # 工具模式
        self.tool_group = QtWidgets.QButtonGroup(self)
        self.tool_group.setExclusive(True)
        for label, tool in (("选择", TOOL_SELECT), ("直线", TOOL_LINE),
                            ("矩形", TOOL_RECT), ("圆", TOOL_CIRCLE),
                            ("折线", TOOL_POLY), ("自由笔", TOOL_PENCIL)):
            b = add_btn(label, lambda _c=False, t=tool: self._set_tool(t),
                        checkable=True)
            b.setChecked(tool == TOOL_SELECT)
            self.tool_group.addButton(b)
        tb.addSeparator()
        # 吸附开关（§4.2）
        self.btn_snap = add_btn("吸附", self._toggle_snap, checkable=True)
        self.btn_snap.setChecked(True)
        tb.addSeparator()
        # 添加
        for label, fn in (("加文字…", self._add_text), ("加图片…", self._add_image),
                          ("导入SVG…", self._add_svg),
                          ("导入Word/Excel…", self._add_doc)):
            add_btn(label, fn)
        tb.addSeparator()
        # 撤销/重做
        self._undo_act = self._undo.createUndoAction(self, "撤销")
        self._undo_act.setShortcut(QtGui.QKeySequence.Undo)
        self._redo_act = self._undo.createRedoAction(self, "重做")
        self._redo_act.setShortcut(QtGui.QKeySequence.Redo)
        self.addAction(self._undo_act)
        self.addAction(self._redo_act)
        for a in (self._undo_act, self._redo_act):
            b = QtWidgets.QToolButton()
            b.setDefaultAction(a)
            b.setAutoRaise(True)
            tb.addWidget(b)
        tb.addSeparator()
        # 镜像（FR-08，贴纸转印）。**checkable**：勾选态即「当前选中项处于镜像
        # 态」，由 :meth:`_refresh_mirror_buttons` 按模型真值回写。
        self.btn_mirror: dict[str, QtWidgets.QToolButton] = {}
        for label, axis in (("水平镜像", "h"), ("垂直镜像", "v")):
            # `_checked=False` 占位：QToolButton.clicked 带 bool 参数，PySide6
            # 会按可调用对象形参个数实参化 —— 只写 `lambda a=axis: …` 的话那个
            # bool 会顶掉默认的 axis（axis 变成 True/False，镜像轴串台）。
            b = add_btn(label,
                        lambda _checked=False, a=axis: self._toggle_mirror(a),
                        checkable=True)
            b.setToolTip(f"{label}（勾选 = 当前选中项处于该镜像态）")
            self.btn_mirror[axis] = b
        tb.addSeparator()
        # 编组/解组（FR-03）
        for label, fn in (("编组", self.group_selected),
                          ("解组", self.ungroup_selected)):
            add_btn(label, fn)
        tb.addSeparator()
        # 层序/对齐/删除
        for label, fn in (("置顶", lambda: self._zorder("top")),
                          ("置底", lambda: self._zorder("bottom")),
                          ("上移", lambda: self._zorder("up")),
                          ("下移", lambda: self._zorder("down")),
                          ("左对齐", lambda: self._align("left")),
                          ("水平居中", lambda: self._align("hcenter")),
                          ("垂直分布", lambda: self._distribute("v")),
                          ("删除", self._delete_selected)):
            add_btn(label, fn)
        tb.addSeparator()
        # 缩放
        for label, fn in (("－", self._zoom_out), ("＋", self._zoom_in),
                          ("适合窗口", self._fit), ("100%", self._zoom_100)):
            add_btn(label, fn)
        return tb

    def _build_props(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        props = QtWidgets.QHBoxLayout(box)
        props.setContentsMargins(0, 0, 0, 0)
        for name in ("X", "Y", "宽", "高", "角度°"):
            props.addWidget(QtWidgets.QLabel(name + ":"))
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(-1000, 1000)
            sp.setDecimals(2)
            sp.setFixedWidth(72)  # 紧凑，避免撑宽
            sp.valueChanged.connect(self._apply_props)
            sp.editingFinished.connect(self._end_prop_gesture)
            setattr(self, {"X": "sp_x", "Y": "sp_y", "宽": "sp_w", "高": "sp_h",
                           "角度°": "sp_ang"}[name], sp)
            props.addWidget(sp)
        props.addWidget(QtWidgets.QLabel("缩放:"))
        self.sp_scale = QtWidgets.QDoubleSpinBox()
        self.sp_scale.setRange(0.05, 20.0)
        self.sp_scale.setValue(1.0)
        self.sp_scale.setSingleStep(0.1)
        self.sp_scale.setFixedWidth(72)
        self.sp_scale.valueChanged.connect(self._apply_props)
        self.sp_scale.editingFinished.connect(self._end_prop_gesture)
        props.addWidget(self.sp_scale)
        # 镜像状态**只读**指示（FR-08）：镜像是读取时施加的标志，属性栏里看不到
        # 就等于「切了但不知道切没切」。这里只显示不编辑 —— 切换入口仍是工具条
        # 的两个镜像按钮（免得多一条编辑路径要各自接可撤销命令/手势 token）。
        props.addWidget(QtWidgets.QLabel("镜像:"))
        self.lb_mirror = QtWidgets.QLabel("无")
        self.lb_mirror.setMinimumWidth(56)
        self.lb_mirror.setToolTip("当前选中图元的镜像状态（只读；切换用工具条的镜像按钮）")
        props.addWidget(self.lb_mirror)
        props.addStretch(1)
        self.btn_save = QtWidgets.QPushButton("另存为 SVG…")
        self.btn_save.clicked.connect(self._on_save_svg)
        props.addWidget(self.btn_save)
        self.btn_export = QtWidgets.QPushButton("导出并送去执行")
        self.btn_export.clicked.connect(self._on_export)
        props.addWidget(self.btn_export)
        return box

    # -- 工具模式 ----------------------------------------------------------

    def _set_tool(self, tool: str) -> None:
        self.cancel_draw()
        self._tool = tool
        self.view.set_tool(tool)

    def _toggle_snap(self) -> None:
        self.snap_enabled = self.btn_snap.isChecked()

    def _sync_snap_pitch(self) -> None:
        """网格吸附 pitch 随缩放定价 = grid_steps(ppm) 的 minor（§2.2/Q4）。

        缩放变化时自动更新 ``snap_pitch``（测试/调用方可显式覆盖，下次缩放
        重算）。评审 low #3①：旧实现恒 1.0mm 不随缩放。
        """
        ppm = self.view.px_per_mm()
        if ppm > 0:
            self.snap_pitch = SnapEngine.minor_pitch(ppm)

    def _snap_engine(self) -> SnapEngine:
        return SnapEngine(self.snap_pitch, enabled=self.snap_enabled)

    def _snap_pt(self, p: QtCore.QPointF) -> QtCore.QPointF:
        """绘制工具吸附（§2.2：网格 + 对象，同样接入 SnapEngine）。"""
        engine = self._snap_engine()
        x, y = engine.snap((float(p.x()), float(p.y())), self._all_paths())
        return QtCore.QPointF(x, y)

    def _all_paths(self) -> list:
        """绘制工具的对象吸附候选 = **页面系**可见几何（FR-06 前置修复）。

        旧实现返回 ``it.paths``（**局部**坐标）而 :meth:`_snap_pt` 喂入的是页面
        坐标 → 域不一致，``pos≠(0,0)`` 的图元在页面点附近恒零对象命中（既有
        ``test_grid_snap_draw_tool_and_item`` 是空文档、只验网格，掩盖了该 bug）。
        候选源现与拖动侧 ``PathItem._snap_value`` 共用
        :func:`~megapro.gui.canvas.snap.snap_candidate_paths`（一份实现），
        且随编组自动含组内叶子、排除隐藏图元。
        """
        return snap_candidate_paths(self.doc)

    # -- 选中/属性 ---------------------------------------------------------

    def _selected(self) -> list[PathItem]:
        return [gi for gi in self.scene.selectedItems()
                if isinstance(gi, PathItem)]

    def _on_selection_changed(self) -> None:
        self._end_prop_gesture()  # 换目标即新手势（禁止跨选中合并）
        self._expand_group_selection()  # 普通态：选一个组成员 = 选整组
        self._refresh_props()
        self._handles.sync(self._selected())
        self._group_overlay.refresh(self._page_box)

    def _refresh_props(self) -> None:
        sel = self._selected()
        # 镜像按钮/指示器同源于选中项（早于两个分支，故无选中也会刷新）
        self._refresh_mirror_buttons()
        self._refresh_mirror_label(sel)
        spins = (self.sp_x, self.sp_y, self.sp_w, self.sp_h, self.sp_ang,
                 self.sp_scale)
        if not sel:
            for s in spins:
                s.blockSignals(True)
                s.setValue(0 if s is not self.sp_scale else 1.0)
                s.blockSignals(False)
                s.setEnabled(False)
            return
        for s in spins:
            s.setEnabled(True)
            s.blockSignals(True)
        it = sel[0].model_item
        if len(sel) == 1:
            self.sp_x.setValue(it.pos[0])
            self.sp_y.setValue(it.pos[1])
            x0, y0, x1, y1 = self._page_box(it)
        else:
            # 多选：X/Y 显示操作单元集合的 bbox 锚点（与 _apply_pos 的整体平移
            # 一致）；组按**容器** page_bbox 参与（FR-05 普通态单元二分）
            x0, y0, x1, y1 = _union_bbox([self._unit_bbox(u)
                                         for u, _ in self._selected_units()])
            ax, ay = anchor_point((x0, y0, x1, y1), self._pos_anchor)
            self.sp_x.setValue(ax)
            self.sp_y.setValue(ay)
        self.sp_w.setValue(abs(x1 - x0))
        self.sp_h.setValue(abs(y1 - y0))
        self.sp_ang.setValue(it.angle_deg)
        self.sp_scale.setValue(it.scale)
        for s in spins:
            s.blockSignals(False)

    def _refresh_mirror_label(self, sel: list) -> None:
        """属性栏的镜像状态**只读**指示：多选按「全同 / 混合」口径显示。"""
        if not sel:
            self.lb_mirror.setText("无")
            self.lb_mirror.setEnabled(False)
            return
        self.lb_mirror.setEnabled(True)
        if len(sel) == 1:
            it = sel[0].model_item
            texts = {(True, False): "水平", (False, True): "垂直",
                     (True, True): "水平+垂直", (False, False): "无"}
            self.lb_mirror.setText(texts[(bool(it.mirror_x), bool(it.mirror_y))])
            return
        all_x = all(gi.model_item.mirror_x for gi in sel)
        all_y = all(gi.model_item.mirror_y for gi in sel)
        if all_x and all_y:
            self.lb_mirror.setText("水平+垂直")
        elif all_x:
            self.lb_mirror.setText("水平")
        elif all_y:
            self.lb_mirror.setText("垂直")
        else:
            self.lb_mirror.setText("混合")

    def _apply_props(self) -> None:
        """数值定位（根因 #10）：按改动字段分派，全部走可撤销命令。"""
        sel = self._selected()
        if not sel:
            return
        src = self.sender()
        if src in (self.sp_x, self.sp_y):
            self._apply_pos(self.sp_x.value(), self.sp_y.value())
        elif src is self.sp_w:
            self._apply_size(self.sp_w.value(), axis="w")
        elif src is self.sp_h:
            self._apply_size(self.sp_h.value(), axis="h")
        elif src is self.sp_ang:
            self._apply_angle(self.sp_ang.value())
        elif src is self.sp_scale:
            self._apply_scale(self.sp_scale.value())

    def _end_prop_gesture(self) -> None:
        """结束数值输入手势（editingFinished / 换选中）：下一次改值另起撤销条目。"""
        self._prop_token = None
        self._prop_sender = None

    def _push_props(self, changes, text: str) -> None:
        """推属性命令；同字段连续改值（键入 "100"=1→10→100）共用一个手势 token
        合并为一条撤销（评审 low #6）。"""
        if not changes:
            return
        src = self.sender()
        if src is not self._prop_sender or self._prop_token is None:
            self._prop_sender = src
            self._prop_token = new_gesture_token()
        self._undo.push(ChangeItemPropsCommand(self, changes, text,
                                               token=self._prop_token))

    def _apply_pos(self, tx: float, ty: float) -> None:
        """单选=锚点绝对定位；多选=按**操作单元** bbox 锚点整体平移（不塌缩）。

        FR-05 单元二分：普通态组按容器 bbox 参与，但**结果作用在叶子集**
        （一条命令、组内布局保持 —— 容器恒等、无变换通路）；组编辑态单元=叶子。
        """
        sel = self._selected()
        if not sel:
            return
        units = self._selected_units()
        changes = []
        if len(sel) == 1:
            it = sel[0].model_item
            changes.append((it, {"pos": it.pos},
                           {"pos": _pos_for_anchor(it, (tx, ty), self._pos_anchor)}))
        else:
            ax, ay = anchor_point(_union_bbox([self._unit_bbox(u) for u, _ in units]),
                                  self._pos_anchor)
            dx, dy = tx - ax, ty - ay
            for unit, _ in units:
                for leaf in self._leaves_of_unit(unit):
                    it = leaf
                    changes.append((it, {"pos": it.pos},
                                   {"pos": (it.pos[0] + dx, it.pos[1] + dy)}))
        self._push_props(changes, "数值定位")

    def _leaves_of_unit(self, unit: Item) -> list[Item]:
        """操作单元 → 实际要写 pos/scale 的**叶子集合**（组 = 整棵子树的叶子）。

        组级变换只写叶子集合（FR-04/FR-05 裁决）：容器恒为恒等、场景里没有
        容器 gi，组级移动/缩放/旋转由此走既有多选机制。
        """
        if unit.is_container():
            return list(iter_leaves([unit]))
        return [unit]

    def _apply_size(self, value: float, *, axis: str) -> None:
        """宽/高等比联动（sp_h 生效：scale × target/cur）。

        FR-05：多选按**操作单元** bbox 定 k，组内叶子按同一 k 等比缩放并围绕
        锚点整体平移（一条命令、组内布局保持）。
        """
        sel = self._selected()
        if value <= 0:
            return
        if len(sel) == 1:
            it = sel[0].model_item
            x0, y0, x1, y1 = self._page_box(it)
            cur = abs(x1 - x0) if axis == "w" else abs(y1 - y0)
            if cur <= 1e-9:
                return
            k = value / cur
            self._push_props(
                [(it, {"scale": it.scale}, {"scale": it.scale * k})], "等比缩放")
            return
        units = self._selected_units()
        u = _union_bbox([self._unit_bbox(x) for x, _ in units])
        cur = abs(u[2] - u[0]) if axis == "w" else abs(u[3] - u[1])
        if cur <= 1e-9:
            return
        k = value / cur
        ax, ay = anchor_point(u, self._pos_anchor)
        changes = []
        for unit, _ in units:
            for it in self._leaves_of_unit(unit):
                changes.append((it,
                                {"pos": it.pos, "scale": it.scale},
                                {"pos": (ax + k * (it.pos[0] - ax),
                                         ay + k * (it.pos[1] - ay)),
                                 "scale": it.scale * k}))
        self._push_props(changes, "整体等比缩放")

    def _apply_angle(self, deg: float) -> None:
        changes = []
        for gi in self._selected():
            it = gi.model_item
            changes.append((it, {"angle_deg": it.angle_deg}, {"angle_deg": deg}))
        self._push_props(changes, "旋转")

    def _apply_scale(self, value: float) -> None:
        sel = self._selected()
        if value <= 0 or not sel:
            return
        if len(sel) == 1:
            it = sel[0].model_item
            self._push_props(
                [(it, {"scale": it.scale}, {"scale": value})], "缩放")
            return
        k = value / (sel[0].model_item.scale or 1e-9)
        units = self._selected_units()
        ax, ay = anchor_point(_union_bbox([self._unit_bbox(x) for x, _ in units]),
                              self._pos_anchor)
        changes = []
        for unit, _ in units:
            for it in self._leaves_of_unit(unit):
                changes.append((it,
                                {"pos": it.pos, "scale": it.scale},
                                {"pos": (ax + k * (it.pos[0] - ax),
                                         ay + k * (it.pos[1] - ay)),
                                 "scale": it.scale * k}))
        self._push_props(changes, "整体等比缩放")

    def _sync_models(self) -> None:
        """model → 场景推送（幂等；拖动已由 MoveItemsCommand 回写 model）。"""
        for gi in list(self._scene_items):
            self._sync_gi(gi.model_item)

    # -- 键盘：删除/复制/箭头 ----------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.view.hasFocus() or self.hasFocus():
            k = event.key()
            mods = event.modifiers()
            if k in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
                self._delete_selected()
                return
            if k == QtCore.Qt.Key_A and mods & QtCore.Qt.ControlModifier:
                for gi in self._scene_items:
                    if not gi.model_item.locked:
                        gi.setSelected(True)
                return
            if k == QtCore.Qt.Key_C and mods & QtCore.Qt.ControlModifier:
                self._copy_selected()
                return
            if k == QtCore.Qt.Key_V and mods & QtCore.Qt.ControlModifier:
                self._paste()
                return
            if k == QtCore.Qt.Key_D and mods & QtCore.Qt.ControlModifier:
                self._duplicate()
                return
            if k in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right,
                     QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
                self._nudge(k, bool(mods & QtCore.Qt.ShiftModifier))
                return
            if k == QtCore.Qt.Key_Escape and self._group_overlay.editing is not None:
                self._exit_group_edit()  # 退出组编辑态
                return
        super().keyPressEvent(event)

    def _nudge(self, key, big: bool) -> None:
        step = self.snap_pitch * (10 if big else 1)
        dx = -step if key == QtCore.Qt.Key_Left else step if key == QtCore.Qt.Key_Right else 0
        dy = -step if key == QtCore.Qt.Key_Down else step if key == QtCore.Qt.Key_Up else 0
        moves = []
        for gi in self._selected():
            it = gi.model_item
            old = it.pos
            moves.append((it, old, (old[0] + dx, old[1] + dy)))
        if moves:
            self._undo.push(MoveItemsCommand(self, moves, "微调"))

    def _delete_selected(self) -> None:
        items = [gi.model_item for gi in self._selected()]
        if items:
            self._undo.push(RemoveItemsCommand(self, items))

    def _copy_selected(self) -> None:
        """复制（FR-06 递归化）：按**操作单元**序列化，容器连子树一起带走。"""
        units = self._selected_units()
        seen: set[int] = set()
        data = []
        for unit, _ in units:
            if id(unit) in seen:
                continue
            seen.add(id(unit))
            data.append(_item_to_json(unit))
        QtWidgets.QApplication.clipboard().setText(json.dumps(data))

    def _paste(self) -> None:
        try:
            data = json.loads(QtWidgets.QApplication.clipboard().text())
        except Exception:
            return
        items = []
        top_z = self.doc.top_z()
        for d in data:
            it = _item_from_json(d, dz=5.0, z=top_z + 1)
            # 整棵子树抬到最上（散件=自身，行为与旧实现一致）
            top_z = _restack_above(it, top_z)
            items.append(it)
        if items:
            self._undo.push(AddItemsCommand(self, items, "粘贴"))

    def _duplicate(self) -> None:
        """复制副本：同 :meth:`_copy_selected` 的单元口径 + 深拷贝子树。"""
        units = self._selected_units()
        seen: set[int] = set()
        items = []
        top_z = self.doc.top_z()
        for unit, _ in units:
            if id(unit) in seen:
                continue
            seen.add(id(unit))
            clone = _clone_item(unit, dz=5.0, z=top_z + 1)
            top_z = _restack_above(clone, top_z)   # 整棵子树都要在最上（见 _restack_above）
            items.append(clone)
        if items:
            self._undo.push(AddItemsCommand(self, items, "复制副本"))

    # -- 镜像（FR-08） -----------------------------------------------------

    def _toggle_mirror(self, axis: str) -> None:
        """切换选中图元的水平/垂直镜像（FR-08，可撤销）。

        走 :class:`ChangeItemPropsCommand`（镜像标志属其 setattr 通道），该
        命令已按 ``_GEOMETRY_FIELDS`` 额外 ``rebuild_path()`` ⇒ 画布立即重画。
        镜像**绕本地 bbox 中心**、``pos``/``scale``/``angle`` 不补偿 ⇒ 页面
        位置与 bbox 稳定，只内容左右/上下翻转（贴纸转印：刻完翻面字是正的）。

        多选时取**全部选中项当前值的反值**中的一致方向：全 False → 置 True，
        全 True → 置 False，混合 → 置 True（收敛到统一，便于再点一次归零）。
        按钮的勾选态与之**同口径**（全选中项皆该态才算勾上，见
        :meth:`_refresh_mirror_buttons`），故「点一下」永远等于「切到未勾」。

        未选中图元时**不静默**：按钮已被 Qt 翻转的勾选态先回滚，再给中文提示。
        """
        field = "mirror_x" if axis == "h" else "mirror_y"
        sel = self._selected()
        if not sel:
            self._refresh_mirror_buttons()  # 撤掉 Qt 的自动翻转（无选中=全 False）
            self.status_message.emit("未选中图元：请先选中要镜像的图元")
            return
        cur = [getattr(gi.model_item, field) for gi in sel]
        new = not all(cur)
        changes = [
            (gi.model_item, {field: getattr(gi.model_item, field)},
             {field: new})
            for gi in sel
            if getattr(gi.model_item, field) != new
        ]
        if changes:
            self._undo.push(ChangeItemPropsCommand(
                self, changes, "水平镜像" if axis == "h" else "垂直镜像"))
        self._after_change()

    def _refresh_mirror_buttons(self) -> None:
        """两个镜像按钮的勾选态 ← **模型真值**（当前选中项的镜像标志）。

        ``checkable`` 的 QToolButton 被点击时 Qt 会**自己**翻转勾选态，所以每次
        刷新都必须按真值回写，否则会出现「点了没选中、按钮却亮着」「镜像被撤销
        了、按钮还亮着」这类假状态。

        多选口径与 :meth:`_toggle_mirror` 的下一状态一致：**全部**选中项都处于
        该镜像态才算勾上（全 True → 下一次点击是取消；混合/全 False → 下一次
        点击是置 True）。
        """
        sel = self._selected()
        for axis, btn in getattr(self, "btn_mirror", {}).items():
            field = "mirror_x" if axis == "h" else "mirror_y"
            if not sel:
                on = False
            elif len(sel) == 1:
                on = bool(getattr(sel[0].model_item, field))
            else:
                on = all(getattr(gi.model_item, field) for gi in sel)
            btn.setChecked(on)

    # -- 层序 / 对齐 / 分布 -------------------------------------------------

    def _zorder(self, mode: str) -> None:
        """层序（FR-06 组语义）：组按**整组**参与，组内叶子 z 同步更新且
        **保持组内相对次序**，全部一条命令。

        「up/down」按组内**最小** z 判档、整体平移同一增量（保持组内间距）；
        「top/bottom」把整组压到全树最上/最下（组内相对次序仍保留）。
        """
        sel = self._selected()
        if not sel:
            return
        changes = []
        for unit, _ in self._selected_units():
            leaves = self._leaves_of_unit(unit)
            if not leaves:
                continue
            zs = [it.z for it in leaves]
            if mode == "top":
                new_zs = [self.doc.top_z() + 1 + i for i in range(len(leaves))]
            elif mode == "bottom":
                base = self.doc.bottom_z() - 1
                new_zs = [base + i for i in range(len(leaves))]
            else:
                cur = min(zs) if mode == "up" else max(zs)
                delta = 1.0 if mode == "up" else -1.0
                new_zs = [z + delta for z in zs]
            for it, nz in zip(leaves, new_zs):
                if it.z != nz:
                    changes.append((it, {"z": it.z}, {"z": nz}))
        if changes:
            self._undo.push(ChangeItemPropsCommand(self, changes, "层序"))

    def _align(self, mode: str) -> None:
        """对齐（FR-06 组语义）：组按**容器 bbox** 参与，组内布局保持。

        每个操作单元算一次位移，再作用到该单元的**整个叶子集**（一条命令）。
        """
        units = self._selected_units()
        if len(units) < 2:
            return
        boxes = [(u, self._unit_bbox(u)) for u, _ in units]
        xs0 = [b[0] for _, b in boxes]
        xs1 = [b[2] for _, b in boxes]
        ys0 = [b[1] for _, b in boxes]
        ys1 = [b[3] for _, b in boxes]
        changes = []
        for unit, (x0, y0, x1, y1) in boxes:
            dx = dy = 0.0
            if mode == "left":
                dx = min(xs0) - x0
            elif mode == "right":
                dx = max(xs1) - x1
            elif mode == "hcenter":
                dx = (min(xs0) + max(xs1)) / 2 - (x0 + x1) / 2
            elif mode == "top":
                dy = max(ys1) - y1
            elif mode == "bottom":
                dy = min(ys0) - y0
            for it in self._leaves_of_unit(unit):
                changes.append((it, {"pos": it.pos},
                               {"pos": (it.pos[0] + dx, it.pos[1] + dy)}))
        if changes:
            self._undo.push(ChangeItemPropsCommand(self, changes, "对齐"))

    def _distribute(self, axis: str) -> None:
        """分布（FR-06 组语义）：组按容器 bbox 作为一个分布单元参与。"""
        units = self._selected_units()
        if len(units) < 3:
            return
        boxes = [(u, self._unit_bbox(u)) for u, _ in units]
        changes = []
        if axis == "v":
            boxes.sort(key=lambda t: t[1][1])
            lo = boxes[0][1][1]
            hi = boxes[-1][1][3]
            gaps = (hi - lo) / (len(boxes) - 1)
            for i, (unit, (x0, y0, x1, y1)) in enumerate(boxes):
                target = lo + i * gaps
                for it in self._leaves_of_unit(unit):
                    changes.append((it, {"pos": it.pos},
                                   {"pos": (it.pos[0], it.pos[1] + target - y0)}))
        else:
            boxes.sort(key=lambda t: t[1][0])
            lo = boxes[0][1][0]
            hi = boxes[-1][1][2]
            gaps = (hi - lo) / (len(boxes) - 1)
            for i, (unit, (x0, y0, x1, y1)) in enumerate(boxes):
                target = lo + i * gaps
                for it in self._leaves_of_unit(unit):
                    changes.append((it, {"pos": it.pos},
                                   {"pos": (it.pos[0] + target - x0, it.pos[1])}))
        if changes:
            self._undo.push(ChangeItemPropsCommand(self, changes, "分布"))

    # -- 添加 --------------------------------------------------------------

    def _add_items(self, items: list[Item], text: str = "添加") -> None:
        if items:
            self._undo.push(AddItemsCommand(self, items, text))

    def _add_text(self, edit_item: Item | None = None) -> None:
        dlg = _TextDialog(self, edit_item)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            paths = dlg.generate_paths()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "文字生成失败", str(exc))
            return
        if not paths:
            QtWidgets.QMessageBox.warning(self, "无路径", "文字未能生成可画路径")
            return
        it = _import_group([paths], name=f"文字:{dlg.text()[:8]}")[0]
        if edit_item is not None:
            self._undo.push(EditTextCommand(self, edit_item, it.paths, dlg.spec()))
        else:
            it.z = self.doc.top_z() + 1
            self._add_items([it], "加文字")

    # -- 图片 → 线条（FR-10：多模式 + 可重追） -------------------------------

    #: 加图模式 → (显示名, spec 里的 mode 值)
    _IMAGE_MODES = (
        ("中心线（骨架，消双线）", "center"),
        ("区域轮廓（potrace，双线）", "outline"),
        ("照片/素描（Canny+骨架）", "canny"),
    )

    def _trace_image(self, source: str, *, mode: str, threshold: int,
                     use_multi: bool, target_mm: float,
                     low: int | None = None, high: int | None = None
                     ) -> str:
        """按模式产出 SVG。**唯一**的产线分派（导入与重追共用，不各写一套）。"""
        if mode == "center":
            if use_multi:
                from megapro.gui.edge_to_svg import image_to_centerline_svg_multi
                return image_to_centerline_svg_multi(source, target_mm=target_mm)
            from megapro.gui.edge_to_svg import image_to_centerline_svg
            return image_to_centerline_svg(source, target_mm=target_mm,
                                           threshold=threshold)
        if mode == "canny":
            # FR-10：照片/素描用 Canny+骨架（低对比友好、单像素宽）；多阈值
            # 对该管线无意义（阈值是梯度对），故一律走单档。
            from megapro.gui.edge_to_svg import image_to_canny_centerline_svg
            kw = {}
            if low is not None:
                kw["low"] = int(low)
            if high is not None:
                kw["high"] = int(high)
            return image_to_canny_centerline_svg(source, target_mm=target_mm, **kw)
        if use_multi:
            from megapro.gui.image_to_svg import trace_image_multi
            return trace_image_multi(source, target_mm=target_mm)
        from megapro.gui.image_to_svg import trace_image
        return trace_image(source, target_mm=target_mm, threshold=threshold)

    def _image_spec(self, source: str, *, mode: str, threshold: int,
                    use_multi: bool, target_mm: float,
                    low: int | None, high: int | None) -> dict:
        """产线参数回执（FR-10：``Item.image_spec``，仿 ``text_spec``）。

        **只记该模式真的吃进去的参数**（口径与 :meth:`_trace_image` 逐条对齐）：

        - ``canny`` 只吃 ``low``/``high``（阈值对是梯度对）⇒ 不写
          ``threshold``/``multi``。写进去就是**假信息**：看 spec（或照它重追）
          会以为这两个参数参与了产线，其实被 :meth:`_trace_image` 的 canny 分支
          直接忽略 —— 对话框把它们一并置灰（见 :meth:`_ask_image_params`）。
        - 其它模式只吃 ``threshold``/``multi`` ⇒ 反过来不写 low/high。
        """
        spec = {"source": str(source), "mode": mode,
                "target_mm": float(target_mm)}
        if mode == "canny":
            spec["low"] = int(low) if low is not None else None
            spec["high"] = int(high) if high is not None else None
        else:
            spec["threshold"] = int(threshold)
            spec["multi"] = bool(use_multi)
        return spec

    def _add_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*)")
        if not path:
            return
        mode, threshold, use_multi, target_mm, low, high = self._ask_image_params(
            default_source=None)
        if mode is None:
            return
        try:
            svg = self._trace_image(path, mode=mode, threshold=threshold,
                                    use_multi=use_multi, target_mm=target_mm,
                                    low=low, high=high)
            paths = _svg_to_paths(svg)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(
                self, "图片转线条失败",
                f"{exc}\n（中心线需 scikit-image；potrace 需 bin/potrace.exe；"
                "Canny 需 opencv-python + scikit-image）")
            return
        if not paths:
            QtWidgets.QMessageBox.warning(self, "无线条", "阈值后没有可追踪的线条")
            return
        # **单 Item 持全部折线**（FR-10 坑 1 / US-5 承重契约）：Canny+骨架对一张
        # 照片必然产出**多条**折线（一个物体一条），它们必须进**同一个** Item。
        # 机制上：``_import_group`` 的入参是「组列表」，``[paths]`` 是**一个**组
        # ⇒ 返回 1 个 Item，其 ``paths`` 就是那几条折线（``[0]`` 取的是**组**，
        # 不是「第一条折线」—— 早前那版注释把它说成后者，是错的）。
        # ⚠ 不要改成「每条折线一项」：:class:`RetraceImageCommand` 作用于
        # **单个** item，拆开后重追会只换其中一条、其余留在原参数下（旧几何）。
        it = _import_group([paths], name=f"图:{Path(path).name[:8]}")[0]
        it.z = self.doc.top_z() + 1
        it.image_spec = self._image_spec(
            path, mode=mode, threshold=threshold, use_multi=use_multi,
            target_mm=target_mm, low=low, high=high)
        self._add_items([it], "加图片")

    def _ask_image_params(self, *, default_source: str | None = None,
                          spec: dict | None = None) -> tuple | None:
        """加图/重追共用参数对话框；返回 ``(mode, threshold, multi, mm, low,
        high)``，用户取消返回 ``None``。

        ``spec``（重追时 = ``item.image_spec``）非空则**预填**各控件：模式/
        阈值/多阈值/最长边/Canny 阈值都回到上次用的值，用户只改要改的那项
        （US-5「刻完后调阈值重追」的前提是其余参数不丢）。

        两条诚实性约束（否则「预填」= 静默换一组参数重算，见 FR-10）：

        1. **0 是合法取值**：Canny 阈值的预填走 ``spec.get(k)`` + ``is None``
           判断，不能用 ``spec.get(k) or 默认值`` —— 后者把 ``0`` 吞成默认
           （low=0/high=0 被预填成 50/120，用户点确定后**真的**用 50/120 重跑）。
           其余字段（threshold/target_mm/mode/multi）沿用同函数既有的
           ``.get(k, default)`` 写法。
        2. **失效控件不装样子**：canny 分支只吃 low/high，``threshold``/``multi``
           在该模式下无效 ⇒ 置灰 + 标签明说「不使用」。控件的**预填值原样保留**
           （不销毁用户数据），真正的不诚实由 :meth:`_image_spec` 兜住 ——
           canny 的 spec 里**不写**这两个从未参与产线的字段。
        """
        spec = dict(spec or {})
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("图片→线条参数")
        form = QtWidgets.QFormLayout(dlg)
        mode = QtWidgets.QComboBox()
        for label, value in self._IMAGE_MODES:
            mode.addItem(label, value)
        # 预填：spec 里的 mode（不在列表里则留默认第一项）
        if spec.get("mode") in [v for _l, v in self._IMAGE_MODES]:
            mode.setCurrentIndex([v for _l, v in self._IMAGE_MODES]
                                 .index(spec["mode"]))
        form.addRow("模式:", mode)
        multi = QtWidgets.QCheckBox("多阈值合并（一次提全淡→浓所有线条）")
        multi.setChecked(bool(spec.get("multi", False)))
        form.addRow(multi)
        th = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        th.setRange(0, 255)
        th.setValue(int(spec.get("threshold", 160)))
        form.addRow("阈值(暗→线，未勾多阈值时用):", th)
        lo = QtWidgets.QSpinBox()
        lo.setRange(0, 255)
        # 0 是合法取值：只有「键缺失 / 值为 None」才回默认（``or`` 会吞 0）
        lo.setValue(50 if spec.get("low") is None else int(spec["low"]))
        lo.setToolTip("Canny 低阈（0 合法）")
        form.addRow("Canny 低阈:", lo)
        hi = QtWidgets.QSpinBox()
        hi.setRange(0, 255)
        hi.setValue(120 if spec.get("high") is None else int(spec["high"]))
        hi.setToolTip("Canny 高阈（0 合法）")
        form.addRow("Canny 高阈:", hi)
        mm = QtWidgets.QDoubleSpinBox()
        mm.setRange(10, BED_W)
        mm.setValue(float(spec.get("target_mm", 100.0)))
        form.addRow("最长边(mm):", mm)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        def _sync_mode_controls() -> None:
            """模式 ⟶ 失效控件（canny 只吃 low/high，见 :meth:`_trace_image`）。

            只**置灰 + 改标签**，不动控件里的值：值还在，但该模式下它不会被
            消费，也不会进 spec（:meth:`_image_spec` 已按模式裁字段）。
            """
            canny = mode.currentData() == "canny"
            th.setEnabled(not canny)
            multi.setEnabled(not canny)
            label = form.labelForField(th)
            if label is not None:
                label.setText("阈值(Canny 模式不使用):" if canny
                              else "阈值(暗→线，未勾多阈值时用):")

        mode.currentIndexChanged.connect(lambda _i: _sync_mode_controls())
        _sync_mode_controls()
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return (mode.currentData(), th.value(), multi.isChecked(), mm.value(),
                lo.value(), hi.value())

    def _add_svg(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "导入 SVG", "", "SVG (*.svg);;所有文件 (*)")
        if not path:
            return
        try:
            from megapro.toolchain.svg_to_gcode import parse_svg
            paths = parse_svg(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "导入失败", str(exc))
            return
        if not paths:
            QtWidgets.QMessageBox.warning(self, "空", "SVG 无几何")
            return
        it = _import_group([paths], name=f"SVG:{Path(path).name[:8]}")[0]
        it.z = self.doc.top_z() + 1
        self._add_items([it], "导入SVG")

    def _add_doc(self) -> None:
        """导入 Word/Excel（M2 · T7b：调用侧翻转 —— 整组包进恒等容器）。

        ``doc_import.extract_*`` 的**平铺返回契约不变**（M1 冻结用例依赖顶层
        名字查找与 ``len(items)>=3``），包装只发生在这里：``wrap_group`` 恒等
        容器（``paths=[]``/``pos=(0,0)``/``scale=1``/``angle_deg=0``），子项
        数据零改写 ⇒ ``flatten_visible([容器])`` 与平铺逐点恒等（含折线顺序）。

        「严禁逐 Item 归位」的导入期语义仍由既有 :func:`_import_group` 契约
        承担（``_finish`` 内已做组级一次归位）；容器只把「相对布局不散」从
        契约升级为**结构属性**（FR-07 v1.2 裁决：组变换走叶子集合，容器恒等）。
        """
        from megapro.gui.layout.doc_import import DocImportDialog, wrap_group

        dlg = DocImportDialog(self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        items = dlg.items()
        if not items:
            return
        top_z = self.doc.top_z()
        for it in items:
            it.z = top_z + 1
        cont = wrap_group(items, name="导入组")
        self._add_items([cont], "导入Word/Excel")
        for it in items:
            gi = self._gi_for(it)
            if gi is not None:
                gi.setSelected(True)
        self._after_change()

    # -- 绘制预览（供 canvas 调用；坐标 = 纸面 mm 浮点） --------------------

    def _clear_preview(self) -> None:
        if self._preview_item is not None:
            try:
                self.scene.removeItem(self._preview_item)
            except RuntimeError:
                pass
            self._preview_item = None

    def begin_draw(self, scene_pos) -> None:
        self._clear_preview()
        p = self._snap_pt(QtCore.QPointF(scene_pos))
        self._draw_start = p
        self._poly_pts = [p]

    def update_draw(self, scene_pos) -> None:
        self._clear_preview()
        s = self._draw_start
        if s is None or self._tool == TOOL_SELECT:
            return
        cur = self._snap_pt(QtCore.QPointF(scene_pos))
        # 预览统一：QGraphicsPathItem，无填充（只描边），避免出现"封闭面"
        path = QtGui.QPainterPath()
        if self._tool == TOOL_LINE:
            path.moveTo(s)
            path.lineTo(cur)
        elif self._tool == TOOL_RECT:
            path.addRect(QtCore.QRectF(s, cur).normalized())
        elif self._tool == TOOL_CIRCLE:
            path.addEllipse(QtCore.QRectF(s, cur).normalized())
        elif self._tool in (TOOL_POLY, TOOL_PENCIL):
            pts = self._poly_pts + [cur]
            if len(pts) >= 2:
                path.moveTo(pts[0])
                for p in pts[1:]:
                    path.lineTo(p)
        pen = QtGui.QPen(QtGui.QColor("#e60"))
        pen.setWidthF(0)
        item = QtWidgets.QGraphicsPathItem(path)
        item.setPen(pen)
        item.setBrush(QtCore.Qt.NoBrush)  # 关键：不填充
        self._preview_item = item
        self.scene.addItem(item)

    def finish_draw(self, scene_pos) -> None:
        self._clear_preview()
        s = self._draw_start
        if s is None:
            return

        def to_paper(p):
            # 场景 ≡ 纸面 y-up（§2.1）：无翻转，经 coords 恒等换算
            return machine_from_paper((float(p.x()), float(p.y())))

        cur = self._snap_pt(QtCore.QPointF(scene_pos))
        paths = []
        if self._tool == TOOL_LINE:
            paths = [[to_paper(s), to_paper(cur)]]
        elif self._tool == TOOL_RECT:
            r = QtCore.QRectF(s, cur).normalized()
            c = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft(),
                 r.topLeft()]
            paths = [[to_paper(p) for p in c]]
        elif self._tool == TOOL_CIRCLE:
            r = QtCore.QRectF(s, cur).normalized()
            cx, cy = r.center().x(), r.center().y()
            rx, ry = r.width() / 2, r.height() / 2
            pts = []
            for k in range(49):
                a = 2 * math.pi * k / 48
                pts.append(to_paper(QtCore.QPointF(cx + rx * math.cos(a),
                                                   cy + ry * math.sin(a))))
            paths = [pts]
        elif self._tool in (TOOL_POLY, TOOL_PENCIL):
            pts = list(self._poly_pts)
            if pts and (pts[-1].x() != cur.x() or pts[-1].y() != cur.y()):
                pts.append(cur)
            paths = [[to_paper(p) for p in pts]] if len(pts) >= 2 else []
        self._draw_start = None
        self._poly_pts = []
        if paths:
            it = Item(paths=paths, pos=(0.0, 0.0),
                      name={"line": "直线", "rect": "矩形", "circle": "圆",
                            "poly": "折线", "pencil": "自由笔"}.get(self._tool, "图元"),
                      z=self.doc.top_z() + 1)
            # 绘制工具直接产纸面 y-up：跳过①②，但走③归一（§2.2）
            normalize_local(it)
            self._add_items([it], "绘制")

    def add_poly_point(self, scene_pos) -> None:
        self._poly_pts.append(self._snap_pt(QtCore.QPointF(scene_pos)))

    def cancel_draw(self) -> None:
        """取消当前绘制（切工具/右键）。"""
        self._clear_preview()
        self._draw_start = None
        self._poly_pts = []

    def _clear(self) -> None:
        if self.doc.items:
            self._undo.push(ClearCommand(self))

    def _fit(self) -> None:
        self.view.fit()

    def _zoom_in(self) -> None:
        self.view.set_zoom(self.view.px_per_mm() * 1.25)

    def _zoom_out(self) -> None:
        self.view.set_zoom(self.view.px_per_mm() / 1.25)

    def _zoom_100(self) -> None:
        self.view.set_zoom(1.0)

    # -- 双击重编文字 / 重追图片 --------------------------------------------

    def edit_text_item(self, item: Item) -> None:
        if item.text_spec:
            self._add_text(edit_item=item)

    def retrace_image_item(self, item: Item) -> None:
        """图片**就地重追**（FR-10 / US-5）：调阈值/模式后重出线条。

        - 源图从 ``item.image_spec['source']`` 取（FR-10：记 spec 就不用
          重新导入重摆）；
        - 走 :class:`RetraceImageCommand`（显式 ``rebuild_path`` 通道，坑 2）；
        - **pos / scale / angle_deg 保持不变**（只换 paths + spec）。
        """
        spec = item.image_spec or {}
        source = spec.get("source")
        if not source or not Path(source).exists():
            QtWidgets.QMessageBox.warning(
                self, "无法重追", f"源图片不存在或未记录：{source or '（无）'}")
            return
        params = self._ask_image_params(default_source=source, spec=spec)
        if params is None:
            return
        mode, threshold, use_multi, target_mm, low, high = params
        try:
            svg = self._trace_image(source, mode=mode, threshold=threshold,
                                    use_multi=use_multi, target_mm=target_mm,
                                    low=low, high=high)
            # 走组导入契约①②③（y 翻转 + 锚点归位 + 归一）—— 与**加图**同一
            # 函数：重追只换 paths 不动 pos/scale/angle，坐标系必须与导入时
            # 逐点一致，否则同参数重追就会整体翻转/平移（切错位置）。
            paths = _image_paths_to_paper(_svg_to_paths(svg))
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "重追失败", str(exc))
            return
        if not paths:
            QtWidgets.QMessageBox.warning(self, "无线条", "新参数下没有可追踪的线条")
            return
        new_spec = self._image_spec(
            source, mode=mode, threshold=threshold, use_multi=use_multi,
            target_mm=target_mm, low=low, high=high)
        self._undo.push(RetraceImageCommand(self, item, paths, new_spec,
                                            "重追图片"))

    # -- 导出 --------------------------------------------------------------

    def to_job_spec(self):
        """导出当前版面为 :class:`~megapro.gui.job.JobSpec`（阶段 3 导出链）。

        ``flatten_visible``（z 升序拍平，跳 hidden/<2 点）+ ``Placement(mode=
        'preserve')`` —— 版面坐标即工件坐标，**排版直传不做 anchor 归位**
        （§2.2/§10 unclear #4）。纯数据拷贝，不消费/不清空源文档。
        """
        from megapro.gui.job import JobSpec, Placement
        from megapro.gui.layout.model import flatten_visible

        self._sync_models()
        return JobSpec(
            paths_paper=flatten_visible(self.doc),
            source_name="排版版面",
            placement=Placement(mode="preserve"),
        )

    def _on_save_svg(self) -> None:
        self._sync_models()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "另存为 SVG", "layout.svg", "SVG (*.svg);;所有文件 (*)")
        if not path:
            return
        if not path.lower().endswith(".svg"):
            path += ".svg"
        svg = document_to_svg(self.doc)
        try:
            Path(path).write_text(svg, encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.status_message.emit(f"已另存为 {path}")

    def _on_export(self) -> None:
        self._sync_models()
        # 越界预检：走 :meth:`_page_box`（含祖先链）—— 裸 ``it.page_bbox()`` 在
        # 非恒等容器下对组内叶子报局部坐标 → 编组后漏报（M1 缺口 #1）。
        # ⚠ 已知口径（不在本里程碑范围）：此处**只警告**，随后无条件
        # ``export_requested.emit`` —— 从不真正拦截越界导出。
        for it, _chain in iter_units(self.doc.items, visible_only=True):
            x0, y0, x1, y1 = it.unit_page_bbox(_chain)
            if x0 < 0 or y0 < 0 or x1 > BED_W or y1 > BED_H:
                QtWidgets.QMessageBox.warning(
                    self, "越界",
                    f"图元「{it.name}」超出 210×210 床面 "
                    f"(bbox {x0:.0f},{y0:.0f}-{x1:.0f},{y1:.0f})。仍导出？",
                )
                break
        self.export_requested.emit(self.to_job_spec())


class _TextDialog(QtWidgets.QDialog):
    """文字编辑对话框（新增/重编共用）。"""

    def __init__(self, parent, edit_item: Item | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑文字" if edit_item else "添加文字")
        spec = (edit_item.text_spec or {}) if edit_item else {}
        form = QtWidgets.QFormLayout(self)
        self._text = QtWidgets.QPlainTextEdit(spec.get("text", "写字机测试"))
        self._text.setFixedHeight(80)
        form.addRow("文字:", self._text)
        self._mode = QtWidgets.QComboBox()
        self._mode.addItem("轮廓空心字（大字号清晰）", "outline")
        self._mode.addItem("单线手写体（可小字）", "single")
        if spec.get("mode") == "single":
            self._mode.setCurrentIndex(1)
        form.addRow("模式:", self._mode)
        self._font = QtWidgets.QComboBox()
        self._font_paths = list_fonts()
        for name, _path in self._font_paths:
            self._font.addItem(name)
        # 选中已用字体
        cur = spec.get("font_path")
        for i, (_n, p) in enumerate(self._font_paths):
            if p == cur:
                self._font.setCurrentIndex(i)
                break
        form.addRow("字体:", self._font)
        self._size = QtWidgets.QDoubleSpinBox()
        self._size.setRange(2, 100)
        self._size.setValue(float(spec.get("size_mm", 15.0)))
        form.addRow("字号(mm):", self._size)
        self._cgap = QtWidgets.QDoubleSpinBox()
        self._cgap.setRange(-5, 20)
        self._cgap.setValue(float(spec.get("char_gap_mm", 0.0)))
        form.addRow("字距(mm):", self._cgap)
        self._lgap = QtWidgets.QDoubleSpinBox()
        self._lgap.setRange(0, 50)
        self._lgap.setValue(float(spec.get("line_gap_mm", 2.0)))
        form.addRow("行距(mm):", self._lgap)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def text(self) -> str:
        return self._text.toPlainText()

    def spec(self) -> dict:
        idx = self._font.currentIndex()
        path = self._font_paths[idx][1] if 0 <= idx < len(self._font_paths) else None
        return {"text": self.text(), "mode": self._mode.currentData(),
                "font_path": path, "size_mm": self._size.value(),
                "char_gap_mm": self._cgap.value(),
                "line_gap_mm": self._lgap.value()}

    def generate_paths(self):
        s = self.spec()
        if s["mode"] == "single":
            data = _DATA_DIR / "chinese_hershey_heiti.json"
            svg = text_singleline_svg(s["text"], data_path=data,
                                      size_mm=s["size_mm"],
                                      char_gap_mm=s["char_gap_mm"],
                                      fallback_font=s["font_path"] or find_cjk_font())
        else:
            svg = text_outline_svg(s["text"], font_path=s["font_path"],
                                   size_mm=s["size_mm"],
                                   line_gap_mm=s["line_gap_mm"],
                                   char_gap_mm=s["char_gap_mm"])
        return _svg_to_paths(svg)
