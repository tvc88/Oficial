from pathlib import Path

import recorder
from recorder import Recorder


class FakeProc:
    def __init__(self, returncode=None, stderr_text=""):
        self._returncode = returncode
        self.returncode = returncode
        self.stderr_text = stderr_text
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return self._returncode

    def communicate(self, timeout=None):
        self.returncode = self._returncode
        return "", self.stderr_text

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


def test_start_manual_does_not_precreate_ts(monkeypatch, tmp_path):
    seen = {}
    fake_proc = FakeProc()

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return fake_proc

    monkeypatch.setattr(recorder.subprocess, "Popen", fake_popen)

    rec = Recorder()
    ts_path = rec.start_manual(1, "Canal Teste", "https://example.com/live", "best", tmp_path)

    assert ts_path.parent == tmp_path / "Canal_Teste"
    assert ts_path.suffix == ".ts"
    assert not ts_path.exists()
    assert seen["cmd"] == [
        "streamlink",
        "https://example.com/live",
        "best",
        "-o",
        str(ts_path),
        "--retry-streams",
        "5",
    ]
    assert seen["kwargs"]["stdout"] is recorder.subprocess.DEVNULL
    assert seen["kwargs"]["stderr"] is recorder.subprocess.PIPE
    assert seen["kwargs"]["text"] is True


def test_start_auto_does_not_precreate_ts(monkeypatch, tmp_path):
    fake_proc = FakeProc()
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *args, **kwargs: fake_proc)

    rec = Recorder()
    ts_path = rec.start_auto(2, "Auto Teste", "https://example.com/auto", "720p", tmp_path)

    assert ts_path.parent == tmp_path / "Auto_Teste"
    assert ts_path.suffix == ".ts"
    assert not ts_path.exists()


def test_get_manual_exit_info_reads_stderr_once():
    rec = Recorder()
    rec.proc[1] = FakeProc(returncode=1, stderr_text="erro detalhado")

    info = rec.get_manual_exit_info(1)

    assert info == (1, "erro detalhado")
    assert rec.exit_info[1] == info


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
    monkeypatch.setattr(recorder, "convert_ts", lambda path: (True, Path(str(path).replace('.ts', '.mp4'))))

    received = {}

    def callback(fut, key):
        received["result"] = fut.result()
        received["key"] = key

    rec.stop_manual(1, callback)

    assert received == {"result": (True, tmp_path / "video.mp4"), "key": 1}
    assert len(immediate_stop.calls) == 1
    assert len(immediate_conv.calls) == 1
