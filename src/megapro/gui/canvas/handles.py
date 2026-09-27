"""选择手柄（Qt）—— 旋转 + **等比**缩放手柄（QGraphicsItem 无内建手柄）。

契约（§2.2 canvas/handles.py）：
- ``model.Item.scale`` 是标量 → 只支持等比缩放（拖角按对角距离比取 k；
  自由拉伸一律按 min 比例等比化）。
- 缩放/旋转支点 = 本地原点（= pos，与 model 数学一致）；多选整体等比缩放
  ``scale_i' = k·scale_i``、``pos_i' = A + k·(pos_i − A)``（A = 选择集 bbox
  锚点绝对坐标），保相对布局。
- 拖动中只临时 setScale/setRotation/setPos；``mouseRelease`` 一次性 push
  :class:`~megapro.gui.canvas.undo_cmds.ChangeItemPropsCommand`（old,new）。
- 手柄恒定屏幕大小：每帧 ``setScale(1/ppm)``（不用 ItemIgnoresTransformations
  —— Qt 文档警告该 flag 下坐标/碰撞必须走 deviceTransform）。
- 摆位用 ``sceneBoundingRect()``（boundingRect 不受自身变换影响）。
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

__all__ = ["SelectionHandles"]

#: 手柄标记半宽（手柄本地单位 = 屏幕 px，经 setScale(1/ppm) 恒定屏幕大小）
_HANDLE_HALF = 5.0


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
        rot.setPos(QtCore.QPointF(rect.center().x(), rect.top() - 18.0))
        self._handles.append(rot)
        for h in self._handles:
            self._scene.addItem(h)
        self.update_sizes()

    def clear(self) -> None:
        for h in self._handles:
            self._scene.removeItem(h)
        self._handles.clear()
        self._targets.clear()
        self._drag = None

    def update_sizes(self) -> None:
        """手柄恒定屏幕大小：setScale(1/ppm)。"""
        ppm = abs(self._view.transform().m11()) or 1.0
        for h in self._handles:
            h.setScale(1.0 / ppm)

    def selection_rect(self) -> QtCore.QRectF:
        rect = QtCore.QRectF()
        for gi in self._targets:
            rect = rect.united(gi.sceneBoundingRect())
        return rect

    def pivot(self) -> QtCore.QPointF:
        """缩放/旋转支点：单选 = pos（本地原点，与 model 数学一致）；多选 = 并集中心。"""
        if len(self._targets) == 1:
            p = self._targets[0].pos()
            return QtCore.QPointF(p.x(), p.y())
        return self.selection_rect().center()

    # -- 拖动 ---------------------------------------------------------------

    def begin(self, handle: _HandleItem, scene_pos: QtCore.QPointF) -> None:
        self._drag = {
            "kind": handle.kind,
            "pivot": self.pivot(),
            "start": QtCore.QPointF(scene_pos),
            "old": [(gi.model_item.pos, gi.model_item.scale, gi.model_item.angle_deg,
                     QtCore.QPointF(gi.pos()), gi.scale(), gi.rotation())
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
            for gi, (_op, _os, oa, _osp, _osc, _orot) in zip(self._targets, d["old"]):
                gi.setRotation(oa + delta)
                if len(self._targets) > 1:
                    gi.setPos(self._rotated(QtCore.QPointF(*_op), pivot, delta))
        else:
            r0 = math.hypot(d["start"].x() - pivot.x(), d["start"].y() - pivot.y())
            r1 = math.hypot(scene_pos.x() - pivot.x(), scene_pos.y() - pivot.y())
            k = (r1 / r0) if r0 > 1e-9 else 1.0
            k = max(k, 1e-3)
            for gi, (op, os_, _oa, osp, _osc, _orot) in zip(self._targets, d["old"]):
                gi.setScale(os_ * k)
                if len(self._targets) > 1:
                    gi.setPos(QtCore.QPointF(
                        pivot.x() + k * (osp.x() - pivot.x()),
                        pivot.y() + k * (osp.y() - pivot.y())))
                else:
                    gi.setPos(osp)

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
        for gi, (op, os_, oa, _osp, _osc, _orot) in zip(self._targets, d["old"]):
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
