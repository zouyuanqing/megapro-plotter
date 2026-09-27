"""P0 GUI 主窗口 gating 回归测试（offscreen，独立 QApplication）。

与 test_gui_worker.py 分开：worker 测用 QCoreApplication，而 MainWindow
是 QWidget，必须跑在 QApplication 上；同进程混用会挂死。故本文件在模块
顶部独占创建 QApplication。
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def test_mainwindow_ready_enables_controls():
    """回归：连接时禁用控制；收到 READY 必须恢复 Home/Jog/笔/命令可用。

    曾 bug：_on_state('READY') 漏调 _set_busy_ui(False)，导致状态显示
    『就绪』但所有控制按钮永久灰（真机验收发现）。
    """
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w.show()

    # 连接中：全部禁用
    w._set_busy_ui(True)
    assert not w.home_btn.isEnabled()
    assert not w._jog_btns[0].isEnabled()
    assert not w.pen_down_btn.isEnabled()
    assert not w.cmd_input.isEnabled()

    # 收到 READY：全部恢复
    w._on_state("READY")
    assert w.home_btn.isEnabled(), "READY 后 Home 仍灰"
    assert w._jog_btns[0].isEnabled(), "READY 后 Jog 仍灰"
    assert w.pen_down_btn.isEnabled(), "READY 后 笔控 仍灰"
    assert w.cmd_input.isEnabled(), "READY 后 命令输入 仍灰"
    w.close()


# ===========================================================================
# 阶段 3 作业页用例（§7/§8.3 七条）：JobSpec 内存源 + _recompile 唯一汇流 +
# 预览≡发送（parse_lines 同一份 lines）
# ===========================================================================


class _Sig:
    """假信号：emit 只记录参数。"""

    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _StubWorker:
    """假 SerialWorker（只供 GUI 侧 _require_ready / reqRunJob / reqSendSequence）。"""

    def __init__(self):
        self._state = "READY"
        self.reqRunJob = _Sig()
        self.reqSendSequence = _Sig()
        self.reqSendLine = _Sig()  # _on_sequence_done(ok=False) 的 G90 模态复位


def _make_window():
    """MainWindow + 显式 Z 标定 + 假 worker（READY）—— 参数确定，不依赖 profile 活数据。

    阶段 4 起 homed 门禁生效（执行/走边框/设工件原点需已归位）；本组用例
    关注作业链路，故显式置 ``_homed = True``（未归位拒绝行为见
    tests/test_gui_alignment.py）。
    """
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w._pen_down_z = 17.0
    w._cut_touch_z = 17.5
    w._safe_z = 30.0
    w._worker = _StubWorker()
    w._homed = True
    w._link_ready = True  # 假 worker 即「已连接」（按钮使能）
    w._refresh_gate_ui()
    return w


def _write_svg(tmp_path, points, *, name="job.svg", bed=False):
    """最小 SVG：bed=True 声明 210×210（→ preserve），否则小画板（→ anchor@bl）。"""
    attrs = ('width="210mm" height="210mm" viewBox="0 0 210 210"' if bed
             else 'width="20mm" height="20mm" viewBox="0 0 20 20"')
    p = tmp_path / name
    p.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>'
        f'<polyline points="{points}" fill="none"/></svg>',
        encoding="utf-8")
    return str(p)


def test_job_set_clear_origin_recompiles_lines_and_preview(tmp_path):
    """① 设/清工件原点后 lines 含新偏移，预览段同步平移（重编译触发集）。"""
    w = _make_window()
    assert w._load_svg_path(_write_svg(tmp_path, "5,5 15,5"))
    # 小画板 anchor@bl→(0,0)：SVG y-down (5,5)-(15,5) → 纸面 (0,0)-(10,0)
    base = list(w._job_lines)
    base_draw = list(w._preview_draw_lines())
    assert base_draw == [((0.0, 0.0), (10.0, 0.0))]
    assert "G0 X0 Y0 F1200" in base
    # 设工件原点：机器 (10,5) = 工件 (0,0) → 编译期纯平移并入 lines + 预览
    w._machine_pos = (10.0, 5.0, 30.0)
    w._on_set_origin()
    assert "G0 X10 Y5 F1200" in w._job_lines
    assert "G1 X20 Y5 F1200" in w._job_lines
    moved = w._preview_draw_lines()
    assert len(moved) == len(base_draw)
    for (p0, p1), (q0, q1) in zip(base_draw, moved):
        assert q0 == pytest.approx((p0[0] + 10.0, p0[1] + 5.0))
        assert q1 == pytest.approx((p1[0] + 10.0, p1[1] + 5.0))
    # 清原点 → 回到未偏移
    w._on_clear_origin()
    assert w._job_lines == base
    assert w._preview_draw_lines() == base_draw
    w.close()


def test_job_param_changes_never_reread_file(tmp_path, monkeypatch):
    """② 改去重/笔径/速度不触发文件读取（JobSpec 在内存；源文件删了也不炸）。"""
    import megapro.toolchain.svg_to_gcode as svg_mod

    w = _make_window()
    path = _write_svg(tmp_path, "5,5 15,5")
    assert w._load_svg_path(path)
    calls = []
    orig_parse, orig_meta = svg_mod.parse_svg, svg_mod.parse_svg_meta
    monkeypatch.setattr(svg_mod, "parse_svg",
                        lambda p: (calls.append(p), orig_parse(p))[1])
    monkeypatch.setattr(svg_mod, "parse_svg_meta",
                        lambda p: (calls.append(p), orig_meta(p))[1])
    Path(path).unlink()  # 旧实现改参数重读源文件 → 此处必炸；新实现零文件 I/O
    w.dedup_cb.setChecked(True)
    w.sort_cb.setChecked(False)
    w.pen_diameter_spin.setValue(0.8)
    w.feed_xy_spin.setValue(600.0)
    w.feed_z_spin.setValue(120.0)
    w.lift_spin.setValue(3.0)
    assert calls == [], f"参数改动触发了文件读取: {calls}"
    assert w._job_lines, "参数改动后必须重编译出 lines"
    assert any("F600" in l for l in w._job_lines)
    assert any("F120" in l for l in w._job_lines)
    w.close()


def test_job_layout_export_keeps_source_and_param_changes_ok():
    """③ 排版导出→作业后源仍在、改参数不炸（JobSpec 直传，无临时文件往返）。"""
    w = _make_window()
    from megapro.gui.layout.model import Item

    lp = w.layout_page
    lp._add_items([Item(paths=[[(10.0, 10.0), (30.0, 10.0)]], name="L")])
    assert len(lp.doc.items) == 1
    lp._on_export()  # export_requested(JobSpec) → MainWindow._on_layout_export
    assert len(lp.doc.items) == 1, "排版源必须保持可用（导出不消费源）"
    assert w.tabs.currentIndex() == 0
    assert w._job_spec is not None
    assert w._job_spec.placement.mode == "preserve"  # 版面坐标即工件坐标
    assert w._job_lines and "G0 X10 Y10 F1200" in w._job_lines
    # 改参数（旧实现会重读已删临时文件 → 炸）：全部走 _recompile
    w.feed_xy_spin.setValue(600.0)
    w.pen_diameter_spin.setValue(0.8)
    w.dedup_cb.setChecked(True)
    w.margin_spin.setValue(2.0)
    assert any("F600" in l for l in w._job_lines)
    assert [list(p) for p in w._job_spec.paths_paper][0] == \
        [(10.0, 10.0), (30.0, 10.0)]  # 几何保留
    w.close()


def test_job_preview_draw_matches_parse_of_sent_lines(tmp_path):
    """④ parse_lines(_job_lines) 的 DRAW 段 ≡ 预览路径（同一份 lines，单一真源）。"""
    w = _make_window()
    assert w._load_svg_path(_write_svg(tmp_path, "5,5 15,5 15,15"))
    from megapro.gui.gcode_parse import parse_lines

    segs = parse_lines(w._job_lines, z_down=17.0, z_safe=30.0, tool="pen")
    assert segs == list(w._job_compiled.segments)  # compile 回读即同一份
    draw = [((s.p0[0], s.p0[1]), (s.p1[0], s.p1[1]))
            for s in segs if s.kind == "DRAW" and s.p0 is not None and s.p0 != s.p1]
    assert draw, "应有落笔段"
    assert w._preview_draw_lines() == draw
    w.close()


def test_job_out_of_bounds_disables_run(tmp_path):
    """⑤ 越界禁执行：violations → runnable=False → 执行按钮灰 + _on_run_job 拒。"""
    w = _make_window()
    assert w._load_svg_path(_write_svg(tmp_path, "5,5 250,5"))
    assert w._job_compiled is not None
    assert w._job_compiled.bounds.violations
    assert not w._job_compiled.runnable
    assert not w.run_btn.isEnabled(), "越界必须禁执行"
    w._on_run_job()
    assert not w._worker.reqRunJob.calls, "越界作业不得发给 worker"
    assert "禁执行" in w.console.toPlainText()
    w.close()


def test_job_tool_switch_keeps_geometry_swaps_zmap(tmp_path):
    """⑥ 工具切换保留几何、换 ZMap（pen_down_z 17.0 ↔ cut_touch_z−深度 16.5）。"""
    w = _make_window()
    w.cut_depth_spin.setValue(1.0)  # 裁刀下压 = 17.5 − 1.0 = 16.5
    assert w._load_svg_path(_write_svg(tmp_path, "5,5 15,5"))
    geom = [list(p) for p in w._job_spec.paths_paper]
    assert w._job_compiled.meta["zmap"].down_z == pytest.approx(17.0)
    w.tool_combo.setCurrentIndex(1)  # 切到刀
    assert w._tool == "knife"
    assert [list(p) for p in w._job_spec.paths_paper] == geom, "切换工具不得丢几何"
    assert w._job_compiled.meta["zmap"].down_z == pytest.approx(16.5)
    assert any(l.startswith("G1 Z16.5") for l in w._job_lines)
    w.tool_combo.setCurrentIndex(0)  # 切回笔
    assert [list(p) for p in w._job_spec.paths_paper] == geom
    assert w._job_compiled.meta["zmap"].down_z == pytest.approx(17.0)
    assert any(l.startswith("G1 Z17") for l in w._job_lines)
    w.close()


def test_job_recompile_noop_while_running_backfills_after_done(tmp_path):
    """⑦ 运行中 _recompile no-op+提示；jobDone 后补编译；进度高亮用执行快照。"""
    w = _make_window()
    assert w._load_svg_path(_write_svg(tmp_path, "5,5 15,5"))
    base = list(w._job_lines)
    w._on_run_job()
    assert w._job_running
    assert w._worker.reqRunJob.calls[-1][0] == base, "worker 收到同一份 lines"
    assert w._running_compiled is not None
    assert list(w._running_compiled.lines) == base  # 不可变快照同源
    # 执行中：参数/原点改动 → no-op + 提示
    w._machine_pos = (10.0, 5.0, 30.0)
    w._on_set_origin()
    w.feed_xy_spin.setValue(600.0)
    assert w._job_lines == base, "执行中不得重编译"
    assert "执行中" in w.console.toPlainText()
    # 进度高亮：映射自执行开始时的快照段（line_range[1] <= done 全亮）
    total = len(base)
    w._on_job_progress(total, total)
    expect = {((float(s.p0[0]), float(s.p0[1])), (float(s.p1[0]), float(s.p1[1])))
              for s in w._running_compiled.segments
              if s.kind == "DRAW" and s.p0 is not None and s.p0 != s.p1
              and s.line_range[1] <= total}
    assert w._gcode_item._done_draw == expect
    # jobDone → 补编译（执行期累积的偏移/速度生效）
    w._on_job_done(True)
    assert not w._job_running
    assert w._job_lines != base
    assert any("F600" in l for l in w._job_lines)
    assert "G0 X10 Y5 F600" in w._job_lines  # 新偏移 + 新速度都进了补编译
    w.close()


def test_job_livemarker_never_synthesized_and_freezes_in_run(tmp_path):
    """LiveMarker 语义（评审 medium #1 + 阶段 4 issue#6）：只认回报位置、绝不按
    已发 G-code 推算；执行期无位置回报 → set_stale 冻结置灰。**执行期迟到回报**
    （reqPoll 与 reqRunJob 排队竞态）也只更新 DRO/缓存、不点亮十字 ——
    LiveMarker.set_machine 在冻结态丢弃回报（阶段 5 竞态修复），_on_position
    再按 _job_running 双保险（仅显示层）；执行结束后的回报恢复正常更新。"""
    w = _make_window()
    assert w._load_svg_path(_write_svg(tmp_path, "5,5 15,5"))
    w._on_position(5.0, 6.0, 30.0)
    assert (w._live_marker.pos().x(), w._live_marker.pos().y()) == (5.0, 6.0)
    assert w._live_marker._stale is False
    w._on_run_job()
    assert w._job_running
    assert w._live_marker._stale is True, "执行期必须标注冻结（无位置回报）"
    x0, y0 = w._live_marker.pos().x(), w._live_marker.pos().y()
    w._on_job_progress(3, 9)
    # 进度不得合成位置（ok≠已移动：软件不下「已移动」结论）
    assert (w._live_marker.pos().x(), w._live_marker.pos().y()) == (x0, y0)
    # 执行期迟到回报：DRO/缓存照实更新，十字保持冻结（不伪造成实时态）
    w._on_position(7.0, 8.0, 30.0)
    assert w._machine_pos == (7.0, 8.0, 30.0)
    assert (w._live_marker.pos().x(), w._live_marker.pos().y()) == (x0, y0)
    assert w._live_marker._stale is True, "执行期迟到回报不得解冻点亮十字"
    w._on_job_done(True)
    assert w._live_marker._stale is False, "jobDone 后解冻（轮询恢复）"
    w._on_position(9.0, 9.0, 30.0)  # 执行结束后的回报正常更新
    assert (w._live_marker.pos().x(), w._live_marker.pos().y()) == (9.0, 9.0)
    assert w._live_marker._stale is False
    assert "LiveMarker 冻结" in w.console.toPlainText()
    w.close()


def test_job_recompile_param_error_keeps_previous_lines(tmp_path):
    """GUI 汇流点负 Z 算术面（评审 medium #2）：cut_z_for_depth(触纸<切深)
    的 ValueError 必须被 _recompile 兜住（提示 + 保持上一份 lines/预览），
    不得抛穿 Qt 槽。"""
    w = _make_window()
    w._cut_touch_z = 2.0
    w.cut_depth_spin.setValue(5.0)  # 2.0 − 5.0 < 0 → cut_z_for_depth ValueError
    assert w._load_svg_path(_write_svg(tmp_path, "5,5 15,5"))
    base = list(w._job_lines)
    base_draw = list(w._preview_draw_lines())
    # 切刀 → _current_zmap → cut_z_for_depth 抛 ValueError：受控，不抛穿
    w.tool_combo.setCurrentIndex(1)
    assert w._tool == "knife"
    assert "编译失败（参数）" in w.console.toPlainText()
    assert w._job_lines == base, "编译失败必须保持上一份 lines"
    assert w._preview_draw_lines() == base_draw, "预览与 lines 保持同一份"
    # 任何其它触发器同样兜住
    w.feed_xy_spin.setValue(900.0)
    assert w._job_lines == base
    assert "编译失败（参数）" in w.console.toPlainText()
    # 参数恢复合法 → 重新编译成功（刀 ZMap 生效）
    w._cut_touch_z = 17.5
    w.cut_depth_spin.setValue(1.0)
    assert w._job_lines != base
    assert any(l.startswith("G1 Z16.5") for l in w._job_lines)
    w.close()
