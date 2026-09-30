"""选择手柄（Qt）—— 旋转 + **等比**缩放手柄（QGraphicsItem 无内建手柄）。

契约（§2.2 canvas/handles.py）：
- ``model.Item.scale`` 是标量 → 只支持等比缩放（拖角按对角距离比取 k；
  自由拉伸一律按 min 比例等比化）。
- 缩放/旋转支点 = 本地原点（= pos，与 model 数学一致）；多选整体等比缩放
  ``scale_i' = k·scale_i``、``pos_i' = A + k·(pos_i − A)``（A = 选择集 bbox
  锚点绝对坐标），保相对布局。**坐标系纪律（F1/R1）**：手柄事件、支点、
  选择框全是**页面系**，而 ``pos`` 是**父系**字段 —— 单选支点经
  ``PathItem.page_origin()`` 进页面系，多选算出的页面系新原点经
  ``PathItem.page_to_parent()``（A⁻¹）换回父系**才**写 ``pos``；空祖先链时
  两者恒等，行为与引入祖先变换之前逐位相同。
- 拖动中只临时 setScale/setRotation/setPos；``mouseRelease`` 一次性 push
  :class:`~megapro.gui.canvas.undo_cmds.ChangeItemPropsCommand`（old,new）。
- 手柄恒定屏幕大小：``setScale(1/ppm)``（不用 ItemIgnoresTransformations
  —— Qt 文档警告该 flag 下坐标/碰撞必须走 deviceTransform）。**ppm 变了就得
  重算**：本类在 ``__init__`` 里自接 ``PaperView.viewChanged``（唯一入口
  ``_apply_transform``，缩放/平移/fit 都走它），否则手柄尺寸要漂到下一次
  ``sync()``（= 选中变化）才对上。slot 先比缓存 ppm，平移不改 ppm 直接返回，
  不把重算挂到「中键拖动每次鼠标移动」上。
- 旋转手柄与选框顶边的间距同样是**屏幕** px（``_ROTATE_GAP_PX / ppm`` 场景
  单位），否则缩小时会陷进选区。
- 摆位用 ``sceneBoundingRect()``（boundingRect 不受自身变换影响）。
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

__all__ = ["SelectionHandles"]

#: 手柄标记半宽（手柄本地单位 = 屏幕 px，经 setScale(1/ppm) 恒定屏幕大小）
_HANDLE_HALF = 5.0

#: 旋转手柄中心 ↔ 选择框顶边的**屏幕**间距（px）；场景单位 = 本值 / ppm
_ROTATE_GAP_PX = 18.0


class _HandleItem(QtWidgets.QGraphicsItem):
    """单个手柄标记（scale 角点 / rotate 顶部）；事件转发给 SelectionHandles。"""

    def __init__(self, kind: str, index: int, owner) -> None:
        super().__init__(None)
        self.kind = kind
        self.index = index
        self.owner = owner
        self.setZValue(1e6)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return QtCore.QRectF(-_HANDLE_HALF, -_HANDLE_HALF,
                             2 * _HANDLE_HALF, 2 * _HANDLE_HALF)

    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        painter.setBrush(QtGui.QColor("#fff"))
        pen = QtGui.QPen(QtGui.QColor("#0a5cff" if self.kind == "scale" else "#e60"))
        pen.setWidthF(max(pen.widthF(), 1.0))
        painter.setPen(pen)
        r = QtCore.QRectF(-_HANDLE_HALF, -_HANDLE_HALF,
                          2 * _HANDLE_HALF, 2 * _HANDLE_HALF)
        if self.kind == "rotate":
            painter.drawEllipse(r)
        else:
            painter.drawRect(r)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.owner.begin(self, QtCore.QPointF(event.scenePos()))
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.owner.update_drag(self, QtCore.QPointF(event.scenePos()))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.owner.end(self, QtCore.QPointF(event.scenePos()))
        event.accept()


class SelectionHandles:
    """管理选中图元的旋转/缩放手柄（自管 QGraphicsItem，不进选择集）。"""

    def __init__(self, scene, view, page=None) -> None:
        self._scene = scene
        self._view = view
        self._page = page
        self._targets: list = []
        self._handles: list[_HandleItem] = []
        self._drag: dict | None = None
        #: 上次写进手柄的 ppm（``_on_view_changed`` 的「有无必要重算」判据）
        self._last_ppm = 0.0
        # 手柄尺寸恒定屏幕大小 ⇒ 必须跟着视图缩放走。``viewChanged`` 由
        # ``PaperView._apply_transform`` 发（缩放/滚轮/fit/平移的唯一汇流点），
        # 自接在这里，不依赖 layout_page 接线。
        view.viewChanged.connect(self._on_view_changed)

    # -- 摆位 ---------------------------------------------------------------

    def sync(self, items) -> None:
        """selectionChanged / 编辑后重新摆位（sceneBoundingRect 并集）。"""
        self.clear()
        self._targets = list(items)
        if not self._targets:
            return
        rect = self.selection_rect()
        corners = [
            rect.topLeft(), rect.topRight(),
            rect.bottomRight(), rect.bottomLeft(),
        ]
        for i, pos in enumerate(corners):
            h = _HandleItem("scale", i, self)
            h.setPos(pos)
            self._handles.append(h)
        rot = _HandleItem("rotate", 0, self)
        self._handles.append(rot)
        for h in self._handles:
            self._scene.addItem(h)
        self.update_sizes()  # 尺寸 + 旋转手柄的屏幕间距摆位

    def clear(self) -> None:
        for h in self._handles:
            self._scene.removeItem(h)
        self._handles.clear()
        self._targets.clear()
        self._drag = None

    def _view_ppm(self) -> float:
        return abs(self._view.transform().m11()) or 1.0

    def _on_view_changed(self) -> None:
        """``PaperView.viewChanged`` 槽：缩放后手柄的屏幕尺寸必须跟上。

        ``viewChanged`` 也会在 ``pan_by`` 里发（**每次**中键拖动一次），而平移
        不改 ppm ⇒ 先比一次缓存 ppm，相同就直接返回：既不碰手柄也不做无谓
        重算，不把这条挂到拖动路径上（PRD NFR-5：递归变换只在选择/缩放路径
        触发）。缩放才走 :meth:`update_sizes`。
        """
        ppm = self._view_ppm()
        if abs(ppm - self._last_ppm) <= 1e-12:
            return
        self.update_sizes()

    def update_sizes(self) -> None:
        """手柄恒定屏幕大小：``setScale(1/ppm)`` + 旋转手柄的屏幕间距。

        幂等；由 ``sync()``（选中/编辑后）与 ``_on_view_changed``（缩放后）
        两条路径共用，故手柄尺寸不依赖「下一次选中变化」才对上。
        """
        ppm = self._view_ppm()
        self._last_ppm = ppm
        for h in self._handles:
            h.setScale(1.0 / ppm)
        self._relayout_rotate(ppm)

    def _relayout_rotate(self, ppm: float) -> None:
        """旋转手柄摆在选框顶边上方 ``_ROTATE_GAP_PX`` **屏幕** px 处。

        场景单位随 ppm 变（``_ROTATE_GAP_PX / ppm``）⇒ 缩放视图时与手柄尺寸
        一同重算，否则缩小后手柄会陷进选区里。
        """
        rot = next((h for h in self._handles if h.kind == "rotate"), None)
        if rot is None:
            return
        rect = self.selection_rect()
        rot.setPos(QtCore.QPointF(rect.center().x(),
                                  rect.top() - _ROTATE_GAP_PX / ppm))

    def selection_rect(self) -> QtCore.QRectF:
        rect = QtCore.QRectF()
        for gi in self._targets:
            rect = rect.united(gi.sceneBoundingRect())
        return rect

    def pivot(self) -> QtCore.QPointF:
        """缩放/旋转支点，**一律页面系**：单选 = ``page_origin()``（画出来的
        本地原点，与 model 数学一致）；多选 = 并集中心。

        ⚠ 单选**不能**读 ``gi.pos()``（F1/R1）：``pos`` 是**父系**字段，而
        ``scene_pos`` 与 ``selection_rect()`` 都是页面系 —— 非恒等祖先下支点
        与手柄实际摆放位置脱节，``r0/r1`` 与旋转角全错（实测组 pos=(100,30)
        时支点误差 104.40mm、拖到两倍得 k=1.1497 而非 2.0）。
        ``self._targets`` 只装 :class:`~megapro.gui.canvas.items.PathItem`
        （``LayoutPage._selected`` 按 ``isinstance`` 过滤），``page_origin()``
        恒可用。
        """
        if len(self._targets) == 1:
            return self._targets[0].page_origin()
        return self.selection_rect().center()

    # -- 拖动 ---------------------------------------------------------------

    def begin(self, handle: _HandleItem, scene_pos: QtCore.QPointF) -> None:
        self._drag = {
            "kind": handle.kind,
            "pivot": self.pivot(),
            "start": QtCore.QPointF(scene_pos),
            # 每项多带一个「手势开始时的**页面系**原点」（gi.page_origin()）：
            # 多选缩放/旋转要在页面系里围绕支点搬原点，而 update_drag 一跑
            # ``gi.setPos`` 就把 pos 改了，届时再取 ``page_origin()``（= 活
            # pos 过祖先仿射）拿到的已是中途值 —— 必须在 begin 一次取齐。
            "old": [(gi.model_item.pos, gi.model_item.scale, gi.model_item.angle_deg,
                     QtCore.QPointF(gi.pos()), gi.scale(), gi.rotation(),
                     gi.page_origin())
                    for gi in self._targets],
        }

    def update_drag(self, handle: _HandleItem, scene_pos: QtCore.QPointF) -> None:
        d = self._drag
        if not d:
            return
        pivot = d["pivot"]
        if d["kind"] == "rotate":
            a0 = math.atan2(d["start"].y() - pivot.y(), d["start"].x() - pivot.x())
            a1 = math.atan2(scene_pos.y() - pivot.y(), scene_pos.x() - pivot.x())
            delta = math.degrees(a1 - a0)
            for gi, (_op, _os, oa, _osp, _osc, _orot, opage) in zip(
                    self._targets, d["old"]):
                gi.setRotation(oa + delta)
                if len(self._targets) > 1:
                    # 支点/手势/初始原点都在**页面系**；``pos`` 是父系字段，
                    # 写回前必须经 ``page_to_parent``（= A⁻¹）换系 —— 直接把
                    # 页面系结果写进 pos 就是 F1 的混系（实测祖先平移 (30,20)
                    # 泄漏恰好 (t.x+t.y, t.y−t.x) = (50,−10)）。
                    gi.setPos(gi.page_to_parent(self._rotated(opage, pivot, delta)))
                    self._refresh_ancestor(gi)
        else:
            r0 = math.hypot(d["start"].x() - pivot.x(), d["start"].y() - pivot.y())
            r1 = math.hypot(scene_pos.x() - pivot.x(), scene_pos.y() - pivot.y())
            k = (r1 / r0) if r0 > 1e-9 else 1.0
            k = max(k, 1e-3)
            for gi, (op, os_, _oa, osp, _osc, _orot, opage) in zip(
                    self._targets, d["old"]):
                gi.setScale(os_ * k)
                if len(self._targets) > 1:
                    # 同上：页面系里围绕支点缩放原点，换回父系再写 pos。
                    gi.setPos(gi.page_to_parent(QtCore.QPointF(
                        pivot.x() + k * (opage.x() - pivot.x()),
                        pivot.y() + k * (opage.y() - pivot.y()))))
                    self._refresh_ancestor(gi)
                else:
                    # 单选：支点 = 画出来的本地原点 ⇒ 缩放只动 scale，pos 不动
                    # （model 数学：paths 绕本地原点缩放）。
                    gi.setPos(osp)

    @staticmethod
    def _refresh_ancestor(gi) -> None:
        """``setPos`` 改了 pos 之后就地重算祖先矩阵 ``M``。

        ``M = T(−pos)·A·T(pos)`` 与 pos 耦合（items._refresh_ancestor_transform），
        手柄拖动的事件走的是手柄项、不经过 PathItem.mouseMoveEvent，不刷的话
        拖动全程按旧 pos 共轭渲染 —— 非恒等祖先下图形与光标脱节。公有的
        ``apply_model_state`` 会整份重设 scale/rotation/pos，会覆盖本手势已写的
        临时值，故用这条窄通道。
        """
        refresh = getattr(gi, "_refresh_ancestor_transform", None)
        if refresh is not None:
            refresh()

    @staticmethod
    def _rotated(p: QtCore.QPointF, pivot: QtCore.QPointF,
                 deg: float) -> QtCore.QPointF:
        rad = math.radians(deg)
        c, s = math.cos(rad), math.sin(rad)
        dx, dy = p.x() - pivot.x(), p.y() - pivot.y()
        return QtCore.QPointF(pivot.x() + dx * c - dy * s,
                              pivot.y() + dx * s + dy * c)

    def end(self, handle: _HandleItem, scene_pos: QtCore.QPointF) -> None:
        d = self._drag
        self._drag = None
        if not d or not self._targets:
            return
        self.update_drag(handle, scene_pos)
        changes = []
        for gi, (op, os_, oa, _osp, _osc, _orot, _opage) in zip(
                self._targets, d["old"]):
            it = gi.model_item
            old = {"pos": op, "scale": os_, "angle_deg": oa}
            new = {
                "pos": (float(gi.pos().x()), float(gi.pos().y())),
                "scale": float(gi.scale()),
                "angle_deg": float(gi.rotation()),
            }
            if old != new:
                changes.append((it, old, new))
        if changes:
            from megapro.gui.canvas.undo_cmds import ChangeItemPropsCommand

            page = self._page
            if page is not None:
                page._undo.push(ChangeItemPropsCommand(page, changes, "手柄变换"))
        self.sync(list(self._targets))  # clear() 会清空 self._targets，先拷贝
