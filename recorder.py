"""Gerencia subprocessos do Streamlink."""

import subprocess
import time
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
        self.aproc = {}
        self.astart = {}
        self.ats = {}
        self.aexit_info = {}

    @staticmethod
    def _spawn_streamlink(url: str, qual: str, ts: Path) -> subprocess.Popen:
        """Inicia o processo do Streamlink."""
        return subprocess.Popen(
            ["streamlink", url, qual, "-o", str(ts), "--retry-streams", "5"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    @staticmethod
    def _collect_exit_info(proc: subprocess.Popen) -> tuple[int | None, str]:
        """Coleta código de saída e stderr de um processo já encerrado."""
        if proc.poll() is None:
            return None, ""
        try:
            _, stderr = proc.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            stderr = proc.stderr.read() if proc.stderr else ""
        except ValueError:
            stderr = ""
        return proc.returncode, (stderr or "").strip()

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
        for d in (self.proc, self.start, self.ts, self.exit_info):
            d.pop(key, None)

    def get_manual_exit_info(self, key: int) -> tuple[int | None, str]:
        """Retorna detalhes do encerramento do processo manual."""
        proc = self.proc.get(key)
        if not proc:
            return self.exit_info.get(key, (None, ""))
        info = self._collect_exit_info(proc)
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
        for d in (self.aproc, self.astart, self.ats, self.aexit_info):
            d.pop(key, None)

    def get_auto_exit_info(self, key: int) -> tuple[int | None, str]:
        """Retorna detalhes do encerramento do processo automático."""
        proc = self.aproc.get(key)
        if not proc:
            return self.aexit_info.get(key, (None, ""))
        info = self._collect_exit_info(proc)
        self.aexit_info[key] = info
        return info
