import json
import logging
from pathlib import Path
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


def load_config(file_path: Path) -> Tuple[Path, Path, List[dict], Optional[str], Optional[str]]:
    out_manual = Path.home() / "GRAVACOES MANUAIS"
    out_monitor = Path.home() / "MONITORAMENTO"
    monitored = []
    telegram_token = None
    telegram_chat_id = None
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text())
            out_manual = Path(data.get("output_dir_manual", str(out_manual)))
            out_monitor = Path(data.get("output_dir_monitor", str(out_monitor)))
            monitored = data.get("monitored", [])
            telegram_token = data.get("telegram_token")
            telegram_chat_id = data.get("telegram_chat_id")
        except Exception as e:
            logger.error("Erro ao carregar configuração: %s", e)

    out_manual.mkdir(parents=True, exist_ok=True)
    out_monitor.mkdir(parents=True, exist_ok=True)
    return out_manual, out_monitor, monitored, telegram_token, telegram_chat_id


def save_config(
    file_path: Path,
    output_dir_manual: Path,
    output_dir_monitor: Path,
    monitored: List[dict],
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
):
    data = {
        "output_dir_manual": str(output_dir_manual),
        "output_dir_monitor": str(output_dir_monitor),
        "monitored": monitored,
    }
    prev = {}
    if file_path.exists():
        try:
            prev = json.loads(file_path.read_text())
        except Exception:
            prev = {}
        if telegram_token is None:
            telegram_token = prev.get("telegram_token")
        if telegram_chat_id is None:
            telegram_chat_id = prev.get("telegram_chat_id")
    data["telegram_token"] = telegram_token
    data["telegram_chat_id"] = telegram_chat_id
    try:
        if file_path.exists():
            backup = file_path.with_suffix('.json.bak')
            if backup.exists():
                backup.unlink()
            file_path.replace(backup)
        file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error("Falha ao salvar configuração: %s", e)
        backup = file_path.with_suffix('.json.bak')
        if file_path.exists():
            file_path.unlink()
        if backup.exists():
            backup.replace(file_path)
