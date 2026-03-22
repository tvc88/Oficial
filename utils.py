"""Funções utilitárias para manipulação de strings e execução de comandos."""

import re
import json
import subprocess
import logging
from pathlib import Path

INVALID_FS_CHARS = r'[<>:"/\\|?*\x00-\x1F]'

logger = logging.getLogger(__name__)


def sanitize(text: str) -> str:
    """Remove caracteres inválidos para sistemas de arquivos."""

    text = re.sub(INVALID_FS_CHARS, "_", text)
    return re.sub(r"\s+", "_", text).strip("_")


def human_size(num_bytes: int) -> str:
    """Converte tamanho em bytes para formato legível."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1_048_576:
        return f"{num_bytes/1024:.1f} KB"
    if num_bytes < 1_073_741_824:
        return f"{num_bytes/1_048_576:.1f} MB"
    return f"{num_bytes/1_073_741_824:.2f} GB"


def human_time(sec: int) -> str:
    """Formata segundos em representação amigável."""
    if sec < 60:
        return f"{sec}s"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    minutes %= 60
    return f"{hours} h {minutes} min"


def convert_ts(ts_path: Path):
    """Converte arquivo TS para MP4 usando ffmpeg."""
    mp4 = ts_path.with_suffix(".mp4")
    ok = False
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(ts_path), "-c", "copy", str(mp4)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3600,
            check=True,
        )
        ok = True
        logger.info("ffmpeg retornou %s para %s", r.returncode, ts_path)
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg falhou (%s) ao converter %s", e.returncode, ts_path)
    except Exception as e:
        mp4 = "ERRO"
        logger.error("Erro ao converter %s: %s", ts_path, e)
    if ok:
        ts_path.unlink(missing_ok=True)
    return ok, mp4


def streamlink_json(url: str):
    """Executa Streamlink com --json e retorna o resultado."""
    try:
        r = subprocess.run(
            ["streamlink", "--json", url],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        logger.error("Tempo excedido ao obter JSON do Streamlink: %s", url)
        return None
    except json.JSONDecodeError:
        logger.error("Resultado inválido do Streamlink para %s", url)
        return None


def is_live(url: str):
    """Verifica se a URL possui transmissões ativas."""
    data = streamlink_json(url)
    if not data:
        return False, None

    streams = data.get("streams", {})
    return bool(streams), data
