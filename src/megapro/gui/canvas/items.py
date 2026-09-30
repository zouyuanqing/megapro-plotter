"""排版图元（Qt）—— PathItem 直画 model.Item（场景 ≡ 纸面 mm y-up）。

契约（§2.2 canvas/items.py）：
- 一图元一条 QPainterPath（moveTo/lineTo 串联，pen 宽 0）；paths 以本地纸面
  y-up **直画**（旧 ``layout/items.py:56`` 的 ``(x, -y)`` 本地翻画已删——
  那是与 model.transformed_paths 的 CCW 数学互为镜像的根因）。
- ``boundingRect`` 直接用 ``model.Item.bbox()``；改前 ``prepareGeometryChange()``。
- ``transformOriginPoint=(0,0)``（= pos，与 model.transformed_paths 旋转
  支点严格一致：mapToScene ≡ transformed_paths，+θ = 纸面逆时针）。
- ``itemChange(ItemPositionChange)`` 只 return 吸附值（Qt 文档禁止在此
  回调内 setPos）。
- 拖动结束（mouseRelease）一次性回写 model 并进撤销栈：构造
  :class:`~megapro.gui.canvas.undo_cmds.MoveItemsCommand`（手势 token，
  mergeWith 先比 token）——命令写 model.Item，场景同步唯一入口 page._sync_gi。

**祖先变换（D1）**：场景是**扁平**的（无 Qt 父子），而切割链
``flatten_visible`` 会把**整条祖先链**逐层合成（父∘子，一般式）。只把叶子
自身变换交给 Qt（``setPos/setScale/setRotation``）时，二者在「祖先带非恒等
变换」时分叉：用户看到的与机器切的不是同一处（组容器被挪动/粘贴出的两层
容器都会踩到）。故本类额外持有一条祖先仿射 ``A(p) = aO + aL·p``，并按 Qt 的
复合顺序 ``T(pos)·M·R·S``（实测，见 :func:`_ancestor_extra`）求出

    ``M = A ∘ T(pos) ∘ T(-pos)``

使 ``mapToParent(自身变换后的点) == unit_paths(item, chain)`` 逐位相等。
``A`` 由 :func:`ancestor_affine` 用**模型自己的合成数学**（``unit_paths`` 跑
三个基准点）反解，不在本文件重写一份镜像/旋转算术（``coords`` 的唯一翻转
实现因此仍是唯一来源）。

⚠ **``pos()`` 的语义不变，仍是父坐标系**：``commit_move`` 拿 ``gi.pos()``
写回 ``it.pos``，``handles.pivot()`` 也按父系读它 —— 祖先偏移只进
``setTransform``，绝不混进 ``pos``，否则回写会把页面坐标当父系坐标写进
模型（一次拖动就把图元甩到床外）。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from megapro.gui.canvas.snap import SnapEngine, snap_candidate_paths
from megapro.gui.layout.model import Item, unit_paths

__all__ = ["PathItem", "ancestor_affine"]


#: 反解祖先仿射用的三个基准点（局部系 (0,0) / (1,0) / (0,1)）。
#: 单点折线足够：``unit_paths`` 只做逐点变换，不做长度过滤（那是
#: ``flatten_visible`` 的职责），故 1 点折线照样给出正确的像。
_BASIS = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))

#: 恒等祖先仿射（无祖先链时的快路径，也是不回归的基准值）。
_IDENTITY_AFFINE = ((0.0, 0.0), ((1.0, 0.0), (0.0, 1.0)))


def ancestor_affine(chain) -> tuple:
    """祖先链的合成仿射 ``A(p) = aO + aL·p``（页面系）。

    **用模型自己的数学反解，不重写变换**（FR-01「合成数学的唯一实现」）：
    造一个恒等变换的探针 :class:`Item`，把三个基准点丢进
    :func:`~megapro.gui.layout.model.unit_paths`（``flatten_visible`` 用的
    同一条合成链），由三个像解出 ``aO`` 与 ``aL`` 的两列。这样镜像
    （``coords.mirror_scalar``，绕本地 bbox 中心）、缩放、旋转的先后次序
    与切割侧**逐位同源**，画布不可能与机器算出两个答案。

    空链（顶层图元 / 恒等容器）返回恒等仿射，不做任何模型调用。

    ⚠ **成本**：每个探针点要走一遍整条链，链上每个祖先还要算一次
    ``_mirror_span``（镜像开启时是 ``ancestor.bbox()``，O(子树点数)）。
    故调用方应**按需**调用（结构变化 / 同步时），**不要**挂在逐帧路径上。
    """
    if not chain:
        return _IDENTITY_AFFINE
    probe = Item(paths=[[p] for p in _BASIS], pos=(0.0, 0.0), scale=1.0,
                 angle_deg=0.0)
    (x0, y0), (x1, y1), (x2, y2) = (p[0] for p in unit_paths(probe, chain))
    return (x0, y0), ((x1 - x0, y1 - y0), (x2 - x0, y2 - y0))


def _ancestor_extra(affine, pos) -> QtGui.QTransform:
    """祖先仿射 → Qt 的 ``setTransform`` 矩阵 ``M``。

    Qt 的复合次序是 ``T(pos)·M·R·S``（本地点先缩放、再旋转、再过 ``M``、
    最后平移 ``pos``；实测 ``pos=(5,7) S=3 extra=T(100,0)`` 把 ``(10,0)``
    映到 ``(115,7)``）。我们要的是 ``A∘T(pos)∘R∘S``，故

        ``T(pos)·M = A·T(pos)``  ⟹  ``M = T(-pos)·A·T(pos)``

    展开成 2×3：``M`` 的线性部分 = ``aL``；平移部分
    ``= aO + aL·pos - pos``。
    """
    (a0x, a0y), ((a00, a10), (a01, a11)) = affine
    px, py = pos
    return QtGui.QTransform(
        a00, a10, a01, a11,
        a0x + a00 * px + a01 * py - px,
        a0y + a10 * px + a11 * py - py,
    )


class PathItem(QtWidgets.QGraphicsPathItem):
    """模型 Item 的折线绘制 + 选择/拖动/网格吸附（场景坐标 = 纸面 mm y-up）。"""

    def __init__(
        self, model_item: Item, *, color: str = "#0a5cff", parent=None,
    ) -> None:
        super().__init__(parent)
        self.model_item = model_item
        self.color = color
        self._syncing = False
        #: 祖先链（根在前、不含自身），由 :meth:`set_ancestor_chain` 写入。
        #: 空链 = 顶层图元（``A`` 恒等）——产品主路径。
        self._chain: tuple = ()
        self._affine = _IDENTITY_AFFINE
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.ItemIsMovable
                      | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        # 旋转/缩放支点 = 本地 (0,0)（= pos），与 model.transformed_paths 一致
        self.setTransformOriginPoint(0.0, 0.0)
        pen = QtGui.QPen(QtGui.QColor(self.color))
        pen.setWidthF(0)  # cosmetic 发丝线：随缩放恒 1px
        self.setPen(pen)
        self.setBrush(QtCore.Qt.NoBrush)
        self.rebuild_path()
        self.apply_model_state()

    # -- 祖先变换（D1）------------------------------------------------------

    def set_ancestor_chain(self, chain, *, _affine_cache: dict | None = None) -> None:
        """写入祖先链并重算场景变换（**祖先变化的唯一入口**）。

        ``chain`` 语义与 :func:`~megapro.gui.layout.model.iter_ancestors`
        一致：根在前、不含自身。空链 ⇒ 恒等 ⇒ 与本类引入 D1 之前**逐位
        相同**（``M`` 退化为单位阵）。

        **每次都重算，不做「链相同就跳过」的短路**：链相同**不代表**祖先
        变换相同 —— 祖先的 ``pos/scale/angle_deg/mirror_*`` 是就地改的
        （``g.pos = (60,20)``），链元组（按身份比较）完全没变。故链相等时
        早退会让画布停在旧位置，而机器已按新容器变换切 —— 正是本条要杀的
        那一类分叉。

        ``_affine_cache``：批量调用方（``LayoutPage._sync_ancestor_chains``）
        传一个 dict，按**链身份**记忆化 :func:`ancestor_affine` 的结果 ——
        同一条链上的所有叶子共用一次计算（它对镜像祖先是 O(子树点数)，
        逐叶重算会把成本乘以叶子数）。**只在一次同步批次内有效**，不可跨
        编辑复用（祖先变换可能已变）。
        """
        chain = tuple(chain)
        self._chain = chain
        if _affine_cache is not None and chain in _affine_cache:
            self._affine = _affine_cache[chain]
        else:
            self._affine = ancestor_affine(chain)
            if _affine_cache is not None:
                _affine_cache[chain] = self._affine
        self._refresh_ancestor_transform()

    def page_origin(self) -> QtCore.QPointF:
        """本地图元**本地原点**的页面（场景）坐标 = ``A(pos)``。

        给需要**页面系**的消费者用（对比 :meth:`pos` 的**父系**语义）。
        D1 之后两者可以差出一个完整的祖先变换：``pos()`` 是父坐标、
        ``page_origin()`` 是 ``ancestor_affine`` 作用后的页面坐标。

        **为什么必须有它**（对抗性复核 R1 实测）：``SelectionHandles.pivot()``
        把支点与 ``scene_pos``（页面系）放在一起算 ``r0/r1`` 与旋转角，而它
        取的是 ``gi.pos()``（父系）⇒ 在非恒等祖先下支点与手柄实际摆放位置
        脱节：实测组 pos=(100,30) 时支点误差 **104.40mm**，抓可见角点拖到
        两倍得 ``k=1.1497`` 而非 2.0。

        ⚠ **消费方要自己在页面系里把结果换回父系再写 pos**（见
        :meth:`page_to_parent`）—— ``pos()`` 的父系语义不能改：手柄
        ``end()`` 与 :meth:`commit_move` 都把 ``gi.pos()`` 直接写回模型的
        ``pos`` 字段，改了就把页面坐标写进父系字段。
        """
        return self.mapToParent(QtCore.QPointF(0.0, 0.0))

    def page_to_parent(self, point: QtCore.QPointF) -> QtCore.QPointF:
        """页面（场景）坐标 → 本图元的**父系** ``pos`` 值。

        :meth:`page_origin` 的逆运算，给「在页面系里算完、要写回父系」的场景
        用（手柄多选缩放/旋转就是：支点与鼠标都在页面系，算完必须换回来）。
        空祖先链时恒等（顶层图元）。
        """
        return self.mapFromParent(point)

    def _refresh_ancestor_transform(self) -> None:
        """按**当前** ``pos`` 重算并写入祖先矩阵 ``M``。

        ⚠ **必须在 Qt 派发 ``ItemPositionChange`` 之后调用**，因为 Qt 的复合
        是 ``T(pos)·M·R·S`` —— ``M`` 的共轭要用**同一个** ``pos`` 才成立。

        ⚠ **不能用** ``itemChange`` 触发：实测 Qt 会把该回调内的
        ``setTransform`` **丢弃**（变换停在旧值）。故拖动期挂在
        :meth:`mouseMoveEvent`（虚函数，调用能生效）。

        **用 ``self.pos()``（活值）而非 ``model_item.pos``**：拖动中模型要等到
        mouseRelease 才由 :meth:`commit_move` 回写，故活值才是「即将写进模型的
        那个 pos」。用它共轭 ⇒ 拖动全程画布 ≡ 提交后的切割位置，不会出现
        「松手瞬间图形跳一下」。
        """
        if not self._chain:
            # 恒等链：写单位阵而非跳过 —— 清掉可能残留的旧祖先变换
            # （例如解组后链变空）。
            self.setTransform(QtGui.QTransform())
            return
        p = self.pos()
        self.setTransform(_ancestor_extra(self._affine, (p.x(), p.y())))

    # -- 几何 ---------------------------------------------------------------

    def _local_rect(self) -> QtCore.QRectF:
        """本地 bbox（= model.Item.bbox()；退化尺寸保护）。"""
        x0, y0, x1, y1 = self.model_item.bbox()
        return QtCore.QRectF(
            QtCore.QPointF(x0, y0),
            QtCore.QPointF(max(x1, x0 + 1e-6), max(y1, y0 + 1e-6)),
        )

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return self._local_rect()

    def shape(self) -> QtGui.QPainterPath:  # noqa: N802
        """可点/可选区域 = 本地 bbox（与旧 PolylineItem 的可点语义一致）。

        不用 QGraphicsPathItem 的描边 shape（宽 0 描边几乎点不中）。
        """
        p = QtGui.QPainterPath()
        p.addRect(self._local_rect())
        return p

    def rebuild_path(self) -> None:
        """model.paths → 单条 QPainterPath（直画 y-up 本地坐标）。

        用 :meth:`Item.local_paths` 而非 ``item.paths``：FR-08 要求**镜像烘进
        画笔路径**（镜像绕本地 bbox 中心 ⇒ ``boundingRect`` 稳定）。场景的
        scale/rotate/pos 仍由 Qt 承担（见 :meth:`apply_model_state`），故这里
        只取本地系。未设镜像时 ``local_paths()`` 逐位等于 ``item.paths``。
        """
        self.prepareGeometryChange()
        path = QtGui.QPainterPath()
        for poly in self.model_item.local_paths():
            if not poly:
                continue
            path.moveTo(QtCore.QPointF(float(poly[0][0]), float(poly[0][1])))
            for x, y in poly[1:]:
                path.lineTo(QtCore.QPointF(float(x), float(y)))
        self.setPath(path)
        self.update()

    def apply_model_state(self) -> None:
        """model → 场景（无翻转；场景 ≡ 纸面）。程序化 setPos 不触发吸附。"""
        self._syncing = True
        try:
            self.setPos(float(self.model_item.pos[0]), float(self.model_item.pos[1]))
            self.setScale(self.model_item.scale)
            self.setRotation(self.model_item.angle_deg)
            # 祖先矩阵必须**最后**写：它的共轭要用刚设好的 pos。
            self._refresh_ancestor_transform()
        finally:
            self._syncing = False

    # -- 拖动吸附 + 回写 ----------------------------------------------------

    def _snap_value(self, v: QtCore.QPointF) -> QtCore.QPointF:
        """拖动位置吸附（Q4）：对象吸附（端点/中点/边）优先，其次网格。

        对象候选 = 其余图元的**页面系**关键点（FR-06 前置修复：候选源切
        :func:`snap_candidate_paths` 的页面域 —— ``transformed_paths`` 即时
        计算）。旧实现遍历 ``doc.items`` 拿 ``it.paths``（**局部**坐标）而喂入
        的是页面坐标 ``v``，域不一致：``pos=(50,60)`` 的图元页面点 (51,61)
        对局部候选零命中、对页面域命中 (51.0,60.0)；且 ``doc.items`` 漏掉
        **组内叶子**（不在顶层列表里）并把**隐藏图元**也算进候选。

        pitch = page.snap_pitch（随缩放 = grid_steps 的 minor，见
        layout_page._sync_snap_pitch）。
        """
        page = getattr(self, "_page", None)
        if page is None or not getattr(page, "snap_enabled", False):
            return v
        pitch = float(getattr(page, "snap_pitch", 0.0) or 0.0)
        engine = SnapEngine(pitch, enabled=True)
        x, y = engine.snap((float(v.x()), float(v.y())),
                           snap_candidate_paths(getattr(page, "doc", None),
                                                exclude=self.model_item))
        return QtCore.QPointF(x, y)

    def itemChange(self, change, value):  # noqa: N802
        if (change == QtWidgets.QGraphicsItem.ItemPositionChange
                and not self._syncing):
            value = self._snap_value(value)
        return super().itemChange(change, value)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """拖动中：Qt 改了 ``pos`` 后就地重算祖先矩阵 ``M``。

        **为什么必须在这里**：``M = T(-pos)·A·T(pos)`` 与 ``pos`` 耦合
        （见 :meth:`_refresh_ancestor_transform`），而 Qt 的拖动直接改
        ``pos``、不经过 :meth:`apply_model_state`。不重算就会「拖动时按旧
        祖先矩阵渲染」——在带旋转/缩放的祖先下，图形会与光标脱节。

        ``itemChange`` 里写 ``setTransform`` 会被 Qt 丢弃（实测），故只能走
        虚函数。``super()`` 先跑完（它负责真正改 ``pos``），再重算。
        """
        super().mouseMoveEvent(event)
        self._refresh_ancestor_transform()

    def commit_move(self) -> None:
        """手势结束（mouseRelease）：位移一次性回写 model 并进撤销栈。

        读取源永远是 model；gi→model 的回写只发生在构建本命令时（§2.2）。
        多选拖动时 Qt 同步移动所有选中项 —— 按「gi 与 model 已漂移」收集整组。
        """
        page = getattr(self, "_page", None)
        if page is None:
            return
        moves = []
        for gi in list(getattr(page, "_scene_items", ())):
            it = gi.model_item
            cur = (float(gi.pos().x()), float(gi.pos().y()))
            if (abs(cur[0] - it.pos[0]) > 1e-9 or abs(cur[1] - it.pos[1]) > 1e-9):
                moves.append((it, it.pos, cur))
        if moves:
            from megapro.gui.canvas.undo_cmds import MoveItemsCommand

            page._undo.push(MoveItemsCommand(page, moves, "移动"))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.commit_move()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击：组编辑态 → 文字重编 → 图片重追（page 回调链）。"""
        page = getattr(self, "_page", None)
        if page is not None and getattr(page, "on_item_double_clicked", None):
            if page.on_item_double_clicked(self.model_item):
                event.accept()
                return
        if page is not None and self.model_item.text_spec:
            page.edit_text_item(self.model_item)
            event.accept()
            return
        if page is not None and self.model_item.image_spec:
            # 图片就地重追（FR-10/US-5）：有 image_spec 的图元双击即调参重出
            page.retrace_image_item(self.model_item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
