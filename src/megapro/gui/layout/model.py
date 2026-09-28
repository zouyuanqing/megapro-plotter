"""排版文档模型（纯逻辑，无 Qt）。

图元 = 一个「折线集合」+ 变换（位置/缩放/旋转）；文档 = 图元列表 + 床尺寸。
paint 与导出同源于此模型 —— QGraphicsScene 只做展示；导出时把变换拍平进
绝对坐标。

坐标契约（docs/preview-layout-blueprint.md §2.1/§2.2，阶段 2 改写）：
- ``Item.paths`` = **本地纸面 y-up mm**（消灭旧「y 可上可下」歧义）；
  入库归一：内容 bbox 左下 = 本地 (0,0)（:func:`normalize_local`，**只在
  创建/导入入口调用**，model/export 内部不自动跑 —— 直接构造 Item 的用例
  不受影响）。
- ``pos`` = 本地 (0,0) 的页面位置；旋转/缩放支点 = 本地 (0,0)。
- ``transformed_paths`` 数学**不动**（test_gui_layout.py 的 transform golden
  保持）：``rx = x1·cosθ − y1·sinθ + px``，即 y-up 下 +θ = 纸面逆时针。

模型树（``docs/PRD_layout_model_tree.md`` FR-01/FR-02/FR-07，M1）：
- **多页（FR-09，M4）**：:class:`Document` 持 ``pages: list[Page]`` +
  ``current``，状态真正存放在 :class:`Page`（items/bed_w/bed_h）；Document 的
  ``items``/``bed_w``/``bed_h``/``add``/``remove``/``contains``/
  ``items_visible``/``top_z``/``bottom_z``/``group_items``/``ungroup`` 等
  **全部委托当前页**（当前页门面）。所有消费点因此自动作用于当前页、无需改
  动 —— 这是「``export_svg.py`` 零改动」在该门面下成立的**前提**（若门面
  不满足，则须改 ``export_svg`` 并解冻 Document 级测试）。默认单页行为
  **逐位不变**（``Document()`` 即一个默认页；``Document(items=[...])``
  关键字构造保留）。
- ``Item.children`` 非空即**容器**；叶子仍是「折线 + 变换」，无 children 的
  Item 数学**逐位不变**（既有 transform/roundtrip golden 钉死）。
- 合成 = **父∘子**（子路径先过自身变换，再进父变换），唯一实现在
  :func:`_apply_transform`；``Item.transformed_paths``（递归）与
  ``flatten_visible``（祖先链）共用它，不各写一套。产品路径上容器变换
  **恒为恒等**（组变换走叶子集合，FR-04/FR-05 裁决），一般式是防御性 pin ——
  防任何一条几何链按恒等特化而漏掉父变换项。
- 拍平按**单元自身 z 全局升序（稳定）**；容器 z 只作用于**容器自身折线**的
  单元，不参与子项排序（单一容器 z 无法再现 z 不连续编组的交错）。
- 容器**自身 paths** 也是几何（可为空但不丢）；``bbox``/``page_bbox``/拍平/
  枚举面（``items_visible``/``sorted_items``）一律同一口径走全树，不留只看
  顶层的残面。
- **画布侧硬约束（M2 必须兑现，M1 只立契约）**：容器**不是渲染单元** ——
  ``paths`` 为空 ⇒ ``PathItem`` 画不出任何东西，但 ``bbox()`` 递归后非零 ⇒
  ``canvas/items.py`` 的 ``_local_rect``/``shape()`` 会造出一个覆盖整组的
  **隐形可点矩形**（实测 ``PathItem(g).path().isEmpty()=True`` 而
  ``shape().isEmpty()=False``）。故场景项必须按 :func:`iter_leaves` 建
  （**须显式 ``visible_only=False``**，见下「iter_leaves 默认值」）。

  ⚠ **「跳过容器」不够，只跳过容器会双重切割**（M1 评审实测，**M2 已修**）：
  叶子在容器里时**不在** ``doc.items`` 顶层列表中（身份判定），故旧版
  ``canvas/undo_cmds.py:make_gi`` 的 ``if item not in page.doc.items:
  page.doc.add(item)`` 会把**每一个组内叶子**当成新图元追加成第二个顶层 Item。
  实跑：``doc.items = ['G','a','b']``、``flatten_visible`` 吐 4 条折线而真实几何
  单元只有 2 条 —— **同一几何被切两遍**，对切纸机即重复下刀。现版把**渲染单元**
  与**模型归属**分开：
  - 渲染：按 :func:`iter_leaves` 建 ``PathItem``，容器跳过（``make_gi`` 对
    ``is_container()`` 返回 ``None``）；
  - 入模：``doc.add`` 只在**确无归属**时执行，判据用
    :meth:`Document.contains_anywhere`（**全文档**身份查找），**不是**
    「是否在顶层列表」、也**不是**当前页的 ``contains``（A1：页删除/重排后
    命令归属页漂移，当前页 ``contains`` 会把「挂在别页的对象」误判为
    「不在文档里」→ ``doc.add`` 挂成第二份 → 同一几何切两遍）。

  组变换只写叶子集合（FR-04/FR-05），容器变换恒为恒等。
- **镜像（FR-08）**：`mirror_x``/``mirror_y`` 布尔，**支点 = 本地 bbox 中心**
  （一般式，v1.3 定稿，**不依赖** ``normalize_local`` 的 ``y0==0`` 不变量 ——
  它只在创建/导入入口调用，直接构造的 Item 同样要正确），``pos``/``scale``/
  ``angle_deg`` **不补偿**（原位镜像 ⇒ bbox 稳定、位置不动）。合成顺序
  **mirror → scale → rotate → translate**（:func:`_apply_transform` 首个分支）。
  镜像数学只在 :func:`coords.mirror_scalar`（复用唯一翻转实现
  :func:`coords.flip_y_scalar`），**本文件不写任何翻转算术**
  （``tests/test_coords.py`` 全树扫描口径）。未设镜像时数学**逐位**不变。
  - **退化跨度必须逐轴挡**（该轴宽/高为 0，如竖直线的水平镜像）= **该轴**无
    镜像轴 ⇒ 该轴不镜像（:func:`_axis_mirrors`）；不挡的话 ``mirror_scalar(v,v,v)``
    会退化成绕原点反射、把图形搬到负半轴。**一轴退化绝不得牵连另一轴** ——
    曾用单个 ``None`` 门控整项，导致「竖直线 + 水平&垂直双标志」时 y 的镜像被
    连带静默丢弃（flag=True 而几何未镜像，且随导出/JobSpec 一路传下去）；
    触发路径是常规操作（工具条两个独立按钮连点）。:func:`_mirror_span` 现只在
    **两标志皆未置位**时返回 None。
  - 画布侧：镜像**必须**烘进画笔路径（:meth:`Item.local_paths` →
    ``PathItem.rebuild_path``），因为 ``apply_model_state`` 只应用
    pos/scale/rotation、**不重建** QPainterPath ⇒ 只改标志画布纹丝不动。
    setattr 通道（``ChangeItemPropsCommand``）据此按 ``_GEOMETRY_FIELDS``
    额外 ``rebuild_path()``。
- **删除/复原的归属契约**：:meth:`Document.remove` **组感知**（按身份定位 owning
  container 并从其 children 摘除）并返回 :class:`DetachInfo`（原容器 + 原下标），
  :meth:`Document.attach` 为其逆。**两者必须成对使用**：只组感知而不把归属信息
  带回 undo 侧，场景重建会把摘下的叶子 ``doc.add`` 成顶层项 → **组被静默解散**
  （实测：摘除 a 后重建得到 ``doc.items=['G','a']``、``G.children=['b']``，无异常）。
  原「抛 ValueError」方案已推翻：它让 ``QUndoStack`` 半污染（命令入栈、模型未改）
  且 Ctrl+Z 后几何翻倍，比静默更差。**M2 已接线**：``RemoveItemsCommand`` /
  ``ClearCommand`` 收集回执、undo 侧 ``attach`` 回原容器；
  ``Document.attach`` 的顶层分支也已改为**按 index 插入**（旧实现忽略 index、
  恒追加到末尾 ⇒ 顶层序无法复原）。
- **``page_bbox()`` 不走祖先链 —— M2 已补正消费侧**。:meth:`Item.page_bbox` 只过
  自身变换，而 :func:`flatten_visible` 过祖先链 —— 对**带非恒等变换的容器下的
  叶子**，两者给出不同的页面坐标。实跑：
  ``leaf(pos=(10,20))`` 在 ``container(pos=(100,0))`` 下，``flatten`` 给
  ``(110,20)``，``leaf.page_bbox()`` 给 ``(10,20)``。``Item`` 无 ``parent``
  反向指针，故本方法本身**仍只含自身变换**（改它须加 parent 字段，超边界）；
  **M2 的做法是不改它、而是给消费侧一条带祖先链的通道**：
  :func:`iter_units`（产出 ``(item, chain)``）+ :meth:`Item.unit_page_bbox` /
  :func:`unit_paths`。越界预检（``layout_page._on_export``）、组框 overlay、
  对齐/分布、多选数值、吸附候选**全部改走新通道** ⇒ 「导出切了 / 预检没报」
  的分叉消除（M2 用例钉住 ``unit_page_bbox`` 给 (110,20) 而裸 ``page_bbox``
  给 (10,20)）。容器恒等时（当前产品路径）两者逐位相同。
- **``iter_leaves`` 默认值**：``visible_only`` 默认 **False**（枚举面要的是
  「全部渲染单元」，可见性过滤是 :func:`flatten_visible` 的活）。默认 True 会
  与 ``make_gi`` 的 ``gi.setVisible(item.visible)``（:51）冲突：隐藏叶子的
  ``PathItem`` 永不创建，此后 ``_gi_for(hidden)`` 恒 ``None``、``_sync_gi``
  恒空操作，**隐藏→显示切换永远画不出来**（M1 评审实测 ``iter_leaves([G])``
  对唯一隐藏叶子返回 ``[]``）。
- **树的无环/深度保护**：所有递归遍历（:func:`iter_items` /
  :func:`iter_flattens` / :meth:`Item.bbox` / :meth:`Item.transformed_paths`）
  共享 :data:`MAX_TREE_DEPTH` 上限，越限抛 :class:`ValueError`（原为
  ``RecursionError``，不可诊断）。PRD §11 问题 1 问「是否需要深度上限」——
  定稿为**需要**：FR-03① 的验收是「对**任意选择**编组/解组前后恒等」，
  而任意选择包含「组选自己」，无保护即成环。组入口的「待编组集合 ∩ 新容器
  子树 = ∅」自含拒绝属 M2（FR-03/T3）。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from megapro.gui.canvas.coords import BED_H, BED_W, mirror_scalar

__all__ = [
    "BED_W",
    "BED_H",
    "Item",
    "Document",
    "DetachInfo",
    "Polyline",
    "iter_items",
    "iter_leaves",
    "iter_flattens",
    "iter_ancestors",
    "iter_units",
    "unit_paths",
    "effectively_visible",
    "normalize_local",
    "flatten_visible",
    "MAX_TREE_DEPTH",
]

Polyline = list[tuple[float, float]]

#: 模型树递归深度上限（PRD §11 问题 1 定稿为「需要」）。
#: 成环（组把自身选进子集，FR-03① 的「任意选择」包含之）在无保护时是
#: ``RecursionError``；越限改为可诊断的 :class:`ValueError`。
MAX_TREE_DEPTH = 64


def _check_depth(depth: int) -> None:
    """递归深度守卫（所有递归遍历共用一份，见模块 docstring「树的无环/深度保护」）。"""
    if depth > MAX_TREE_DEPTH:
        raise ValueError(
            f"模型树深度超过 {MAX_TREE_DEPTH}（疑似成环：编组不得把组选进"
            "自身子集，或移动/删除时产生了环）")


def _mirror_span(item: Item) -> tuple[float, float, float, float] | None:
    """镜像支点跨度 = ``item`` 的**本地 bbox**（FR-08 v1.3：支点=中心，一般式）。

    不依赖 ``normalize_local`` 的 ``y0 == 0`` 不变量（它只在创建/导入入口调用，
    直接构造的 Item 同样正确）。

    只在**两个标志都未置位**时返回 None（整项免镜像的快路径）；置位时**一律
    返回 bbox**，退化与否由**每个轴各自**判定（:func:`_axis_mirrors`）：

    **退化跨度必须逐轴挡，不能整项挡**。一条竖直线（``x0 == x1``）没有水平
    镜像轴，但若同时置了 ``mirror_y``，它的垂直镜像**依然要生效** ——
    整项返回 None 会把 y 的镜像连带静默丢弃，造成「flag=True 而几何未镜像」
    的分叉，并随导出/JobSpec 一路传下去。触发路径是常规操作：工具条是两个
    独立按钮，连点「水平镜像 + 垂直镜像」即触发。
    """
    if not (item.mirror_x or item.mirror_y):
        return None
    return item.bbox()


def _axis_mirrors(item: Item, span: tuple | None) -> tuple[bool, bool]:
    """该图元**每轴**是否真正镜像 = ``(mirror_x 生效, mirror_y 生效)``。

    逐轴判据（FR-08 v1.3：退化跨度 = **该轴**无镜像轴 ⇒ 该轴不镜像）：
    ``mirror_x and x1 > x0`` / ``mirror_y and y1 > y0``。

    退化轴不镜像的原因：``mirror_scalar(v, v, v)`` 会退化成**绕原点反射**
    （``-v``），把图形搬到负半轴；而不镜像才是对的 —— 竖直线的水平镜像、
    水平线的垂直镜像本就是恒等。
    """
    if span is None:
        return (False, False)
    x0, y0, x1, y1 = span
    return (bool(item.mirror_x and x1 > x0), bool(item.mirror_y and y1 > y0))


def _apply_transform(item: Item, path: Polyline,
                     *, span: tuple | None = None) -> Polyline:
    """一条折线过 ``item`` 的变换：
    **镜像 → 缩放 → 旋转(绕 pos) → 平移 pos**（FR-08 合成顺序）。

    **合成数学的唯一实现**（FR-01 父∘子）：叶子取自身变换、容器按祖先链
    由内到外逐层套用，两条路径共用本函数，故 ``Item.transformed_paths`` 与
    ``flatten_visible`` 永不各写一套（导出/预览/送作业三链同源）。

    镜像数学在 :func:`coords.mirror_scalar`（复用唯一翻转实现），**本文件不写
    任何翻转算术**（``tests/test_coords.py`` 全树扫描口径）。未设镜像时
    ``mirror_x/mirror_y`` 皆 False ⇒ 本函数**逐位**走原数学（既有 golden 不变）。

    ``span`` = 镜像支点跨度，由调用方**每 item 算一次**传入 —— 否则每条折线
    都重算一次 ``bbox()``，千段图元退化成 O(n²)。
    """
    px, py = item.pos
    s = item.scale
    rad = math.radians(item.angle_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    if span is None:
        span = _mirror_span(item)
    # 逐轴判定：退化轴不镜像，另一轴照常（M2/M3 评审修正：不得整项门控）
    mirror_x, mirror_y = _axis_mirrors(item, span)
    x_lo, y_lo, x_hi, y_hi = span if span is not None else (0.0, 0.0, 0.0, 0.0)
    pts = []
    for x, y in path:
        # 本地 → 镜像(绕本地 bbox 中心) → 缩放 → 旋转(绕 pos) → 平移
        if mirror_x:
            x = mirror_scalar(x, x_lo, x_hi)
        if mirror_y:
            y = mirror_scalar(y, y_lo, y_hi)
        x1, y1 = x * s, y * s
        rx = x1 * cos - y1 * sin + px
        ry = x1 * sin + y1 * cos + py
        pts.append((rx, ry))
    return pts


@dataclass(eq=False)
class Item:
    """一个排版图元：若干折线（本地纸面 y-up mm）+ 变换；或一组子图元（容器）。

    约定：paths 坐标为「本地」纸面 y-up 坐标（创建/导入入口经
    :func:`normalize_local` 归一：内容 bbox 左下 = 本地 (0,0)）；
    pos = 本地 (0,0) 的页面(工件)位置；scale = 整体缩放；angle_deg =
    旋转（绕 pos，y-up 下逆时针为正）。
    导出时：本地 → 缩放 → 旋转(绕 pos) → 平移 pos → 页面坐标。

    **相等语义 = 对象身份**（``eq=False``）：``Item`` 是可变模型对象，
    结构相等（两个字段全同的图元/容器）在编辑语义上是**两个不同图元** ——
    删一个不能删掉另一个（``Document.remove``、``make_gi`` 的
    ``item not in page.doc.items`` 都按身份判定）。

    **容器**（``children`` 非空）：几何 = 自身折线 ∪ 子项几何按**父∘子**递归
    合成。容器只承载结构；产品路径上容器变换恒为恒等（组变换走叶子集合）。
    """

    paths: list[list[tuple[float, float]]] = field(default_factory=list)
    pos: tuple[float, float] = (0.0, 0.0)  # 页面/工件 mm
    scale: float = 1.0
    angle_deg: float = 0.0
    name: str = "item"
    z: float = 0.0  # 层序（大=上）；容器 z 只作用于容器自身折线，不排子项
    locked: bool = False  # 锁定（不可选中/移动）
    visible: bool = True  # 隐藏则导出/显示都跳过（含整棵子树）
    text_spec: dict | None = None  # 文字源信息（可重编）：见 text_to_svg
    # 图片源参数（可重追，FR-10）：``{source, mode, threshold, multi,
    # target_mm, low, high, aperture_size}``。仿 ``text_spec`` 先例（D6）——
    # 记下产线参数即可**就地重追**，不必重新导入重摆。
    image_spec: dict | None = None
    children: list["Item"] = field(default_factory=list)  # 非空即容器
    # 镜像（FR-08）：支点 = **本地 bbox 中心**（一般式，v1.3 定稿），
    # pos/scale/angle **不补偿**（原位镜像：内容绕自身中心像点翻转，仅标志
    # 变更 ⇒ bbox 稳定、页面位置不变）。数学在 coords.mirror_scalar（复用唯一
    # 翻转实现 flip_y_scalar），本文件不写任何翻转算术。
    mirror_x: bool = False
    mirror_y: bool = False

    def is_container(self) -> bool:
        """容器 = ``children`` 非空（FR-01 术语）。

        画布侧的跳过判据：容器**不建 PathItem** —— 它自身无折线可画，而
        :meth:`bbox` 递归后非零，会在画布上留下隐形可点的命中矩形（见模块
        docstring「画布侧硬约束」；M2 在 ``make_gi`` 兑现）。
        """
        return bool(self.children)

    def bbox(self, *, _depth: int = 0) -> tuple[float, float, float, float]:
        """本地 bbox (x0,y0,x1,y1)（未含自身变换）。空 → (0,0,0,0)。

        容器：自身折线与**子项几何（已含子项自身变换、不含本项变换）**的并集
        —— 即子项组合进本项局部系后的范围。
        """
        _check_depth(_depth)
        xs = [x for p in self.paths for x, _ in p]
        ys = [y for p in self.paths for _, y in p]
        for ch in self.children:
            for p in ch.transformed_paths(_depth=_depth + 1):
                xs.extend(x for x, _ in p)
                ys.extend(y for _, y in p)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def transformed_paths(self, *, _depth: int = 0
                          ) -> list[list[tuple[float, float]]]:
        """把变换(缩放+旋转+平移)拍平进坐标，返回页面/工件坐标的折线。

        容器：自身折线与**子项几何（子项已过自身变换 ⇒ 落在本项局部系）**各自
        再过本项变换 —— 一般式 父∘子（FR-01 ② 防御性 pin；产品路径上本项变换
        恒为恒等时逐点等于子项现状几何）。``flatten_visible`` 走同一条合成链
        （:func:`iter_flattens` 携祖先链），二者不得各写一套。
        """
        _check_depth(_depth)
        span = _mirror_span(self)
        out = [_apply_transform(self, p, span=span) for p in self.paths]
        for ch in self.children:
            out.extend(_apply_transform(self, p, span=span)
                       for p in ch.transformed_paths(_depth=_depth + 1))
        return out

    def page_bbox(self) -> tuple[float, float, float, float]:
        """变换后的页面 bbox —— **只含自身变换，不含祖先链**（已知缺口，见模块
        docstring「``page_bbox()`` 不走祖先链」）。

        对**带非恒等变换的容器下的叶子**，本方法给的不是页面坐标：实跑
        ``leaf(pos=(10,20))`` 在 ``container(pos=(100,0))`` 下，
        ``flatten_visible`` 给 ``(110,20)`` 而本方法给 ``(10,20)``。
        ``Item`` 无 ``parent`` 反向指针，本方法无法走祖先链 —— **M2 的消费侧
        必须改用** :func:`iter_units` / :meth:`unit_page_bbox`（自带祖先链），
        不可再直接吃本方法。

        **无 children 的叶子语义逐位不变**（既有 golden 钉死）：其祖先链为空，
        自身变换即页面变换。
        """
        paths = self.transformed_paths()
        xs = [x for p in paths for x, _ in p]
        ys = [y for p in paths for _, y in p]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def local_paths(self) -> list[Polyline]:
        """**本地**折线（含镜像、不含 scale/rotate/translate）—— 画布绘制用。

        :meth:`transformed_paths` 拍平了**全部**变换（镜像→缩放→旋转→平移），
        而 :class:`~megapro.gui.canvas.items.PathItem` 的场景变换由 Qt 承担
        （``setPos``/``setScale``/``setRotation``，支点 = 本地 (0,0)），故它
        需要的是「只烘了镜像」的本地坐标。

        FR-08：镜像**必须**烘进画笔路径 —— 只改 ``mirror_x/mirror_y`` 标志而
        不重画，场景上什么都不会变（``apply_model_state`` 只应用
        pos/scale/rotation，见 ``canvas/items.py``）。镜像绕**本地 bbox 中心**，
        故 ``boundingRect`` 稳定。

        未设镜像时**逐位等于** ``item.paths``。两轴**各自**判退化
        （:func:`_axis_mirrors`）—— 一轴退化不得牵连另一轴。
        """
        span = _mirror_span(self)
        mirror_x, mirror_y = _axis_mirrors(self, span)
        if not (mirror_x or mirror_y):
            return [list(p) for p in self.paths]
        x_lo, y_lo, x_hi, y_hi = span
        out: list[Polyline] = []
        for poly in self.paths:
            out.append([(mirror_scalar(x, x_lo, x_hi) if mirror_x else x,
                        mirror_scalar(y, y_lo, y_hi) if mirror_y else y)
                       for x, y in poly])
        return out

    def unit_page_bbox(self,
                       chain: tuple["Item", ...] = ()) -> tuple[float, float, float, float]:
        """本单元的**页面系** bbox —— 含祖先链（``chain`` 根在前）。

        M2 修 M1 缺口 #1（``page_bbox()`` 不走祖先链）而设：越界预检
        （``layout_page._on_export``）、组框 overlay、对齐/分布的 bbox 全部
        改吃本方法，故「导出切了 / 预检没报」的分叉消除。

        祖先链由 :func:`iter_units` 携带；``chain=()`` 时与 :meth:`page_bbox`
        逐位相同（叶子文档、恒等容器 —— 当前产品路径）。
        """
        paths = unit_paths(self, chain)
        xs = [x for p in paths for x, _ in p]
        ys = [y for p in paths for _, y in p]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def descendants(self) -> list["Item"]:
        """自身 + 全部后代（DFS，前序）。含自身便于「组选中 = 整棵子树」判定。"""
        out = [self]
        for ch in self.children:
            out.extend(ch.descendants())
        return out


@dataclass(frozen=True)
class DetachInfo:
    """:meth:`Document.remove` 的归属回执 —— undo 复原组结构所需的全部信息。

    ``owner`` = 原 owning container（顶层图元为 ``None``）；``index`` = 在
    ``owner.children``（或顶层 ``items``）中的原下标。undo 侧调
    :meth:`Document.attach` 即可原样复原，避免「叶子变顶层项、组被静默解散」。

    ⚠ **``index`` 是「本次删除前、已删项塌陷之后」的位置，不是原列表下标**
    （A3 修的就是这一条）。一次删多项时回执逐个产生，后面的下标已被前面的
    删除压过 ⇒ 脱离上下文单独使用必然错位。消费方**必须逆删除序**回插
    （见 :class:`~megapro.gui.canvas.undo_cmds.RemoveItemsCommand._do_undo`
    的论证）：轮到某项时列表已还原成「原列表 − 更早被摘的那些」，与它被摘下
    那一刻的列表相同，故该下标恰好是它的正确落点。正序回插则得到原顺序的一
    个置换 —— 几何条数不变、画布看不出来，但 ``document_to_svg`` 与删除前
    **逐字节**不同 ⇒ 送作业的切割次序变了。
    """

    owner: Item | None
    index: int


@dataclass
class Page:
    """一个排版页：图元列表 + 床尺寸（原点 (0,0) = 工件原点，床左下）。

    状态真正存放处；:class:`Document` 持 ``pages: list[Page]`` 并把
    ``items``/``bed_w``/``bed_h``/``top_z``… **委托当前页**（FR-09 当前页
    门面）。历史上这些字段直接在 :class:`Document` 上，现在下沉到本页 ——
    单页文档下两者逐位等价（``tests/test_gui_layout.py`` 全部 Document 级
    用例不改字通过 = 门面逐位不变的可执行证明）。
    """

    items: list[Item] = field(default_factory=list)
    bed_w: float = BED_W
    bed_h: float = BED_H
    name: str = "页1"

    def clone(self, *, name: str | None = None) -> "Page":
        """深拷贝一整页（复制页用：图元与子树全部新建，身份互不共享）。

        用 ``copy.deepcopy``（含 ``children`` 树）；``Item.__eq__=False``
        使 ``deepcopy`` 走 ``__reduce_ex__`` 默认路径、按对象图逐个新建，
        不会把两个页的图元混成同一批对象。
        """
        return Page(
            items=copy.deepcopy(self.items),
            bed_w=self.bed_w,
            bed_h=self.bed_h,
            name=self.name if name is None else name,
        )


    def remove(self, item: Item) -> "DetachInfo | None":
        """按**对象身份**删除图元（顶层或组内），返回**归属信息**供 undo 复原。

        **组感知**（M1 评审裁决，推翻原「抛 ValueError」方案）：组内叶子被
        定位到 owning container 后从其 ``children`` 摘除，而不是报错。理由是
        抛异常更差而非更好 —— 实跑 ``QT_QPA_PLATFORM=offscreen``：
        ``QUndoStack.push(RemoveItemsCommand(lp,[kid]))`` 触发 PySide6
        ``Error calling Python override of QUndoCommand::redo()``，但**命令
        已经进了栈**（``undo.count()==1``、``canUndo()==True``）而模型未改
        （几何仍在、切纸机照样下刀）；随后 Ctrl+Z 得到 ``doc.items=['G','kid']``
        且 flatten 吐**两条完全相同的折线** —— 几何翻倍。即抛异常把「静默错」
        换成「不可撤销的错」，非净改善。

        **归属信息是必需的，单独组感知仍不够**（M1 评审复现）：摘除后执行
        ``RemoveItemsCommand.undo`` 实际调用的 ``make_gi(lp, a)``，
        ``undo_cmds.py:54`` 的 ``if item not in page.doc.items`` 对刚被摘除的
        ``a`` 判为「不在」→ 走 ``page.doc.add(a)``，得到
        ``doc.items=['G','a']``、``G.children=['b']``：**叶子变成顶层项、组被
        静默解散，全程无异常**。几何还看得见，只是不再成组，用户更不易察觉。
        故本方法**返回** :class:`DetachInfo`（原容器 + 原下标），M2 的 undo
        侧据此 **re-attach 回原容器**（:meth:`Document.attach`），而不是让
        ``make_gi`` 走 ``doc.add``。

        不在文档里的对象静默忽略并返回 ``None``（既有契约：重复删不崩）。
        """
        for i, cur in enumerate(self.items):
            if cur is item:
                del self.items[i]
                return DetachInfo(owner=None, index=i)
        for owner in iter_items(self.items):
            if not owner.children:
                continue
            for i, ch in enumerate(owner.children):
                if ch is item:
                    del owner.children[i]
                    return DetachInfo(owner=owner, index=i)
        return None

    def attach(self, item: Item, *, owner: Item | None = None,
               index: int | None = None) -> None:
        """把图元挂回文档 —— :meth:`remove` 的逆操作（undo 侧复原用）。

        ``owner`` 给定则挂回该容器 ``children`` 的 ``index`` 位（组结构原样
        复原，**不会**把叶子变成顶层项）；``owner=None`` 则挂到顶层 ``items``。

        **顶层同样按 ``index`` 插入**（M2 编组修正）：``index=None`` 或越界时
        追加到末尾。旧实现顶层分支直接 ``append``、忽略 ``index`` ⇒ 编组容器
        总是落到顶层末尾、解组后子项也全被追加 —— 顶层序无法复原
        （``test_group_ungroup_preserves_flatten_pointwise_and_order`` 钉住：
        ``[a,c,b]`` 编组再解组必须回到 ``[a,c,b]`` 而非 ``[c,a,b]``）。
        """
        if owner is None:
            if any(cur is item for cur in self.items):
                return
            if index is None or not (0 <= index <= len(self.items)):
                self.items.append(item)
            else:
                self.items.insert(index, item)
            return
        if any(ch is item for ch in owner.children):
            return
        if index is None or not (0 <= index <= len(owner.children)):
            owner.children.append(item)
        else:
            owner.children.insert(index, item)

    def sorted_items(self) -> list[Item]:
        """全部**几何拥有者**（容器展开），按 z 升序（小 z 先画=在下层）。

        「几何拥有者」= 自身 ``paths`` 非空的图元：叶子恒是，带自身折线的容器
        也是（容器自身折线是一等几何，见 :func:`iter_flattens`）——枚举面必须
        与拍平面同口径，否则容器自身折线会从越界预检里消失。
        """
        return sorted(_own_geometry(self.items, visible_only=False),
                      key=lambda it: it.z)

    def items_visible(self) -> list[Item]:
        """可见**几何拥有者**（同上），按 z 升序。

        隐藏容器跳过整棵子树（组内成员逐个隐藏仍有效）。
        """
        return sorted(_own_geometry(self.items), key=lambda it: it.z)

    def contains(self, item: Item) -> bool:
        """全树身份查找。

        画布侧删除的**前置判定**用：必须先确认模型层能删（顶层 or 可组感知
        detach）再摘场景项，否则会留下「场景已删、模型未删」的半应用状态
        （M2/FR-04）。
        """
        return any(cur is item for cur in iter_items(self.items))

    def top_z(self) -> float:
        """全树最高 z（含容器自身折线单元的 z）—— 新图元 ``top_z() + 1`` 用。"""
        return max((it.z for it in iter_items(self.items)), default=0.0)

    def bottom_z(self) -> float:
        """全树最低 z（含容器自身折线单元的 z）。"""
        return min((it.z for it in iter_items(self.items)), default=0.0)

    # -- 编组 / 解组（FR-03，M2） -------------------------------------------

    def owner_of(self, item: Item) -> "Item | None":
        """图元的**直接** owning container（顶层图元返回 ``None``）。

        「谁持有它」= 它出现在谁（且仅谁）的 ``children`` 里（按身份）。顶层
        列表不是归属 —— 组内叶子不在 ``items`` 里却属于某容器。
        """
        for owner in iter_items(self.items):
            for ch in owner.children:
                if ch is item:
                    return owner
        return None

    def group_items(self, items, *, name: str = "组") -> Item | None:
        """把所选图元收进一个新容器（**几何恒等重组**，M2 FR-03①）。

        - **不做「容器 z 归位」**（PRD v1.3 删除该未定义操作）：容器恒为恒等
          变换（``paths=[]``/``pos=(0,0)``/``scale=1``/``angle_deg=0``），
          拍平按**全局叶子 z**，故 z 不连续的选择（0、2 夹 1）编组前后
          ``flatten_visible`` 含折线顺序**逐点恒等**。
        - 成员可为**任意 mix**：顶层散件 + 已存在的容器（组套组）。成员先按
          各自所在容器摘除（:meth:`remove` 的组感知），再挂进新容器。
        - **成环自含拒绝**：待编组集合若与任一成员子树有交集（把祖先选进自己
          的子集）→ 返回 ``None``（不抛异常，调用侧可静默忽略或提示）。
        - 成员不足 2 个 → 返回 ``None``（无意义的单元素组）。
        - 容器插入位置 = **第一个成员的原位置**（owner + 下标）。
        - **顶层列表序的复原范围**：**相邻**成员编组再解组 ⇒ 顶层序精确复原
          （undo 的强契约）；**非相邻**成员（如 ``a、b`` 中间夹 ``c``）解组后
          成员并到组槽位**成连续一段** —— 编组本身就把它们拉到了一起，顶层序
          不可复原。但**拍平（= 实际切割次序）两种情况都逐点恒等**，因为它按
          叶子 z 稳定排序、与顶层列表序无关。FR-03① 的验收契约是拍平恒等，
          故非相邻情形同样成立。

        返回新容器；未编组时返回 ``None``。
        """
        chosen: list[Item] = []
        seen: set[int] = set()
        for it in items:
            if id(it) in seen:
                continue
            seen.add(id(it))
            chosen.append(it)
        if len(chosen) < 2:
            return None
        # 自含拒绝：任一成员的**真**子树（不含自身）∩ 待编组集 ≠ ∅ → 成环
        chosen_ids = {id(it) for it in chosen}
        for it in chosen:
            for d in it.descendants():
                if d is it:
                    continue  # 自身不算「子树」——每个成员都含自己
                if id(d) in chosen_ids:
                    return None
        # 按当前文档顺序排序（顶层列表序，其次容器内序）→ 成员顺序稳定可预期
        order = {id(it): i for i, it in enumerate(iter_items(self.items))}
        chosen.sort(key=lambda it: order.get(id(it), 1 << 30))
        cont = Item(name=name, z=0.0, children=[])
        placements: list[DetachInfo] = []
        for it in chosen:
            info = self.remove(it)
            cont.children.append(it)
            if info is not None:
                placements.append(info)
        if not placements:
            # 成员都不在文档里（纯悬空选择）→ 不产生副作用，回滚
            cont.children = []
            return None
        first = placements[0]
        self.attach(cont, owner=first.owner, index=first.index)
        return cont

    def ungroup(self, container: Item) -> list[Item]:
        """解散容器：其 children **原地**提升到容器原位置（FR-03① 精确恢复）。

        返回被提升的子项列表（原顺序）；容器不是本树的容器 / 本身不持有子项
        时返回空列表并**不动模型**。容器被摘出后其 ``children`` 清空（它不再是
        组）。几何**逐位不变**（提升不碰任何成员字段）。

        ⚠ **本方法对容器是破坏性的，undo 侧必须自己复原 children**
        （A2 修的就是这条）。清空 ``container.children`` 是设计如此（摘出来
        之后它已不是组），但这意味着**光把容器 ``attach`` 回去并不能撤销解
        组** —— 那是「空组 + 孩子全被提为顶层」的错误结构（顶层多一个无子项
        幽灵容器）。要撤销必须按 **摘孩子 → 挂回 children → 挂容器** 三步，
        见 :class:`~megapro.gui.canvas.undo_cmds.UngroupCommand`（孩子快照
        取自本方法的返回值）。故本方法**返回 kids** 不是可选项而是契约。
        """
        if not container.is_container():
            return []
        if not any(cur is container for cur in iter_items(self.items)):
            return []
        kids = list(container.children)
        info = self.remove(container)
        if info is None:
            return []
        idx = info.index
        for ch in kids:
            if idx is None:
                self.attach(ch)
            else:
                self.attach(ch, owner=info.owner, index=idx)
                idx += 1
        container.children = []
        return kids


@dataclass
class Document:
    """排版文档：**多页**容器 + 当前页门面。原点 (0,0) = 工件原点（床左下）。

    **当前页门面（FR-09 v1.1 兼容迁移）**：``items``/``bed_w``/``bed_h``/
    ``top_z``/``bottom_z``/``add``/``remove``/``attach``/``contains``/
    ``items_visible``/``sorted_items``/``owner_of``/``group_items``/
    ``ungroup`` 全部**委托当前页**（:class:`Page`）。所有消费点
    （``export_svg`` 经 ``flatten_visible(doc)`` 读 ``doc.items``、
    ``undo_cmds.make_gi`` 的 ``doc.contains``、``ClearCommand.saved =
    list(doc.items)``、``layout_page`` 全文 ``self.doc.items``）因此**自动
    作用于当前页**，无需改动它们 —— 这正是「``export_svg.py`` 零改动」在该
    门面下成立的原因（设计约束：门面不满足则须改 ``export_svg`` 并解冻
    Document 级测试）。

    **默认单页行为逐位不变**：``Document()`` 即「一个默认页」，既有
    ``Document(items=[...])`` 关键字构造把那些图元放进该页
    （``tests/test_gui_layout.py`` 全部 Document 级用例不改字通过 =
    门面逐位不变的可执行证明）。

    ⚠ ``bed_w``/``bed_h`` 刻意**不**做成 property 转发：它们是 dataclass
    字段且被 ``Document(...)`` 构造使用。改为构造时同步进页
    （:meth:`_sync_page_bed`）、切页时同步回来。

    ⚠ **``contains`` 是当前页语义，「入模判据」要用 ``contains_anywhere``**
    （A1）：页被删后撤销命令的归属页会漂移/兜底到别的页，此时对象可能挂在
    **另一页** —— 用 ``contains``（当前页）判「不在」→ ``doc.add`` 把同一对象
    挂成第二份 ⇒ **同一几何切两遍**。两者分工见 :meth:`contains_anywhere`。
    """

    pages: list[Page] = field(default_factory=lambda: [Page()])
    current: int = 0
    bed_w: float = BED_W
    bed_h: float = BED_H
    #: 兼容构造：``Document(items=[...])``（既有测试与 ``layout_page`` 依赖）。
    #: 非 None 时这些图元进当前页。**不是** dataclass 字段（``items`` 是
    #: property，同名字段会冲突），只在本类自定义 ``__init__`` 里消费。
    _init_items: list[Item] | None = field(default=None, repr=False)

    def __init__(self, pages: list[Page] | None = None, current: int = 0,
                 bed_w: float = BED_W, bed_h: float = BED_H,
                 items: list[Item] | None = None) -> None:
        """**自定义构造**（不再用 dataclass 生成的 ``__init__``）。

        原因：``items`` 被做成了 :attr:`items` property（当前页门面），而
        dataclass 字段不能与 property 同名。既有代码/测试广泛使用
        ``Document(items=[...])``，故在此显式接受该关键字并放进当前页 ——
        单页文档下与旧行为**逐位相同**。
        """
        self.pages = list(pages) if pages else [Page()]
        self.current = int(current)
        self.bed_w = float(bed_w)
        self.bed_h = float(bed_h)
        self._init_items = list(items) if items is not None else None
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.pages:  # Document(pages=[]) 兜底：至少一页
            self.pages = [Page()]
        self._clamp_current()
        if self._init_items is not None:
            self.page.items = list(self._init_items)
            self._init_items = None
        self._sync_page_bed(to_page=True)

    def _clamp_current(self) -> None:
        if not self.pages:
            self.pages = [Page()]
        if not (0 <= self.current < len(self.pages)):
            self.current = 0

    def _sync_page_bed(self, *, to_page: bool) -> None:
        """床尺寸在 Document 与当前页之间同步。

        ``to_page=True`` 用 Document 的值写进页（构造时）；``to_page=False``
        用页的值写回 Document（切换页时，让门面字段跟随当前页）。
        """
        page = self.page
        if to_page:
            page.bed_w = self.bed_w
            page.bed_h = self.bed_h
        else:
            self.bed_w = page.bed_w
            self.bed_h = page.bed_h

    # -- 当前页门面（全部委托到 Page） --------------------------------------

    @property
    def page(self) -> Page:
        """当前页对象（越界自动夹到合法下标）。"""
        self._clamp_current()
        return self.pages[self.current]

    @property
    def items(self) -> list[Item]:
        """当前页的顶层图元列表（**活引用**，改它即改当前页）。"""
        return self.page.items

    @items.setter
    def items(self, value: list[Item]) -> None:
        self.page.items = list(value)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def add(self, item: Item) -> None:
        self.page.items.append(item)

    def remove(self, item: Item) -> "DetachInfo | None":
        return self.page.remove(item)

    def attach(self, item: Item, *, owner: Item | None = None,
               index: int | None = None) -> None:
        self.page.attach(item, owner=owner, index=index)

    def sorted_items(self) -> list[Item]:
        return self.page.sorted_items()

    def items_visible(self) -> list[Item]:
        return self.page.items_visible()

    def contains(self, item: Item) -> bool:
        return self.page.contains(item)

    def contains_anywhere(self, item: Item) -> bool:
        """**全文档**身份查找：对象是否挂在**任意一页**的树里。

        与 :meth:`contains` 的分工是本轮修的关键（A1）：

        - :meth:`contains` = **当前页**门面（FR-09），回答「它在不在**这一页**」，
          用于**选择/编辑**语义（选中的东西不能是别的页的）；
        - 本方法 = 「它**在不在文档里**」，用于**入模判据**（要不要
          ``doc.add`` 挂上去）。

        两者混用即出重复切割：命令的归属页在页被删后可能兜底到当前页，而对象
        其实挂在**另一页** —— ``contains`` 在那页判 False ⇒ ``doc.add`` 把
        **同一个对象**追加成第二个顶层 Item ⇒ 该几何被切两遍。实跑（删首屏 +
        undo 一次 + redo 一次）：``p2a`` 同时出现在 p0 与 p1，合并两页
        ``to_job_spec().paths_paper`` 同一条线出现两次 = 切纸机下两遍刀。

        实现复用 :meth:`page_of`（全文档身份查找），不新写一套遍历。
        """
        return self.page_of(item) is not None

    def top_z(self) -> float:
        return self.page.top_z()

    def bottom_z(self) -> float:
        return self.page.bottom_z()

    def owner_of(self, item: Item) -> "Item | None":
        return self.page.owner_of(item)

    def group_items(self, items, *, name: str = "组") -> Item | None:
        return self.page.group_items(items, name=name)

    def ungroup(self, container: Item) -> list[Item]:
        return self.page.ungroup(container)

    # -- 页管理（FR-09） ---------------------------------------------------

    def add_page(self, *, at: int | None = None, name: str | None = None) -> int:
        """新建空页并返回其下标（**不**切换过去）。

        ``at=None`` 追加到末尾；否则插到 ``at`` 位。页名自动去重
        （已有同名则递增）。
        """
        idx = len(self.pages) if at is None else max(0, min(at, len(self.pages)))
        self.pages.insert(idx, Page(name=name or self._next_page_name()))
        return idx

    def _next_page_name(self) -> str:
        used = {p.name for p in self.pages}
        n = len(self.pages) + 1
        while f"页{n}" in used:
            n += 1
        return f"页{n}"

    def duplicate_page(self, index: int | None = None) -> int | None:
        """复制页（深拷贝整页图元树）并返回新页下标；源页下标非法返回 None。"""
        if index is None:
            index = self.current
        if not (0 <= index < len(self.pages)):
            return None
        clone = self.pages[index].clone(name=self._next_page_name())
        self.pages.insert(index + 1, clone)
        return index + 1

    def remove_page(self, index: int | None = None) -> bool:
        """删页；**至少保留一页**（最后一页不可删，返回 False）。

        删当前页时把当前下标夹到合法范围（删末页 ⇒ 落到新末页）。
        """
        if index is None:
            index = self.current
        if not (0 <= index < len(self.pages)) or len(self.pages) <= 1:
            return False
        del self.pages[index]
        if index < self.current:
            self.current -= 1
        self._clamp_current()
        return True

    def move_page(self, index: int, to: int) -> bool:
        """页重排：把 ``index`` 页移到 ``to`` 位（越界或原地返回 False）。"""
        if not (0 <= index < len(self.pages)):
            return False
        to = max(0, min(to, len(self.pages) - 1))
        if index == to:
            return False
        page = self.pages.pop(index)
        self.pages.insert(to, page)
        if self.current == index:
            self.current = to
        elif index < self.current <= to:
            self.current -= 1
        elif to <= self.current < index:
            self.current += 1
        return True

    def switch_page(self, index: int) -> bool:
        """切换当前页（越界或原地返回 False，不改状态）。

        切页时床尺寸**跟随页**（同步回 ``doc.bed_w/bed_h`` 门面字段）。
        """
        if not (0 <= index < len(self.pages)) or index == self.current:
            return False
        self.current = index
        self._sync_page_bed(to_page=False)
        return True

    def page_of(self, item: Item) -> int | None:
        """图元所属页下标（**全文档**身份查找，跨页）；不在任何页返回 None。

        undo 的页归属用：命令记住「当时在哪一页」，撤销时据此回原页。
        """
        for i, page in enumerate(self.pages):
            if any(cur is item for cur in iter_items(page.items)):
                return i
        return None


def iter_items(items: list[Item], *, _depth: int = 0):
    """深度优先展开**全部** Item（容器本身也产出），不按 visible 过滤。"""
    _check_depth(_depth)
    for it in items:
        yield it
        yield from iter_items(it.children, _depth=_depth + 1)


def iter_ancestors(items: list[Item], *, _depth: int = 0, _chain: tuple = ()):
    """产出 ``(item, 祖先链)``，祖先链**根在前、不含 item 自身**。

    补上 M1 缺口的第二条通道：``Item`` 无 ``parent`` 反向指针，而
    :meth:`Item.page_bbox` 走不到祖先（M2 改用 :meth:`Item.unit_page_bbox`）。
    与 :func:`iter_flattens` 的 ``chain`` 语义一致，两条通道不得分叉。
    """
    _check_depth(_depth)
    for it in items:
        yield it, _chain
        if it.children:
            yield from iter_ancestors(it.children, _depth=_depth + 1,
                                       _chain=_chain + (it,))


def effectively_visible(doc, item: Item) -> bool:
    """图元的**有效**可见性 = 自身 + 整条祖先链全 ``visible``（M2 画布侧契约）。

    M1 连带契约的兑现（M1 ``iter_leaves`` docstring「不要在 iter_leaves 里过滤
    掉隐藏」）：渲染面要建出隐藏图元的场景项（否则隐藏→显示切换永远画不
    出来），故可见性**不能**在建项时过滤，只能在**显示**时按祖先链求值。
    画布侧唯一可见性判据（``make_gi`` 与 ``LayoutPage._sync_gi`` 共用）。

    祖先链**必须从文档根查**（``doc.items``）：从 ``[item]`` 查只会遍历
    item 自己的子树，容器的 ``visible`` 永远不被访问 ⇒ 隐藏容器下的叶子仍
    被画出。``doc`` 为 ``None``/``item`` 不在文档内时退化为「只看自身」。
    """
    if doc is None:
        return item.visible
    for cur, chain in iter_ancestors(doc.items):
        if cur is item:
            return all(anc.visible for anc in chain) and item.visible
    return item.visible  # 不在文档里：只看自身


def unit_paths(item: Item, chain: tuple["Item", ...] = ()) -> list[Polyline]:
    """单元的**页面系**折线（已含自身变换 + 祖先链，父∘子由内到外）。

    :func:`iter_units` 的配套消费函数 —— 吸附候选（FR-06）、越界预检、组框
    overlay 都改走本函数而非裸 ``item.transformed_paths()``/``page_bbox()``：
    后两者不含祖先链（M1 缺口 #1），编组后对组内叶子会给出局部坐标。
    """
    paths = item.transformed_paths()  # 自身变换已含镜像
    for anc in reversed(chain):  # 由内到外：父∘子
        anc_span = _mirror_span(anc)
        paths = [_apply_transform(anc, p, span=anc_span) for p in paths]
    return paths


def iter_units(items: list[Item], *, visible_only: bool = True):
    """产出**渲染/编辑单元** ``(item, 祖先链)``：叶子 + 带自身折线的容器。

    = :func:`iter_flattens` 的单元口径（容器自身折线是一等几何），但只带
    ``(item, chain)`` —— 供 :meth:`Item.unit_page_bbox` 这类「要页面系几何」
    的消费侧（越界预检、组框 overlay、对齐/分布）使用，**替代裸
    ``page_bbox()``**（M1 缺口 #1：裸 page_bbox 不走祖先链 → 编组后漏报）。
    """
    for it, _paths, chain in iter_flattens(items, visible_only=visible_only):
        yield it, chain


def iter_flattens(items: list[Item], *, visible_only: bool = True):
    """深度优先产出几何单元 ``(item, 自身折线组, 祖先变换链)``（容器叶子都产出）。

    - 自身折线组 = ``item.paths`` 已过 item 自身变换 ⇒ 落在**item 父系**；
      容器无自身折线时为空列表（其几何由 children 单元承担）。
    - 祖先链 = 根在前、**不含 item 自身**，调用方按 ``reversed(chain)``
      （由内到外）逐层套 :func:`_apply_transform` 即得页面系（父∘子，一般式）。
    - **容器自身 paths 是独立单元**（z 取容器 z，不排子项）—— 否则容器自带
      几何会在拍平里消失。
    - ``visible_only`` 时隐藏项连同整棵子树跳过。
    - 递归深度受 :data:`MAX_TREE_DEPTH` 约束（成环 → ``ValueError``）。

    ⚠ 产出的 ``chain`` 是**唯一**的祖先变换通道：:meth:`Item.page_bbox` 走不到
    祖先（见模块 docstring「``page_bbox()`` 不走祖先链」），故需要页面系几何的
    消费侧一律改走 :func:`iter_units` + :meth:`Item.unit_page_bbox` /
    :func:`unit_paths`（M2 已全部接上）。

    叶子文档下与「按 z 排序后逐项 transformed_paths」逐位相同。
    """
    def _walk(seq, chain, depth):
        _check_depth(depth)
        for it in seq:
            if visible_only and not it.visible:
                continue
            span = _mirror_span(it)  # 每 item 一次，不每折线
            yield it, [_apply_transform(it, p, span=span) for p in it.paths], chain
            if it.children:
                yield from _walk(it.children, chain + (it,), depth + 1)

    yield from _walk(items, (), 0)


def _own_geometry(items: list[Item], *, visible_only: bool = True):
    """深度优先收集**自身折线非空**的图元（几何拥有者），每项只出现一次。

    与 :func:`iter_flattens` 同口径遍历 —— 枚举面（``items_visible`` /
    ``sorted_items``）必须看得见容器自身折线，否则它能进导出/送作业却进不了
    越界预检（layout_page._on_export）。容器本身也是「拥有者」时才算。
    """
    for it, paths, _chain in iter_flattens(items, visible_only=visible_only):
        if paths:
            yield it


def iter_leaves(items: list[Item], *, visible_only: bool = False):
    """深度优先收集叶子图元（容器自身不产出，只产出其后代）= **渲染单元**。

    **``visible_only`` 默认 False**（M1 评审裁决）：枚举面要的是「全部渲染
    单元」，可见性过滤是 :func:`flatten_visible` 的活。默认 True 会与
    ``make_gi`` 的 ``gi.setVisible(item.visible)``（``undo_cmds.py:51``）冲突 ——
    隐藏叶子的 ``PathItem`` 永不创建，此后 ``_gi_for(hidden)`` 恒 ``None``、
    ``_sync_gi`` 恒空操作，**隐藏→显示切换永远画不出来**。实跑：
    ``iter_leaves([G])`` 对唯一 ``visible=False`` 的叶子返回 ``[]``。

    渲染面按本函数建 ``PathItem``（容器跳过，见模块 docstring「画布侧硬约束」）；
    但**入模另判**：组内叶子不可再 ``doc.add`` 成第二个顶层 Item，用
    :meth:`Document.contains` 判归属，否则同一几何被切两遍。

  ⚠ **连带契约（M2 已兑现）**：默认 False 意味着**隐藏容器的叶子也会被
  建出场景项**。故 ``make_gi`` 的 ``gi.setVisible(item.visible)``（:51）
  **不足以**表达可见性 —— 容器隐藏、叶子 ``visible=True`` 时会把组内几何
  画出来。**有效可见性 = 自身 + 整条祖先链全 visible**，由
  :func:`effectively_visible` 统一实现（``make_gi`` 与 ``LayoutPage._sync_gi``
  共用一份，不各写一套）；祖先链由 :func:`iter_ancestors` 携带，**没有**在
  :func:`iter_leaves` 里过滤掉隐藏 —— 那正是 B4 要修的「切换画不出来」。


    反之，隐藏叶子在默认模式下**仍产出**（这正是 B4 的修复点）。
    """
    for it, _paths, _chain in iter_flattens(items, visible_only=visible_only):
        if not it.children:
            yield it


def normalize_local(item: Item) -> tuple[float, float]:
    """入库归一：paths 平移使内容 bbox 左下 = 本地 (0,0)，pos 补偿。

    **页面几何严格不变**（纯局部重锚）：``pos' = pos + R(θ)S(s)·(x0, y0)``。
    **只在创建/导入入口调用**（layout_page 的绘制/组导入路径）；model/export
    内部不自动跑 —— 直接构造 Item 的用例不受影响（§2.2）。

    容器（``children`` 非空）不适用：它没有自身折线可平移、子项局部坐标各自
    独立（否则会算出一个无意义的 pos 补偿）→ 直接 no-op。

    返回平移量 delta=(x0, y0)（= 旧局部坐标系原点在新局部坐标系的位置）。
    """
    if item.children:
        return (0.0, 0.0)
    x0, y0, _x1, _y1 = item.bbox()
    if x0 == 0.0 and y0 == 0.0:
        return (0.0, 0.0)
    item.paths = [[(x - x0, y - y0) for x, y in p] for p in item.paths]
    s = item.scale
    rad = math.radians(item.angle_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    ox, oy = x0 * s, y0 * s
    item.pos = (
        item.pos[0] + ox * cos - oy * sin,
        item.pos[1] + ox * sin + oy * cos,
    )
    return (x0, y0)


def flatten_visible(doc: Document) -> list[Polyline]:
    """拍平全部可见图元为**绝对页面坐标**折线（跳过 <2 点折线）。

    按 z 升序（稳定）；容器 z 不参与排序（FR-02 v1.3 —— 单一容器 z 无法再现
    z 不连续编组在全局序里的交错，如 z=0、2 夹 z=1）。

    **祖先变换必须参与**：每个单元先过自身变换，再按祖先链由内到外逐层
    合成（父∘子，一般式）。容器恒等时退化为「子项现状几何」，产品路径无
    差别；但导出/预览/送作业三链都吃本函数，漏掉祖先链 = 画布显示变换后
    位置、导出却在未变换位置（预览≡发送断裂）。
    叶子文档与原「``items_visible()`` 逐项 transformed_paths」逐位相同。
    """
    units = sorted(iter_flattens(doc.items), key=lambda u: u[0].z)
    out: list[Polyline] = []
    for _it, paths, chain in units:
        for anc in reversed(chain):  # 由内到外：父∘子
            anc_span = _mirror_span(anc)
            paths = [_apply_transform(anc, p, span=anc_span) for p in paths]
        for p in paths:
            if len(p) >= 2:
                out.append(p)
    return out
