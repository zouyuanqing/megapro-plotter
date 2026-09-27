"""SerialWorker —— 常驻串口消费者（QObject，moveToThread 到工作线程）。

唯一直接使用 pyserial/transport 的 Qt 对象。GUI 线程绝不调用本类方法；
一律经 signal/slot 投递。阻塞读（send_and_wait_ok）全部发生在工作线程。

transport 依赖可注入（默认取 megapro.transport.marlin_serial 的 open_session），
便于用 tests/fake_serial 或 MarlinSim 在无真机/无显示环境下测试。

信号语义：
- stateChanged: DISCONNECTED/CONNECTING/READY/FAULT/ESTOP
- echo:         原始回显行（echo:/温度/坐标行等，不含 ok）
- status:       单行状态（ok / 错误 / 链路丢失…）
- position:     (x, y, z) 或含 None —— 由 M114 逻辑坐标解析而来
- log:          时间戳 TX/RX 日志行（写 logs/）
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal, Slot

from megapro.safety.guard import GuardReject, check
from megapro.gui.controller import parse_position

# 默认 transport（可注入覆盖）
import megapro.transport.marlin_serial as _ts


class _Capture:
    """收集一次 send_and_wait_ok 期间的 RX 行（transport log= 接口）。"""

    def __init__(self):
        self.rx: list[str] = []

    def write_tx(self, line):  # noqa: D401 - transport 接口
        pass

    def write_rx(self, line):
        self.rx.append(line)


class SerialWorker(QObject):
    # -- 输出信号（worker → GUI） --
    stateChanged = Signal(str)  # MachineState.name
    echo = Signal(str)
    status = Signal(str)
    position = Signal(object, object, object)
    log = Signal(str)
    # send_sequence 结束（含被 guard 拦停）：True=序列每行 send_line 都成功；
    # False=中途被 guard 拦/超时/链路异常/急停而中断（GUI 的 homed 门禁只认 ok=True）
    sequenceDone = Signal(bool)
    progress = Signal(int, int)  # job 进度 (i, total)
    jobDone = Signal(bool)  # job 结束：True=完成 / False=中止或出错

    # -- 请求信号（GUI → worker，跨线程 queued）--
    # worker 的槽在 worker 线程跑；调用方只 emit 这些信号，绝不直接调槽。
    reqOpen = Signal()
    reqClose = Signal()
    reqSendLine = Signal(str, object)  # (line, timeout|None)
    reqSendSequence = Signal(list, bool)
    reqRunJob = Signal(list)
    reqPoll = Signal()
    reqPause = Signal()
    reqResume = Signal()
    reqAbort = Signal()
    reqEstop = Signal()

    def __init__(
        self,
        port: str,
        baud: int | None,
        *,
        allow_z: bool = False,
        lift_configured: bool = False,
        open_session=None,
        send_and_wait_ok=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self.allow_z = allow_z
        self.lift_configured = lift_configured
        # 必须用 open_session（返回 (ser, baud_used) 元组并处理波特率扫描）；
        # 不能用 open_link（只返回 ser，解包会因 pyserial 可迭代而报错）。
        self._open_session = open_session or _ts.open_session
        self._send_and_wait_ok = send_and_wait_ok or _ts.send_and_wait_ok
        self._session = None
        self._baud_used = None
        self._estop_sent = False
        self._state = "DISCONNECTED"
        # job 运行状态（行间生效的控制 flag）
        self._job_pause = False
        self._job_abort = False
        # 请求信号 → 本对象槽：调用方（GUI/对话框）emit 请求信号；
        # 因本对象 moveToThread 到工作线程，emit 会 queued 到工作线程执行。
        self.reqOpen.connect(self.open)
        self.reqClose.connect(self.close)
        self.reqSendLine.connect(self.send_line)
        self.reqSendSequence.connect(self.send_sequence)
        self.reqRunJob.connect(self.run_job)
        self.reqPoll.connect(self.poll_position)
        self.reqPause.connect(self.pause_job)
        self.reqResume.connect(self.resume_job)
        self.reqAbort.connect(self.abort_job)
        self.reqEstop.connect(self.estop)

    # -- 内部 --------------------------------------------------------------

    def _set_state(self, name: str) -> None:
        self._state = name
        self.stateChanged.emit(name)

    def _emit_log(self, tx: bool, line: str) -> None:
        self.log.emit(f"{time.monotonic():.3f} {'>' if tx else '<'} {line}")

    def _guard(self, line: str) -> None:
        """发送前逐行过 guard（与 CLI 一致）；E-stop 是唯一免检路径。"""
        check(line, allow_z=self.allow_z, lift_configured=self.lift_configured)

    # -- slots（经队列调用，运行在工作线程） ---------------------------------

    @Slot()
    def open(self) -> None:
        """打开串口并完成引导（open_session 内含 dtr/2.5s/settle/波特率扫描）。阻塞。"""
        if self._session is not None:
            return
        self._estop_sent = False
        self._set_state("CONNECTING")
        try:
            self._session, self._baud_used = self._open_session(self.port, self.baud)
        except SystemExit as exc:
            self.status.emit(f"连接失败：{exc}")
            self._set_state("FAULT")
            return
        except Exception as exc:  # noqa: BLE001 - 边界收敛到 FAULT
            self.status.emit(f"连接失败：{exc!r}")
            self._set_state("FAULT")
            return
        self.status.emit(f"已连接 {self.port} @ {self._baud_used}（主板已复位，需归位）")
        self._set_state("READY")

    @Slot()
    def close(self) -> None:
        """关闭串口会话。"""
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
            self._session = None
        self._set_state("DISCONNECTED")

    @Slot(str)
    def send_line(self, line: str, timeout: float | None = None) -> bool:
        """发一行（过 guard），等 ok。阻塞至完成或超时。

        默认超时 30s；G28 归位等长操作需显式传更长（如 70s），
        否则会误报 FAULT（CLI 的 HOME_TIMEOUT=60s）。

        返回 True = 该行已成功发出并得到 ok；False = 被 guard 拦 /
        超时 / 链路异常 / 急停 / 会话已关（调用方可据此中断序列）。
        """
        if self._session is None or self._estop_sent:
            return False
        text = line.strip()
        if not text or text.startswith(";"):
            return True  # 空/注释行视为已处理
        try:
            self._guard(text)
        except GuardReject as exc:
            self.status.emit(f"已拦下：{exc}")
            return False
        cap = _Capture()
        self._emit_log(True, text)
        try:
            result = self._send_and_wait_ok(
                self._session, text, timeout=timeout or 30.0, log=cap
            )
        except TimeoutError as exc:
            self._emit_log(False, f"<timeout> {exc}")
            self.status.emit(f"超时：{exc}")
            self._set_state("FAULT")
            return False
        except Exception as exc:  # noqa: BLE001 - MarlinError / link lost
            self._emit_log(False, f"<error> {exc}")
            self.status.emit(f"链路异常：{exc}")
            self._set_state("FAULT")
            return False
        for rx in cap.rx:
            rx = rx.strip()
            if not rx:
                continue
            self._emit_log(False, rx)
            if rx.lower().startswith("ok"):
                self.status.emit("ok")
                continue
            if rx.lower().startswith("error") or rx.startswith("!!"):
                self.status.emit(rx)
                continue
            self.echo.emit(rx)
            x, y, z = parse_position(rx)
            if x is not None or y is not None or z is not None:
                self.position.emit(x, y, z)
        if result == "resend":
            self.status.emit("resend（已由 transport 处理）")
        return True

    @Slot(list)
    def send_sequence(self, lines: list[str], home: bool = False) -> None:
        """顺序发送一组行；任一失败（guard 拦/超时/链路/急停）即中断。

        home=True 时按归位序列处理：G28 行给 70s 超时（否则 30s 会误报）。
        序列执行期间状态置 BUSY，结束后回 READY（若仍连接且未急停）；
        无论结果都发 sequenceDone(ok)（供 UI 恢复 + homed 门禁判据）：
        ok=True **仅当序列每行 send_line 都成功**（空/注释行视为已处理）；
        被 guard 拦一半的序列必为 ok=False，GUI 不得当成功（评审 break #6）。
        入口早退（会话已关/已急停）同样发 sequenceDone(False)：序列一行未成功，
        且让 GUI 的在途归位标记不必靠状态变化兜底清理（评审 low #3）。
        """
        if self._session is None or self._estop_sent:
            self.sequenceDone.emit(False)
            return
        self._set_state("BUSY")
        ok = True
        for line in lines:
            if self._session is None or self._estop_sent:
                ok = False
                break
            is_g28 = line.strip().upper().startswith("G28")
            timeout = 70.0 if (home or is_g28) else None
            if not self.send_line(line, timeout=timeout):
                ok = False
                break  # guard 拦 / 超时 / 链路 / 急停 → 中断剩余行
        # 结束后恢复：若仍连接且未被急停/故障锁死，回 READY
        if self._session is not None and not self._estop_sent \
                and self._state not in ("FAULT", "ESTOP"):
            self._set_state("READY")
        self.sequenceDone.emit(ok)

    @Slot()
    def poll_position(self) -> None:
        """发 M114 刷新坐标。"""
        self.send_line("M114")

    # -- job 运行（写字/裁纸，可暂停/中止） --------------------------------

    @Slot(list)
    def run_job(self, lines: list[str]) -> None:
        """逐行发送一个 job；支持 pause_job/resume_job/abort_job（行间生效）。

        - 每行 guard → ack；发 progress(i,total)。
        - pause：当前行完成后挂起，等 resume 或 abort。
        - abort：停止喂行；若笔/刀可能在纸面则调用方负责停靠序列。
        - 结束（完成/中止/出错）发 jobDone(ok: bool)，状态回 READY。
        """
        if self._session is None or self._estop_sent:
            return
        self._job_pause = False
        self._job_abort = False
        self._set_state("BUSY")
        total = len(lines)
        ok = True
        for i, line in enumerate(lines, 1):
            # 暂停挂起点
            while self._job_pause and not self._job_abort \
                    and self._session is not None and not self._estop_sent:
                self._busy_wait(0.05)
            if self._job_abort or self._session is None or self._estop_sent:
                ok = False
                break
            # abort 由 GUI 线程直写 flag（非 queued）；行间即检查。
            # 单行发送用 30s 超时（job 行通常 <1s ack；G0 长移动由固件异步）
            if not self.send_line(line, timeout=None):
                ok = False
                break
            self.progress.emit(i, total)
        self._job_pause = False
        self._job_abort = False
        if self._session is not None and not self._estop_sent \
                and self._state not in ("FAULT", "ESTOP"):
            self._set_state("READY")
        self.jobDone.emit(ok)

    @staticmethod
    def _busy_wait(seconds: float) -> None:
        from PySide6.QtCore import QThread

        QThread.msleep(int(seconds * 1000))

    @Slot()
    def pause_job(self) -> None:
        """请求暂停：当前行完成后不再继续。"""
        if self._state == "BUSY":
            self._job_pause = True

    @Slot()
    def resume_job(self) -> None:
        self._job_pause = False

    @Slot()
    def abort_job(self) -> None:
        """请求中止：停止喂行（调用方负责后续停靠/M400）。"""
        self._job_abort = True

    @Slot()
    def estop(self) -> None:
        """急停：裸发 M112、不等 ok（CLI estop 语义）。之后需断电重启。"""
        if self._session is None:
            return
        self._estop_sent = True
        raw = "M112"
        self._emit_log(True, raw)
        try:
            # 免检裸发：写原行，不等待回执
            self._session.write((raw + "\n").encode("ascii"))
        except Exception as exc:  # noqa: BLE001
            self.status.emit(f"急停发送失败：{exc}")
        self.status.emit("急停已发送（M112）；机器已 halt，需断电重启")
        self._set_state("ESTOP")
