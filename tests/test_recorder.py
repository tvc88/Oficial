from collections import deque
from pathlib import Path
import io

import recorder
from recorder import Recorder


class FakeProc:
    def __init__(self, returncode=None, stderr_text=""):
        self._returncode = returncode
        self.returncode = returncode
        self.stderr_text = stderr_text
        self.stderr = io.StringIO(stderr_text)
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.returncode = 0
        self._returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self._returncode = 0
        self.returncode = 0

    def kill(self):
        self.killed = True
        self._returncode = -9
        self.returncode = -9


class ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def add_done_callback(self, callback):
        callback(self)

    def result(self):
        return self._value


class ImmediateExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return ImmediateFuture(fn(*args, **kwargs))


class DummyThread:
    def __init__(self, alive=False):
        self.alive = alive
        self.join_calls = 0

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.join_calls += 1
        self.alive = False


def test_start_manual_does_not_precreate_ts(monkeypatch, tmp_path):
    seen = {}
    fake_proc = FakeProc()

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return fake_proc

    started = {}

    def fake_start_stderr_reader(self, key, proc, tails, threads):
        started["key"] = key
        started["proc"] = proc
        tails[key] = deque(maxlen=200)
        threads[key] = DummyThread()

    monkeypatch.setattr(recorder.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(Recorder, "_start_stderr_reader", fake_start_stderr_reader)

    rec = Recorder()
    ts_path = rec.start_manual(1, "Canal Teste", "https://example.com/live", "best", tmp_path)

    assert ts_path.parent == tmp_path / "Canal_Teste"
    assert ts_path.suffix == ".ts"
    assert not ts_path.exists()
    assert seen["cmd"] == [
        "streamlink",
        "--retry-streams",
        "5",
        "-o",
        str(ts_path),
        "https://example.com/live",
        "best",
    ]
    assert seen["kwargs"]["stdout"] is recorder.subprocess.DEVNULL
    assert seen["kwargs"]["stderr"] is recorder.subprocess.PIPE
    assert seen["kwargs"]["text"] is True
    assert seen["kwargs"]["bufsize"] == 1
    assert started["key"] == 1
    assert started["proc"] is fake_proc


def test_start_auto_does_not_precreate_ts(monkeypatch, tmp_path):
    fake_proc = FakeProc()
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(
        Recorder,
        "_start_stderr_reader",
        lambda self, key, proc, tails, threads: (
            tails.setdefault(key, deque(maxlen=200)),
            threads.setdefault(key, DummyThread()),
        ),
    )

    rec = Recorder()
    ts_path = rec.start_auto(2, "Auto Teste", "https://example.com/auto", "720p", tmp_path)

    assert ts_path.parent == tmp_path / "Auto_Teste"
    assert ts_path.suffix == ".ts"
    assert not ts_path.exists()


def test_get_manual_exit_info_reads_drained_stderr():
    rec = Recorder()
    rec.proc[1] = FakeProc(returncode=1)
    rec.stderr_tail[1] = deque(["linha 1", "erro detalhado"], maxlen=200)
    rec.stderr_thread[1] = DummyThread(alive=True)

    info = rec.get_manual_exit_info(1)

    assert info == (1, "linha 1\nerro detalhado")
    assert rec.exit_info[1] == info
    assert rec.stderr_thread[1].join_calls == 1


def test_get_auto_exit_info_reads_drained_stderr():
    rec = Recorder()
    rec.aproc[2] = FakeProc(returncode=2)
    rec.astderr_tail[2] = deque(["auto 1", "auto erro"], maxlen=200)
    rec.astderr_thread[2] = DummyThread(alive=True)

    info = rec.get_auto_exit_info(2)

    assert info == (2, "auto 1\nauto erro")
    assert rec.aexit_info[2] == info
    assert rec.astderr_thread[2].join_calls == 1


def test_finish_manual_cleans_stderr_state():
    rec = Recorder()
    rec.proc[1] = FakeProc(returncode=1)
    rec.start[1] = 123.0
    rec.ts[1] = Path("video.ts")
    rec.exit_info[1] = (1, "erro")
    rec.stderr_tail[1] = deque(["erro"], maxlen=200)
    rec.stderr_thread[1] = DummyThread()

    rec.finish_manual(1)

    assert 1 not in rec.proc
    assert 1 not in rec.start
    assert 1 not in rec.ts
    assert 1 not in rec.exit_info
    assert 1 not in rec.stderr_tail
    assert 1 not in rec.stderr_thread


def test_stop_manual_converts_finished_process(monkeypatch, tmp_path):
    rec = Recorder()
    proc = FakeProc(returncode=1, stderr_text="falhou")
    ts_path = tmp_path / "video.ts"
    ts_path.write_bytes(b"dados")
    rec.proc[1] = proc
    rec.ts[1] = ts_path

    immediate_conv = ImmediateExecutor()
    immediate_stop = ImmediateExecutor()
    monkeypatch.setattr(recorder, "EXEC_CONV", immediate_conv)
    monkeypatch.setattr(recorder, "EXEC_STOP", immediate_stop)
    monkeypatch.setattr(
        recorder,
        "convert_ts",
        lambda path: (True, Path(str(path).replace('.ts', '.mp4'))),
    )

    received = {}

    def callback(fut, key):
        received["result"] = fut.result()
        received["key"] = key

    rec.stop_manual(1, callback)

    assert received == {"result": (True, tmp_path / "video.mp4"), "key": 1}
    assert len(immediate_stop.calls) == 1
    assert len(immediate_conv.calls) == 1
