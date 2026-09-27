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

  ⚠ **「跳过容器」不够，只跳过容器会双重切割**（M1 评审实测，见
  :func:`iter_leaves` 与 :meth:`Document.contains` 的回归用例）：叶子在
  容器里时**不在** ``doc.items`` 顶层列表中（身份判定），故
  ``canvas/undo_cmds.py:make_gi`` 现写的 ``if item not in page.doc.items:
  page.doc.add(item)``（:54-55）会把**每一个组内叶子**当成新图元追加成
  第二个顶层 Item。实跑：``doc.items = ['G','a','b']``、``flatten_visible``
  吐 4 条折线而真实几何单元只有 2 条 —— **同一几何被切两遍**，对切纸机
  即重复下刀。正确的 M2 改法是把**渲染单元**与**模型归属**分开：
  - 渲染：按 :func:`iter_leaves` 建 ``PathItem``，容器跳过；
  - 入模：``doc.add`` 只在**确无归属**时执行，判据用 :meth:`Document.contains`
    （全树身份查找），**不是**「是否在顶层列表」。

  组变换只写叶子集合（FR-04/FR-05），容器变换恒为恒等。
- **删除/复原的归属契约（M1 立，M2 接线）**：:meth:`Document.remove` **组感知**
  （按身份定位 owning container 并从其 children 摘除）并返回 :class:`DetachInfo`
  （原容器 + 原下标），:meth:`Document.attach` 为其逆。**两者必须成对使用**：
  只组感知而不把归属信息带回 undo 侧，``make_gi`` 仍会把摘下的叶子
  ``doc.add`` 成顶层项 → **组被静默解散**（实测：摘除 a 后 ``make_gi(lp,a)``
  得 ``doc.items=['G','a']``、``G.children=['b']``，无异常）。原「抛 ValueError」
  方案已推翻：它让 ``QUndoStack`` 半污染（命令入栈、模型未改）且 Ctrl+Z 后
  几何翻倍，比静默更差。
- **已知缺口：``page_bbox()`` 不走祖先链（属 M2，M1 无法在本文件内修）**。
  :meth:`Item.page_bbox` 只过自身变换，而 :func:`flatten_visible` 过祖先链 ——
  对**带非恒等变换的容器下的叶子**，两者给出不同的页面坐标。实跑：
  ``leaf(pos=(10,20))`` 在 ``container(pos=(100,0))`` 下，``flatten`` 给
  ``(110,20)``，``leaf.page_bbox()`` 给 ``(10,20)``、``cont.page_bbox()`` 给
  ``(110,20)``。而 ``layout_page.py:981-982`` 的越界预检正是
  ``for it in doc.items_visible(): it.page_bbox()`` —— 枚举面拿不到祖先链
  （``Item`` 无 ``parent`` 反向指针，:func:`iter_flattens` 产出的是
  ``(item, paths, chain)`` 三元组而非带 ``page_bbox`` 的单元），故实跑一个
  真实 x 范围 250..300（超 210 床）的图元时**所有上报 bbox 都在床内、越界
  警告永不触发**，形成「导出切了 / 预检没报」分叉。M1 不能修：修法要么给
  ``Item`` 加 ``parent`` 反向指针（新字段，须重想 ``eq=False`` 之外的构造与
  剪贴板白名单语义），要么改 ``layout_page.py:981-982`` 消费单元 —— 两者都
  越出本里程碑边界。**M2 必须处理**，组框 overlay（FR-05 按容器 page_bbox）
  与吸附候选（FR-06 按页面域）都会撞上。容器恒等时（当前产品路径）不触发。
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

import math
from dataclasses import dataclass, field

from megapro.gui.canvas.coords import BED_H, BED_W

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


def _apply_transform(item: Item, path: Polyline) -> Polyline:
    """一条折线过 ``item`` 的变换：本地 → 缩放 → 旋转(绕 pos) → 平移 pos。

    **合成数学的唯一实现**（FR-01 父∘子）：叶子取自身变换、容器按祖先链
    由内到外逐层套用，两条路径共用本函数，故 ``Item.transformed_paths`` 与
    ``flatten_visible`` 永不各写一套（导出/预览/送作业三链同源）。
    """
    px, py = item.pos
    s = item.scale
    rad = math.radians(item.angle_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    pts = []
    for x, y in path:
        # 本地(可能带缩放前) → 缩放 → 旋转(绕 pos) → 平移
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
    children: list["Item"] = field(default_factory=list)  # 非空即容器

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
        out = [_apply_transform(self, p) for p in self.paths]
        for ch in self.children:
            out.extend(_apply_transform(self, p)
                       for p in ch.transformed_paths(_depth=_depth + 1))
        return out

    def page_bbox(self) -> tuple[float, float, float, float]:
        """变换后的页面 bbox —— **只含自身变换，不含祖先链**（已知缺口，见模块
        docstring「``page_bbox()`` 不走祖先链」）。

        对**带非恒等变换的容器下的叶子**，本方法给的不是页面坐标：实跑
        ``leaf(pos=(10,20))`` 在 ``container(pos=(100,0))`` 下，
        ``flatten_visible`` 给 ``(110,20)`` 而本方法给 ``(10,20)``。
        ``Item`` 无 ``parent`` 反向指针，本方法在 M1 内无法走祖先链；``_on_export``
        的越界预检（``layout_page.py:981-982``）因此在编组后会漏报，属 M2。

        **无 children 的叶子语义逐位不变**（既有 golden 钉死）：其祖先链为空，
        自身变换即页面变换。
        """
        paths = self.transformed_paths()
        xs = [x for p in paths for x, _ in p]
        ys = [y for p in paths for _, y in p]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class DetachInfo:
    """:meth:`Document.remove` 的归属回执 —— undo 复原组结构所需的全部信息。

    ``owner`` = 原 owning container（顶层图元为 ``None``）；``index`` = 在
    ``owner.children``（或顶层 ``items``）中的原下标。undo 侧调
    :meth:`Document.attach` 即可原样复原，避免「叶子变顶层项、组被静默解散」。
    """

    owner: Item | None
    index: int


@dataclass
class Document:
    """排版文档：图元列表 + 床尺寸。原点 (0,0) = 工件原点（床左下）。"""

    items: list[Item] = field(default_factory=list)
    bed_w: float = BED_W
    bed_h: float = BED_H

    def add(self, item: Item) -> None:
        self.items.append(item)

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
        复原，**不会**把叶子变成顶层项）；``owner=None`` 则挂到顶层
        ``items``。``index`` 越界/为 ``None`` 时追加到末尾。
        """
        if owner is None:
            if not any(cur is item for cur in self.items):
                self.items.append(item)
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


def iter_items(items: list[Item], *, _depth: int = 0):
    """深度优先展开**全部** Item（容器本身也产出），不按 visible 过滤。"""
    _check_depth(_depth)
    for it in items:
        yield it
        yield from iter_items(it.children, _depth=_depth + 1)


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

    ⚠ 产出的 ``chain`` 是**唯一**的祖先变换通道：``Item.page_bbox()`` 走不到
    祖先（见模块 docstring「``page_bbox()`` 不走祖先链」），故本函数的三元组
    目前**不能**直接喂给 ``layout_page._on_export`` 的越界预检（它要
    ``Item.page_bbox()``）。属 M2。

    叶子文档下与「按 z 排序后逐项 transformed_paths」逐位相同。
    """
    def _walk(seq, chain, depth):
        _check_depth(depth)
        for it in seq:
            if visible_only and not it.visible:
                continue
            yield it, [_apply_transform(it, p) for p in it.paths], chain
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

    ⚠ **连带契约（M2 必须兑现）**：默认 False 意味着**隐藏容器的叶子也会被
    建出场景项**。故 ``make_gi`` 的 ``gi.setVisible(item.visible)``（:51）
    **不足以**表达可见性 —— 容器隐藏、叶子 ``visible=True`` 时会把组内几何
    画出来。M2 的有效可见性必须是「自身 + 整条祖先链全 visible」；祖先链由
    :func:`iter_flattens` 携带（或新增携带祖先可见性的遍历），不要在
    ``iter_leaves`` 里把它过滤掉 —— 那正是 B4 要修的「切换画不出来」。

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
            paths = [_apply_transform(anc, p) for p in paths]
        for p in paths:
            if len(p) >= 2:
                out.append(p)
    return out
