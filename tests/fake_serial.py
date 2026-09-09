"""In-memory fake of pyserial's minimal surface for transport tests.

No hardware, no threads: replies are scripted per write. Each ``write``
consumes the next entry of ``script`` (a list of reply-line lists) and queues
those lines for ``readline``.
"""


class FakeSerial:
    """Test double exposing the pyserial bits ``send_and_wait_ok`` uses.

    Args:
        script: list of reply-line-lists, one entry per ``write``. Each entry
            is a list of ``str``/``bytes`` lines queued as the reply to the
            n-th write. Writes past the end of the script get no reply.
        mode: ``"normal"`` (default) or ``"silent"``. Silent mode queues no
            replies, so ``readline`` always returns ``b""`` -- simulating a
            dead link / timeout.

    Attributes:
        written: list of every ``bytes`` object passed to ``write``.
        is_open: mirrors ``serial.Serial.is_open``; cleared by ``close()``.
    """

    def __init__(self, script=None, mode="normal"):
        self._script = [list(lines) for lines in (script or [])]
        self.mode = mode
        self.written = []
        self.is_open = True
        self._pending = []

    @staticmethod
    def _as_bytes(line):
        if isinstance(line, bytes):
            data = line
        else:
            data = str(line).encode("ascii", errors="replace")
        return data if data.endswith(b"\n") else data + b"\n"

    def write(self, data):
        data = bytes(data)
        self.written.append(data)
        if self.mode != "silent" and self._script:
            self._pending.extend(self._as_bytes(line) for line in self._script.pop(0))
        return len(data)

    def readline(self):
        if self._pending:
            return self._pending.pop(0)
        return b""

    def reset_input_buffer(self):
        del self._pending[:]

    def close(self):
        self.is_open = False
