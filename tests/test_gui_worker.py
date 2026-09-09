"""P0 GUI worker 集成测：SerialWorker × MarlinSim(TCP)，无真机。

复用 tests/test_integration.py 的 MarlinSim 思路：本地 TCP 模拟 Marlin，
worker 经注入的 open_session 用 pyserial socket:// 连它，跑 open → send → poll
→ estop 全链，验证 signal 输出。

Qt 需要离屏平台：测试前设 QT_QPA_PLATFORM=offscreen；若 PySide6 不可用
则整模块 skip（controller 单测仍独立保证纯逻辑）。
"""

import os
import socket
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication

from megapro.gui.worker import SerialWorker

# MarlinSim：精简版（只回 M115/M114/ok + 记录）
FIRMWARE = "FIRMWARE_NAME:megapro-sim PROTOCOL_VERSION:1.0"


class MarlinSim(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.received: list[str] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(10)
        self.port = self._srv.getsockname()[1]

    def run(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            buf = b""
            try:
                while True:
                    chunk = conn.recv(256)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("ascii", errors="replace").strip()
                        if not line:
                            continue
                        self.received.append(line)
                        cmd = line.split()[0].upper()
                        if cmd == "M112":
                            conn.sendall(b"Error:Printer halted. kill() called!\n")
                            return
                        if cmd == "M115":
                            conn.sendall(f"{FIRMWARE}\nok\n".encode("ascii"))
                        elif cmd == "M114":
                            conn.sendall(
                                b"X:0.00 Y:50.00 Z:30.00 E:0.00 Count X: 0 Y:4000 Z:12000\nok\n"
                            )
                        else:
                            conn.sendall(b"ok\n")
            except OSError:
                return

    def stop(self):
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture()
def app():
    qapp = QCoreApplication.instance() or QCoreApplication([])
    yield qapp


@pytest.fixture()
def sim():
    s = MarlinSim()
    s.start()
    yield s
    s.stop()


def _open_link_to_sim(port, baud=None, log=None):
    from serial import serial_for_url

    # port 已是完整 socket://127.0.0.1:PORT URL（与 test_integration 同约定）
    ser = serial_for_url(port, baudrate=baud or 250000, timeout=2.0)
    return ser, baud or 250000


def _collect(worker):
    """返回 (states, statuses, echos, positions) 记录器，connect 后填充。"""
    box = {"states": [], "statuses": [], "echos": [], "positions": []}
    worker.stateChanged.connect(lambda s: box["states"].append(s))
    worker.status.connect(lambda s: box["statuses"].append(s))
    worker.echo.connect(lambda s: box["echos"].append(s))
    worker.position.connect(lambda *p: box["positions"].append(p))
    return box


def _flush(ms=20):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_worker_open_send_poll_estop(app, sim):
    w = SerialWorker(
        f"socket://127.0.0.1:{sim.port}",
        None,
        open_session=lambda port, baud, log=None: _open_link_to_sim(port, baud, log),
    )
    box = _collect(w)

    w.open()  # 直接同步调用（worker 方法内部即为工作线程路径）
    assert "READY" in box["states"], box["states"]

    w.poll_position()
    _flush()
    assert any(p == (0.0, 50.0, 30.0) for p in box["positions"]), box["positions"]
    assert "M114" in sim.received

    w.send_line("G0 X10 F600")
    _flush()
    assert "G0 X10 F600" in sim.received

    w.estop()
    assert "ESTOP" in box["states"]
    assert sim.received and sim.received[-1] == "M112"

    w.close()
    assert "DISCONNECTED" in box["states"]


def test_worker_guard_blocks_heater(app, sim):
    w = SerialWorker(
        f"socket://127.0.0.1:{sim.port}",
        None,
        open_session=lambda port, baud, log=None: _open_link_to_sim(port, baud, log),
    )
    box = _collect(w)
    w.open()
    assert "READY" in box["states"]
    w.send_line("M104 S200")  # guard 拦
    _flush()
    assert not any("M104" in l for l in sim.received)  # 从未发出
    assert any("已拦下" in s for s in box["statuses"])
    w.close()


def test_send_sequence_interrupts_on_guard_reject(app, sim):
    """回归：序列中 guard 拦一行后必须中断，不再发后续行，且发 BUSY→READY。"""
    w = SerialWorker(
        f"socket://127.0.0.1:{sim.port}",
        None,
        open_session=lambda port, baud, log=None: _open_link_to_sim(port, baud, log),
    )
    box = _collect(w)
    w.open()
    assert "READY" in box["states"]

    before = len(sim.received)
    # 序列：合法 → 被 guard 拦（M104）→ 若未中断会继续发的危险行 M3
    w.send_sequence(["G0 X5 F600", "M104 S200", "M3 S1"])
    _flush()
    got = sim.received[before:]
    assert "G0 X5 F600" in got
    # M104 被拦未发；M3 也不得发出（序列中断）
    assert not any("M104" in l for l in got)
    assert not any(l.split()[0] == "M3" for l in got)
    # BUSY → 回到 READY
    assert "BUSY" in box["states"], box["states"]
    assert box["states"][-1] == "READY"
    w.close()


def test_run_job_progress_and_done(app, sim):
    w = SerialWorker(
        f"socket://127.0.0.1:{sim.port}",
        None,
        open_session=lambda port, baud, log=None: _open_link_to_sim(port, baud, log),
    )
    box = {"progress": [], "done": [], "states": []}
    w.stateChanged.connect(lambda s: box["states"].append(s))
    w.progress.connect(lambda i, t: box["progress"].append((i, t)))
    w.jobDone.connect(lambda ok: box["done"].append(ok))
    w.open()
    lines = ["G0 X1 F600", "G0 X2 F600", "G0 X3 F600"]
    w.run_job(lines)
    _flush()
    assert box["progress"] == [(1, 3), (2, 3), (3, 3)], box["progress"]
    assert box["done"] == [True]
    assert box["states"][-1] == "READY"
    for l in lines:
        assert l in sim.received
    w.close()


def test_run_job_abort_stops_feeding(app, sim):
    w = SerialWorker(
        f"socket://127.0.0.1:{sim.port}",
        None,
        open_session=lambda port, baud, log=None: _open_link_to_sim(port, baud, log),
    )
    box = {"progress": [], "done": []}
    w.progress.connect(lambda i, t: box["progress"].append((i, t)))
    w.jobDone.connect(lambda ok: box["done"].append(ok))

    # 在 job 中途请求中止：用一个会"卡"的 send（注入慢版）模拟行间点 abort
    import time as _time

    def slow_send(ser, line, timeout=5.0, log=None):
        if "X5" in line:  # 走到某行时点 abort
            w._job_abort = True
        _time.sleep(0.01)
        return "ok"

    w.open()
    w._send_and_wait_ok = slow_send
    lines = [f"G0 X{n} F600" for n in range(1, 20)]
    w.run_job(lines)
    _flush()
    assert box["done"] == [False]
    sent = [l for l in sim.received if l.startswith("G0 X")]
    assert len(sent) < len(lines)  # 中止后未发完
    w.close()


def test_run_job_pause_resume(app, sim):
    """暂停：job 在第 1 行后置 pause → 停住不发后续；resume 后继续到完成。"""
    import threading

    w = SerialWorker(
        f"socket://127.0.0.1:{sim.port}",
        None,
        open_session=lambda port, baud, log=None: _open_link_to_sim(port, baud, log),
    )
    box = {"progress": [], "done": []}
    w.progress.connect(lambda i, t: box["progress"].append((i, t)))
    w.jobDone.connect(lambda ok: box["done"].append(ok))
    w.open()
    lines = [f"G0 X{n} F600" for n in range(1, 6)]

    # job 在后台线程跑（worker 槽本身是同步阻塞的）
    result = {}

    def run():
        w.run_job(lines)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    # 等第 1 行发出后暂停
    deadline = 0
    while len(sim.received) < 1 and deadline < 50:
        _flush(5)
        deadline += 1
    w.pause_job()
    _flush(60)  # 暂停应生效：不再发后续行
    n_after_pause = len(sim.received)
    w.resume_job()
    t.join(2)
    _flush(20)
    assert box["done"] == [True], box["done"]
    assert len(box["progress"]) == len(lines)
    assert len(sim.received) >= len(lines)  # resume 后发完
    w.close()
