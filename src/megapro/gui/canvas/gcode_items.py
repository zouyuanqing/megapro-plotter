"""G-code 走线/实机标记（Qt）—— 机器预览页附加层（§2.2/§3.2）。

契约（docs/preview-layout-blueprint.md §2.2 canvas/gcode_items.py）：
- :class:`GcodePathItem` 吃 ``gcode_parse.Segment``（= ``parse_lines(将发送
  的同一份 lines)`` 的回读，§3.1 预览≡发送单一真源）：三条 QPainterPath
  分色 —— TRAVEL 红虚 / DRAW 蓝实（tool='knife' 紫实）/ PLUNGE·RETRACT 灰
  短竖标；零长段（``p0 == p1``）渲染跳过（解析保留）。
- ``set_progress(done, segments=None)``：``done`` = worker 1 基已发送行数
  （``line_range`` 为 0 基半开 ``[i0, i1)``），段全亮 iff
  ``line_range[1] <= done``（天然对齐）；``segments`` 传执行开始时的不可变
  快照（与 worker 收到的 lines 拷贝同源），缺省用自身段。
- :class:`LiveMarker`：M114 logical 十字（坐标 ≡ 纸面 mm）。**ok ≠ 已移动、
  M114 的 Count 不可信**（AGENTS/REPORT §6）—— 只显示回报的逻辑值，不作
  物理真值，也**绝不按已发 G-code 推算位置**（软件不下「已移动」结论）。
- **执行期位置冻结（如实声明）**：作业执行中 worker 工作线程被 run_job 发送
  循环占住（worker.py run_job），job 行响应只有 'ok'（不触发 position 信号），
  且 GUI 仅 READY 态轮询 M114 → 执行窗口内**没有任何位置回报**，十字停在
  执行前位置。此时用 :meth:`LiveMarker.set_stale` 显示灰色虚十字（提示
  「执行期无位置回报」），避免「走线在动、十字不动」被误读为实机脱节——
  真正的脱节对照需停机读 M114/人工复核（§5-⑥/§9 风险表）；执行中实时回报
  需改 worker（阶段 3 约束 worker 零 diff，故本阶段如实冻结而非伪造）。
  冻结态下**迟到的 M114 回报由 LiveMarker.set_machine 丢弃**（阶段 5 竞态
  修复）：不得把置灰虚十字点亮/位移。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

__all__ = ["GcodePathItem", "LiveMarker"]


class GcodePathItem(QtWidgets.QGraphicsItem):
    """G-code 走线三色预览（TRAVEL 红虚 / DRAW 蓝实·紫实 / Z 灰竖标）。

    段来源 = ``parse_lines(将发送的同一份 lines)``；进度高亮映射的数据源是
    执行开始时的不可变快照（:meth:`set_progress` 显式传入）。
    """

    def __init__(self, segments, tool: str = "pen", parent=None) -> None:
        super().__init__(parent)
        self.segments = tuple(segments)
        self.tool = tool
        #: DRAW 段（滤零长）的 (p0, p1) 纸面 mm 2 点线 —— 与 parse_lines 同源
        self.draw_lines: list = []
        self.travel_lines: list = []
        self.z_marks: list = []
        self._done_draw: set = set()
        for s in self.segments:
            if s.p0 is None:
                continue
            p0 = (float(s.p0[0]), float(s.p0[1]))
            p1 = (float(s.p1[0]), float(s.p1[1]))
            if s.kind == "DRAW":
                if s.p0 != s.p1:
                    self.draw_lines.append((p0, p1))
            elif s.kind == "TRAVEL":
                if p0 != p1:
                    self.travel_lines.append((p0, p1))
            elif s.kind in ("PLUNGE", "RETRACT"):
                self.z_marks.append(p1)
        pts = [p for ln in self.draw_lines + self.travel_lines for p in ln]
        pts += [tuple(m[:2]) for m in self.z_marks]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self._rect = QtCore.QRectF(
                QtCore.QPointF(min(xs) - 1.0, min(ys) - 1.0),
                QtCore.QPointF(max(xs) + 1.0, max(ys) + 1.0),
            )
        else:
            self._rect = QtCore.QRectF(0.0, 0.0, 1.0, 1.0)

    def set_progress(self, done: int, segments=None) -> None:
        """进度高亮：``segments`` 传执行开始时的不可变快照（缺省用自身段）。"""
        segs = self.segments if segments is None else tuple(segments)
        self._done_draw = {
            ((float(s.p0[0]), float(s.p0[1])), (float(s.p1[0]), float(s.p1[1])))
            for s in segs
            if s.kind == "DRAW" and s.p0 is not None and s.p0 != s.p1
            and s.line_range[1] <= int(done)
        }
        self.update()

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return self._rect

    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        pen_t = QtGui.QPen(QtGui.QColor("#c00"))
        pen_t.setWidthF(0)
        pen_t.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen_t)
        for p0, p1 in self.travel_lines:
            painter.drawLine(QtCore.QPointF(*p0), QtCore.QPointF(*p1))
        col = QtGui.QColor("#08c" if self.tool != "knife" else "#80a")
        pen_pending = QtGui.QPen(col.lighter(150))
        pen_pending.setWidthF(0)
        pen_done = QtGui.QPen(col)
        pen_done.setWidthF(0)
        painter.setPen(pen_pending)
        for p0, p1 in self.draw_lines:
            if (p0, p1) not in self._done_draw:
                painter.drawLine(QtCore.QPointF(*p0), QtCore.QPointF(*p1))
        painter.setPen(pen_done)
        for p0, p1 in self.draw_lines:
            if (p0, p1) in self._done_draw:
                painter.drawLine(QtCore.QPointF(*p0), QtCore.QPointF(*p1))
        pen_z = QtGui.QPen(QtGui.QColor("#888"))
        pen_z.setWidthF(0)
        painter.setPen(pen_z)
        for x, y in self.z_marks:
            painter.drawLine(QtCore.QPointF(x, y - 1.5), QtCore.QPointF(x, y + 1.5))


class LiveMarker(QtWidgets.QGraphicsItem):
    """M114 logical 实时位置十字（计划走线 + 实机十字同屏，LaserGRBL 式）。

    来源 = 回报的 logical 坐标（≡ 纸面 mm，coords.machine_from_paper 恒等）。
    **ok ≠ 已移动、M114 的 Count 不可信**（AGENTS/REPORT §6）—— 只显示逻辑
    值，不作物理真值；绝不按已发 G-code 推算位置。十字与走线脱节时按已知
    硬件问题处置（暂停人查，REPORT §6），软件不下「已移动」结论。

    **执行期冻结**（见模块 docstring）：run_job 期间无任何位置回报（worker
    发送循环占线程、job 行响应只有 'ok'、M114 仅 READY 轮询），十字停在执行
    前位置 —— :meth:`set_stale` 置灰虚十字 + tooltip 声明「执行期无位置回报」，
    避免被误读为实机脱节；冻结态下迟到的回报由 :meth:`set_machine` 丢弃
    （不点亮置灰虚十字）。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setZValue(100.0)
        self._stale = False
        self.setToolTip(
            "M114 logical 十字：ok≠已移动、Count 不可信；执行期无位置回报"
            "（M114 不轮询），十字冻结在执行前位置")
        self.hide()

    def set_machine(self, x: float, y: float) -> None:
        """回报到达即更新位置（绝不接受无回报的推算位置）。

        **冻结态迟到回报不点亮**（阶段 5 竞态修复）：``set_stale(True)``
        之后才到达的 M114 回报（reqPoll 与 reqRunJob 的排队竞态）直接丢弃
        —— 不解冻、不点亮相、也不位移（执行期十字冻结在执行前位置，见模块
        docstring）。解冻只由 :meth:`set_stale`（False，jobDone 后）触发。
        """
        if self._stale:
            return  # 迟到回报：保持置灰虚十字（不点亮、不位移）
        self.setPos(float(x), float(y))
        self.show()
        self.update()

    def set_stale(self, stale: bool) -> None:
        """执行期无位置回报：置灰虚十字（不是新位置，只是显示状态）。"""
        self._stale = bool(stale)
        self.update()

    def boundingRect(self) -> QtCore.QRectF:  # noqa: N802
        return QtCore.QRectF(-4.0, -4.0, 8.0, 8.0)

    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        pen = QtGui.QPen(QtGui.QColor("#aaa" if self._stale else "#f80"))
        pen.setWidthF(0)
        if self._stale:
            pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(-4.0, 0.0), QtCore.QPointF(4.0, 0.0))
        painter.drawLine(QtCore.QPointF(0.0, -4.0), QtCore.QPointF(0.0, 4.0))
