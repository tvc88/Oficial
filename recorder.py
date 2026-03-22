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
        self.aproc = {}
        self.astart = {}
        self.ats = {}

    # Manual recording
    def start_manual(
        self, key: int, label: str, url: str, qual: str, output_dir: Path
    ) -> Path:
        """Inicia gravação manual e retorna caminho do arquivo TS."""
        subdir = output_dir / sanitize(label)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        ts.touch()
        p = subprocess.Popen(
            ["streamlink", url, qual, "-o", str(ts), "--retry-streams", "5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.proc[key] = p
        self.start[key] = time.time()
        self.ts[key] = ts
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
        for d in (self.proc, self.start, self.ts):
            d.pop(key, None)

    # Automatic recording
    def start_auto(
        self, key: int, name: str, url: str, qual: str, output_dir: Path
    ) -> Path:
        """Inicia gravação automática e retorna caminho do arquivo TS."""
        subdir = output_dir / sanitize(name)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        ts.touch()
        p = subprocess.Popen(
            ["streamlink", url, qual, "-o", str(ts), "--retry-streams", "5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.aproc[key] = p
        self.astart[key] = time.time()
        self.ats[key] = ts
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
        for d in (self.aproc, self.astart, self.ats):
            d.pop(key, None)
