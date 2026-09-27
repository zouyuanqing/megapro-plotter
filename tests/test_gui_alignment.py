"""阶段 4 对准工具 + homed 门禁（offscreen，独立 QApplication）。

契约：docs/preview-layout-blueprint.md §4.1-3（对准工具条）、§5（对准工作流）、
§6（复位/归位门禁 + 反馈可信度）、§7 阶段 4、§8.3（offscreen 断言纪律）。

覆盖：
- 对准序列（Frame/MoveTo/GotoOrigin）经 UI 发出的 golden 与 homed 门禁；
- Frame 轮数 <1 归 1（UI 边界；构造器层 rounds<1 抛 MachineError 被
  tests/test_gui_job.py:464-465 钉死，该文件在本阶段不动清单）；
- homed 置位/清零规则（仅一键寻零 sequenceDone(ok=True) 置位；
  连接打开/FAULT/ESTOP/断开清零；guard 拦一半 ok=False 不置位）；
- 未勾『允许 Z』禁用一键寻零（内含 G28 Z）；_send 预检 allow_z 传 UI 真值；
- 空跑校验 = compile_job(dry_run=True)：Z 恒 safe_z、无落笔/下压、段无 DRAW/
  PLUNGE（全部按 TRAVEL/SETUP 对待），可选发送。

与 test_gui_window.py 同约定：模块顶部独占创建 QApplication。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# --- 假 worker / 假 transport -----------------------------------------------


class _Sig:
    """假信号：emit 只记录参数。"""

    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _StubWorker:
    """假 SerialWorker（只供 GUI 侧 reqSendSequence / reqRunJob 记录）。"""

    def __init__(self):
        self._state = "READY"
        self.reqRunJob = _Sig()
        self.reqSendSequence = _Sig()
        self.reqSendLine = _Sig()  # _on_sequence_done(ok=False) 的 G90 模态复位


class _FakeLink:
    """假串口会话：记录发送行（estop 裸发走 write）。"""

    def __init__(self):
        self.writes = []

    def write(self, data):  # noqa: D401 - transport 接口
        self.writes.append(data)

    def close(self):
        pass


def _fake_send(ser, line, timeout=30.0, log=None):
    """假 send_and_wait_ok：记行并回 'ok'（行级成功）。"""
    ser.writes.append(line)
    return "ok"


def _make_window(*, homed=False, worker=None):
    """MainWindow + 显式标定 + 假 worker（READY）；homed 默认 False（未归位）。

    参数全部显式给定（含 feed_xy=1200），不依赖 profile/预设活数据。
    """
    from megapro.gui.main_window import MainWindow

    w = MainWindow()
    w._pen_down_z = 17.0
    w._cut_touch_z = 17.5
    w._safe_z = 30.0
    w._safe_x = 0.0
    w._safe_y = 50.0
    w._feed_xy = 1200.0
    w._feed_z = 300.0
    w._worker = worker if worker is not None else _StubWorker()
    w._homed = homed
    w._link_ready = True  # 假 worker 即「已连接」（按钮使能）
    w._refresh_gate_ui()
    return w


def _write_svg(tmp_path, points="5,5 15,5 15,15", name="job.svg"):
    """最小 SVG（小画板 → anchor@bl→(0,0)）：3 点 L 形，bbox (0,0)-(10,10)。"""
    p = tmp_path / name
    p.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="20mm" height="20mm" viewBox="0 0 20 20">'
        f'<polyline points="{points}" fill="none"/></svg>',
        encoding="utf-8")
    return str(p)


# --- Frame 轮数归一（UI 边界） ----------------------------------------------


def test_frame_rounds_below_one_clamp_to_one_at_ui_boundary():
    """rounds<1 归 1：在轮数进入构造器之前的 UI 边界归一（1/3 轮可选，§4.1）。

    构造器 build_frame_sequence 对 rounds<1 抛 MachineError 的行为保持不变
    （tests/test_gui_job.py:464-465 钉死，本阶段不动该文件）。
    """
    from megapro.gui.main_window import MainWindow

    assert MainWindow._clamp_rounds(0) == 1
    assert MainWindow._clamp_rounds(-2) == 1
    assert MainWindow._clamp_rounds(0.4) == 1
    assert MainWindow._clamp_rounds(1) == 1
    assert MainWindow._clamp_rounds(3) == 3
    assert MainWindow._clamp_rounds(None) == 1  # 非法输入归 1


# --- homed 门禁：未归位拒绝 执行/走边框/设工件原点（中文原因） ---------------


def test_unhomed_rejects_run_frame_set_origin_with_cn_reason(tmp_path):
    w = _make_window(homed=False)
    assert w._load_svg_path(_write_svg(tmp_path))

    # 执行：按钮禁 + 拒发 + 中文原因
    assert not w.run_btn.isEnabled()
    w._on_run_job()
    assert not w._worker.reqRunJob.calls, "未归位不得发 job"
    assert "未归位" in w.status_label.text()
    assert "禁执行：未归位" in w.console.toPlainText()

    # 走边框：拒发 + 中文原因
    assert not w.frame_btn.isEnabled()
    w._on_frame()
    assert not w._worker.reqSendSequence.calls, "未归位不得走边框"
    assert "走边框被拒：未归位" in w.console.toPlainText()

    # 设工件原点：拒设 + 中文原因
    assert not w.set_origin_btn.isEnabled()
    w._machine_pos = (10.0, 5.0, 30.0)
    w._on_set_origin()
    assert w._work_origin is None, "未归位不得设工件原点"
    assert "设工件原点被拒：未归位" in w.console.toPlainText()
    assert "未归位" in w.status_label.text()
    w.close()


def test_unhomed_rejects_corner_center_goto_origin(tmp_path):
    """四角/中心点检与回原点同为绝对定位动作（§5 工作流在归位之后）→ 一并门禁。"""
    w = _make_window(homed=False)
    assert w._load_svg_path(_write_svg(tmp_path))
    for btn in (w.check_bl_btn, w.check_br_btn, w.check_tr_btn,
                w.check_tl_btn, w.check_center_btn, w.goto_origin_btn):
        assert not btn.isEnabled()
    w._on_check_point("c")
    w._on_goto_origin()
    assert not w._worker.reqSendSequence.calls
    assert "点检被拒：未归位" in w.console.toPlainText()
    assert "回原点被拒：未归位" in w.console.toPlainText()
    w.close()


def test_homed_enables_alignment_and_frame_matches_builder(tmp_path):
    """已归位：对准按钮可用；走边框/点检发出的序列 = 构造器 golden。"""
    from megapro.gui.controller import (
        build_frame_sequence,
        build_move_to_sequence,
    )

    w = _make_window(homed=True)
    assert w._load_svg_path(_write_svg(tmp_path))
    for btn in (w.frame_btn, w.check_bl_btn, w.check_center_btn,
                w.goto_origin_btn, w.set_origin_btn, w.run_btn):
        assert btn.isEnabled()

    w._on_frame()
    seq = w._worker.reqSendSequence.calls[-1][0]
    assert seq == build_frame_sequence((0.0, 0.0, 10.0, 10.0), 30.0, 1200.0,
                                       rounds=1)
    assert seq[0] == "G90" and seq[-1] == "M400"

    # 3 轮可选
    w.frame_rounds_combo.setCurrentIndex(1)  # "3 轮"
    w._on_frame()
    seq3 = w._worker.reqSendSequence.calls[-1][0]
    assert seq3 == build_frame_sequence((0.0, 0.0, 10.0, 10.0), 30.0, 1200.0,
                                        rounds=3)
    assert len(seq3) == 18

    # 四角/中心点检 = build_move_to_sequence（bbox (0,0)-(10,10)）
    w._on_check_point("bl")
    assert w._worker.reqSendSequence.calls[-1][0] == \
        build_move_to_sequence(0.0, 0.0, 30.0, 1200.0)
    w._on_check_point("tr")
    assert w._worker.reqSendSequence.calls[-1][0] == \
        build_move_to_sequence(10.0, 10.0, 30.0, 1200.0)
    w._on_check_point("c")
    assert w._worker.reqSendSequence.calls[-1][0] == \
        build_move_to_sequence(5.0, 5.0, 30.0, 1200.0)

    # 回原点：未设原点拒；设过原点 → 移到原点机器点
    w._on_goto_origin()
    assert "回原点被拒：未设工件原点" in w.console.toPlainText()
    w._machine_pos = (3.0, 4.0, 30.0)
    w._on_set_origin()
    assert w._work_origin == (3.0, 4.0)
    w._on_goto_origin()
    assert w._worker.reqSendSequence.calls[-1][0] == \
        build_move_to_sequence(3.0, 4.0, 30.0, 1200.0)
    w.close()


# --- homed 置位/清零规则 ----------------------------------------------------


def test_homed_set_only_on_home_sequence_done_ok():
    """homed 仅在「一键寻零（home 序列）」sequenceDone(ok=True) 后置位。"""
    w = _make_window(homed=False)

    # 非 home 序列跑完（ok=True）：homed 不变
    assert w._send(["G0 X1 F600"])
    w._on_sequence_done(True)
    assert w._homed is False

    # 一键寻零：发出即记 pending（假 worker 不回执，手动补 sequenceDone 两分支）
    w._on_home_full_clicked()
    assert w._pending_home is True
    w._on_sequence_done(False)  # guard 拦一半/中断 → 保持未归位
    assert w._homed is False
    assert w._pending_home is False
    assert "归位序列未完整执行" in w.console.toPlainText()

    w._on_home_full_clicked()
    assert w._pending_home is True
    w._on_sequence_done(True)  # 全行成功 → 置位
    assert w._homed is True
    assert w._pending_home is False
    assert "归位完成（homed）" in w.console.toPlainText()
    assert w.frame_btn.isEnabled(), "homed 后对准门禁应解除"
    w.close()


def test_homed_cleared_on_connect_fault_estop_disconnect(monkeypatch):
    """连接打开/FAULT/ESTOP/断开一律清 homed（ESTOP 弹窗在测试里静默）。"""
    from PySide6 import QtWidgets

    from megapro.gui.main_window import MainWindow

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        lambda *a, **k: None)
    w = _make_window(homed=True)
    assert w._homed is True

    w._on_state("CONNECTING")  # 连接打开（主板复位）
    assert w._homed is False

    w._homed = True
    w._on_state("FAULT")
    assert w._homed is False

    w._homed = True
    w._on_state("ESTOP")
    assert w._homed is False

    w._homed = True
    w._pending_home = True
    w._on_state("DISCONNECTED")
    assert w._homed is False and w._pending_home is False

    # READY 不清（序列结束回 READY 之后才由 _on_sequence_done 决定）
    w._homed = True
    w._on_state("READY")
    assert w._homed is True
    # 注：连接打开的另一清零点在 _spawn_worker（真串口路径，测试不触发）
    w.close()


def test_guard_half_block_keeps_unhomed_end_to_end():
    """复刻评审 break #6 假阳性：序列被 guard 拦一半 → sequenceDone(ok=False)
    → homed 不置位（G28 Z 未 allow_z 场景）。

    说明（评审 issue#1 修复后）：UI 侧已无法造出「预检放行而 worker 拦」的两源
    分叉（_sync_worker_guard_params 把 UI 真值推平给 worker，见
    test_worker_guard_params_follow_ui_truth）。本用例在 **worker 层**构造拦一
    半（逐行 guard 比预检严的残余路径，如 worker 侧配置差异/后续 guard 收紧），
    验证的是链条后半：ok=False → homed 不置位 + 中文提示。
    """
    from megapro.gui.controller import build_full_home_sequence
    from megapro.gui.worker import SerialWorker

    link_box = []

    def _open(port, baud, log=None):
        link = _FakeLink()
        link_box.append(link)
        return link, 250000

    worker = SerialWorker("FAKE", None, allow_z=False,
                          open_session=_open, send_and_wait_ok=_fake_send)
    w = _make_window(homed=False, worker=worker)
    worker.sequenceDone.connect(w._on_sequence_done)
    worker.open()
    assert worker._state == "READY"
    w.allow_z_cb.setChecked(False)  # 同源：UI 与 worker 判据一致（都无 allow_z）

    w._pending_home = True  # 模拟一键寻零序列在途（等 sequenceDone 裁决）
    worker.send_sequence(build_full_home_sequence(safe_y=50.0, safe_z=30.0),
                         home=True)
    sent = link_box[-1].writes
    assert "G28 X Y" in sent, f"拦一半：前半应已发出，实际 {sent}"
    assert "G28 Z" not in sent, f"被 guard 拦下的行不得发出，实际 {sent}"
    assert "G0 Y50 F1200" not in sent, "中断后不得再发剩余行"
    assert w._homed is False, "guard 拦一半的归位序列不得置 homed"
    assert w._pending_home is False
    assert "归位序列未完整执行" in w.console.toPlainText()
    w.close()


def test_worker_guard_params_follow_ui_truth(monkeypatch):
    """评审 issue#1 回归：allow_z/lift_configured 两处判据**同源**。

    连接后切『允许 Z』/预设改 _pen_down_z/Z 标定回写，worker 的逐行 guard 判据
    必须与 _send 预检同步（旧实现 worker 只持连接时快照 → 分叉 → 预检放行而
    worker 中途拦 = break #6 假阳性路径 + 半截序列遗留 G91 模态）。
    """
    from megapro.gui.controller import sequence_ok
    from megapro.gui.worker import SerialWorker

    def _open(port, baud, log=None):
        return _FakeLink(), 250000

    worker = SerialWorker("FAKE", None, allow_z=True,
                          open_session=_open, send_and_wait_ok=_fake_send)
    w = _make_window(homed=True, worker=worker)
    worker.open()
    assert (worker.allow_z, worker.lift_configured) == (True, True)

    # ① 连接后切『允许 Z』（toggled 漏斗）
    w.allow_z_cb.setChecked(False)
    assert worker.allow_z is False, "切勾选后 worker 判据必须跟随"
    assert worker.lift_configured is True  # _pen_down_z=17.0 未变
    assert not w._send(["G28 Z"]), "预检与 worker 同判：G28 Z 双双拒"
    assert sequence_ok(["G28 Z"], allow_z=worker.allow_z,
                       lift_configured=worker.lift_configured) != []

    # ② 预设应用改 _pen_down_z（presets.apply_config → _on_preset_applied 漏斗）
    w._pen_down_z = None
    w._on_preset_applied()
    assert worker.lift_configured is False, "标定真值变更后 worker 判据必须跟随"
    assert worker.allow_z is False

    # ③ Z 标定回写漏斗（_reload_profile_z）
    monkeypatch.setattr(w, "_load_profile",
                        lambda: {"pen_down_z": "17.0", "cut_touch_z": "17.5"})
    w.allow_z_cb.setChecked(True)
    w._reload_profile_z()
    assert w._pen_down_z == 17.0
    assert (worker.allow_z, worker.lift_configured) == (True, True)
    w.close()


def test_dry_run_send_refused_when_unhomed(tmp_path):
    """评审 issue#2：『空跑发送』= 真实绝对坐标走位，必须过 homed 门禁。"""
    w = _make_window(homed=False)
    assert w._load_svg_path(_write_svg(tmp_path))
    w.dry_run_send_cb.setChecked(True)
    w._on_dry_run()
    assert w._dry_run_compiled is not None and w._dry_run_compiled.lines, \
        "编译/报告仍给（离线检查不受门禁）"
    assert not w._worker.reqSendSequence.calls, "未归位不得物理空跑"
    assert "空跑发送被拒：未归位" in w.console.toPlainText()
    w.close()


def test_alignment_refuses_out_of_travel_bbox(tmp_path):
    """评审 issue#3：bbox 超行程 → 软限位会静默钳位（描出的框≠真实 bbox）→
    走边框/点检直接拒绝并给中文原因。"""
    w = _make_window(homed=True)
    assert w._load_svg_path(_write_svg(tmp_path))  # 内容 bbox (0,0)-(10,10)
    w._machine_pos = (205.0, 205.0, 30.0)
    w._on_set_origin()  # 原点把内容推到 (205,205)-(215,215) → 超 210 行程
    assert w._content_bbox() == pytest.approx((205.0, 205.0, 215.0, 215.0))

    w._on_frame()
    assert not w._worker.reqSendSequence.calls, "超行程 bbox 不得发描框"
    assert "走边框被拒" in w.console.toPlainText()
    assert "超出机器行程" in w.console.toPlainText()
    w._on_check_point("c")
    assert not w._worker.reqSendSequence.calls, "超行程 bbox 不得发点检"
    assert "中心点检被拒" in w.console.toPlainText()
    w.close()


def test_dry_run_send_refused_on_out_of_bounds(tmp_path):
    """评审 issue#2/#3：strict=False 编译保留越界几何 —— 物理空跑发送必须按
    violations 拦截（发出去就是越界 G0）。"""
    w = _make_window(homed=True)
    assert w._load_svg_path(_write_svg(tmp_path))
    w._machine_pos = (205.0, 205.0, 30.0)
    w._on_set_origin()
    assert w._job_compiled.bounds.violations
    w.dry_run_send_cb.setChecked(True)
    w._on_dry_run()
    assert not w._worker.reqSendSequence.calls, "越界内容不得物理空跑"
    assert "空跑发送被拒：内容越界" in w.console.toPlainText()
    # 对照：未勾发送时只报告不拦（编译即检查工具本体）
    w.dry_run_send_cb.setChecked(False)
    w._on_dry_run()
    assert w._dry_run_compiled is not None
    w.close()


def test_check_point_wraps_machine_error(tmp_path):
    """评审 low：_on_check_point 对构造器 MachineError 兜底（如 safe_z<0），不抛穿槽。"""
    w = _make_window(homed=True)
    assert w._load_svg_path(_write_svg(tmp_path))
    w._safe_z = -1.0  # 异常 profile 面：build_move_to_sequence 会抛
    w._on_check_point("c")
    assert not w._worker.reqSendSequence.calls
    assert "中心点检被拒" in w.console.toPlainText()
    w.close()


def test_spawn_worker_clears_homed(monkeypatch):
    """评审 low（补测）：连接打开入口 _spawn_worker 清 homed/_link_ready。

    用假 SerialWorker 替身避开真串口（_spawn_worker 会起 QThread + 真 open）。
    """
    import megapro.gui.main_window as mw_mod

    class _FakeSig:
        def __init__(self):
            self.slots = []

        def connect(self, cb):
            self.slots.append(cb)

        def emit(self, *args):
            pass

        def __call__(self, *args):  # QThread.started.connect(reqOpen) 需可调用
            pass

    class _FakeWorker:
        def __init__(self, *args, **kwargs):
            for name in ("stateChanged", "status", "echo", "position",
                         "sequenceDone", "log", "progress", "jobDone",
                         "reqOpen", "reqClose"):
                setattr(self, name, _FakeSig())

        def moveToThread(self, _thread):
            pass

    monkeypatch.setattr(mw_mod, "SerialWorker", _FakeWorker)
    w = _make_window(homed=True)
    assert w._homed is True
    w._spawn_worker()  # 连接打开 = 主板复位 → 清 homed
    assert w._homed is False
    assert w._pending_home is False
    assert w._link_ready is False
    w._teardown_thread()
    w.close()


def test_home_sequence_done_ok_true_sets_homed_end_to_end():
    """对照分支：worker allow_z=True → 全行成功 → sequenceDone(ok=True) → homed。"""

    from megapro.gui.worker import SerialWorker

    link_box = []

    def _open(port, baud, log=None):
        link = _FakeLink()
        link_box.append(link)
        return link, 250000

    worker = SerialWorker("FAKE", None, allow_z=True,
                          open_session=_open, send_and_wait_ok=_fake_send)
    w = _make_window(homed=False, worker=worker)
    worker.sequenceDone.connect(w._on_sequence_done)
    worker.open()

    w._on_home_full_clicked()
    sent = link_box[-1].writes
    assert "G28 Z" in sent and "G0 Y50 F1200" in sent  # 全序列发出
    assert w._homed is True
    assert w._pending_home is False
    w.close()


# --- 未勾『允许 Z』禁用一键寻零 + _send 预检 allow_z UI 真值 -----------------


def test_home_full_disabled_without_allow_z():
    w = _make_window(homed=True)
    w.allow_z_cb.setChecked(False)
    assert not w.home_full_btn.isEnabled(), "未勾『允许 Z』必须禁用一键寻零（内含 G28 Z）"
    assert "G28 Z" in w.home_full_btn.toolTip()

    # 点击兜底（快捷键/编程调用绕过禁用时同样拒绝）
    w._on_home_full_clicked()
    assert not w._worker.reqSendSequence.calls
    assert "G28 Z" in w.status_label.text()

    w.allow_z_cb.setChecked(True)
    assert w.home_full_btn.isEnabled()
    w.close()


def test_send_preflight_uses_ui_allow_z_value():
    """_send 的 sequence_ok 预检 allow_z 传 UI 真值（修 main_window 曾硬编码 True）。"""
    w = _make_window(homed=True)
    w.allow_z_cb.setChecked(False)
    assert not w._send(["G0 Z-1 F300"]), "allow_z=False 时负 Z 应被预检拦下"
    assert not w._worker.reqSendSequence.calls
    assert "已拦截（安全门）" in w.console.toPlainText()

    w.allow_z_cb.setChecked(True)
    assert w._send(["G0 Z-1 F300"])  # allow_z + lift_configured → 放行
    assert w._worker.reqSendSequence.calls
    w.close()


# --- 空跑校验（compile_job(dry_run=True)） -----------------------------------


def test_dry_run_lines_never_pen_down(tmp_path):
    """空跑 lines 无任何 G1 Z<safe_z、无落笔/下压；段无 DRAW/PLUNGE（全 TRAVEL/SETUP）。"""
    w = _make_window(homed=True)
    assert w._load_svg_path(_write_svg(tmp_path))
    # 对照：真编译含落笔行（G1 Z17）
    assert any(l.startswith("G1 Z17") for l in w._job_lines)

    w._on_dry_run()
    dry = w._dry_run_compiled
    assert dry is not None and dry.lines
    safe_z = w._safe_z
    z_vals = [w._z_value(l) for l in dry.lines]
    assert all(z is None or z >= safe_z - 1e-6 for z in z_vals), \
        f"空跑出现低于安全 Z 的 Z：{dry.lines}"
    assert not any(l.split()[0] == "G1" and w._z_value(l) is not None
                   and w._z_value(l) < safe_z - 1e-6 for l in dry.lines), \
        "空跑不得有 G1 Z<safe_z（落笔/下压行）"
    assert all(abs(z - safe_z) < 1e-6 for z in z_vals if z is not None), \
        "空跑 Z 恒 = safe_z"
    kinds = {s.kind for s in dry.segments}
    assert not (kinds & {"DRAW", "PLUNGE"}), f"空跑段不得有落笔/下压：{kinds}"
    assert kinds <= {"SETUP", "TRAVEL", "SYNC", "UNKNOWN"}
    assert "空跑" in w.console.toPlainText()
    # 真作业的 lines 不被空跑覆盖
    assert any(l.startswith("G1 Z17") for l in w._job_lines)

    # 可选发送：勾选后把空跑 lines 发给机器（guard 逐行照查，经 _send）
    n_before = len(w._worker.reqSendSequence.calls)
    w.dry_run_send_cb.setChecked(True)
    w._on_dry_run()
    assert len(w._worker.reqSendSequence.calls) == n_before + 1
    assert w._worker.reqSendSequence.calls[-1][0] == list(dry.lines)
    w.close()


# --- 运行中 LiveMarker 诚实化（阶段 4-③） ------------------------------------


def test_livemarker_honesty_annotation_during_run(tmp_path):
    """执行期间置灰冻结 + 标注『执行中不轮询位置』；tooltip 注明 ok≠已移动、
    Count 不可信（§6 反馈可信度）。"""
    w = _make_window(homed=True)
    assert w._load_svg_path(_write_svg(tmp_path))
    w._on_run_job()
    assert w._job_running
    assert w._live_marker._stale is True
    assert "执行中不轮询位置" in w.live_hint.text()
    assert "ok≠已移动" in w.live_hint.toolTip()
    assert "Count 不可信" in w.live_hint.toolTip()
    w._on_job_done(True)
    assert "执行中不轮询位置" not in w.live_hint.text()
    assert "ok≠已移动" in w.live_hint.toolTip()  # 诚实声明常驻
    w.close()
