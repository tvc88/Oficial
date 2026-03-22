"""Gerencia subprocessos do Streamlink."""

import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Callable

from utils import sanitize, convert_ts

EXEC_CONV = ProcessPoolExecutor(max_workers=4)
EXEC_STOP = ThreadPoolExecutor(max_workers=4)


class Recorder:
    """Gerencia processos de gravação com o Streamlink."""

    def __init__(self):
        """Inicializa contêineres de processos."""
        self.proc = {}
        self.start = {}
        self.ts = {}
        self.exit_info = {}
        self.stderr_tail = {}
        self.stderr_thread = {}
        self.aproc = {}
        self.astart = {}
        self.ats = {}
        self.aexit_info = {}
        self.astderr_tail = {}
        self.astderr_thread = {}

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen, tail: deque[str]) -> None:
        """Consome o stderr continuamente para evitar bloqueio do subprocesso."""
        if not proc.stderr:
            return
        try:
            for line in proc.stderr:
                text = line.strip()
                if text:
                    tail.append(text)
        except Exception as exc:  # pragma: no cover - proteção extra
            tail.append(f"[stderr reader error] {exc}")
        finally:
            try:
                proc.stderr.close()
            except Exception:  # pragma: no cover - apenas limpeza defensiva
                pass

    def _start_stderr_reader(
        self,
        key: int,
        proc: subprocess.Popen,
        tails: dict[int, deque[str]],
        threads: dict[int, threading.Thread],
    ) -> None:
        """Inicia thread daemon para drenar stderr do Streamlink."""
        tail: deque[str] = deque(maxlen=200)
        thread = threading.Thread(
            target=self._drain_stderr,
            args=(proc, tail),
            name=f"streamlink-stderr-{key}",
            daemon=True,
        )
        tails[key] = tail
        threads[key] = thread
        thread.start()

    @staticmethod
    def _spawn_streamlink(url: str, qual: str, ts: Path) -> subprocess.Popen:
        """Inicia o processo do Streamlink."""
        return subprocess.Popen(
            ["streamlink", "--retry-streams", "5", "-o", str(ts), url, qual],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    @staticmethod
    def _collect_exit_info(
        proc: subprocess.Popen,
        tail: deque[str] | None,
        thread: threading.Thread | None,
    ) -> tuple[int | None, str]:
        """Coleta código de saída e stderr de um processo já encerrado."""
        if proc.poll() is None:
            return None, ""
        if thread and thread.is_alive():
            thread.join(timeout=0.5)
        stderr = "\n".join(tail or ())
        return proc.returncode, stderr.strip()

    # Manual recording
    def start_manual(
        self, key: int, label: str, url: str, qual: str, output_dir: Path
    ) -> Path:
        """Inicia gravação manual e retorna caminho do arquivo TS."""
        subdir = output_dir / sanitize(label)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        p = self._spawn_streamlink(url, qual, ts)
        self.proc[key] = p
        self.start[key] = time.time()
        self.ts[key] = ts
        self.exit_info.pop(key, None)
        self._start_stderr_reader(key, p, self.stderr_tail, self.stderr_thread)
        return ts

    def stop_manual(self, key: int, callback: Callable):
        """Interrompe gravação manual e agenda conversão em thread."""
        proc = self.proc.get(key)
        ts_file = self.ts.get(key)
        if not proc or not ts_file:
            return

        def worker():
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            fut = EXEC_CONV.submit(convert_ts, ts_file)
            fut.add_done_callback(lambda f, k=key: callback(f, k))

        EXEC_STOP.submit(worker)

    def finish_manual(self, key: int):
        """Remove registros da gravação manual finalizada."""
        for d in (
            self.proc,
            self.start,
            self.ts,
            self.exit_info,
            self.stderr_tail,
            self.stderr_thread,
        ):
            d.pop(key, None)

    def get_manual_exit_info(self, key: int) -> tuple[int | None, str]:
        """Retorna detalhes do encerramento do processo manual."""
        proc = self.proc.get(key)
        if not proc:
            return self.exit_info.get(key, (None, ""))
        info = self._collect_exit_info(
            proc,
            self.stderr_tail.get(key),
            self.stderr_thread.get(key),
        )
        self.exit_info[key] = info
        return info

    # Automatic recording
    def start_auto(
        self, key: int, name: str, url: str, qual: str, output_dir: Path
    ) -> Path:
        """Inicia gravação automática e retorna caminho do arquivo TS."""
        subdir = output_dir / sanitize(name)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        p = self._spawn_streamlink(url, qual, ts)
        self.aproc[key] = p
        self.astart[key] = time.time()
        self.ats[key] = ts
        self.aexit_info.pop(key, None)
        self._start_stderr_reader(key, p, self.astderr_tail, self.astderr_thread)
        return ts

    def stop_auto(self, key: int, callback: Callable):
        """Interrompe gravação automática e agenda conversão em thread."""
        proc = self.aproc.get(key)
        ts_file = self.ats.get(key)
        if not proc or not ts_file:
            return

        def worker():
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            fut = EXEC_CONV.submit(convert_ts, ts_file)
            fut.add_done_callback(lambda f, k=key: callback(f, k))

        EXEC_STOP.submit(worker)

    def finish_auto(self, key: int):
        """Remove registros da gravação automática finalizada."""
        for d in (
            self.aproc,
            self.astart,
            self.ats,
            self.aexit_info,
            self.astderr_tail,
            self.astderr_thread,
        ):
            d.pop(key, None)

    def get_auto_exit_info(self, key: int) -> tuple[int | None, str]:
        """Retorna detalhes do encerramento do processo automático."""
        proc = self.aproc.get(key)
        if not proc:
            return self.aexit_info.get(key, (None, ""))
        info = self._collect_exit_info(
            proc,
            self.astderr_tail.get(key),
            self.astderr_thread.get(key),
        )
        self.aexit_info[key] = info
        return info
