"""Funções auxiliares para interação com Telegram."""

import os
import json
import logging
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).with_name("config.json")
TOKEN_ENV = "TELEGRAM_TOKEN"
CHAT_ENV = "TELEGRAM_CHAT_ID"


def _load_creds() -> tuple[Optional[str], Optional[str]]:
    """Carrega credenciais a partir de variáveis de ambiente ou arquivo."""

    token = os.getenv(TOKEN_ENV)
    chat_id = os.getenv(CHAT_ENV)
    if token and chat_id:
        return token, chat_id
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            token = token or data.get("telegram_token")
            chat_id = chat_id or data.get("telegram_chat_id")
        except Exception as exc:
            logger.error("Erro ao ler credenciais do Telegram: %s", exc)
    return token, chat_id


TOKEN, CHAT_ID = _load_creds()


def update_creds(token: Optional[str], chat_id: Optional[str]) -> None:
    """Atualiza as credenciais em memória."""
    global TOKEN, CHAT_ID
    TOKEN = token
    CHAT_ID = chat_id


def enviar_notificacao_telegram(mensagem: str) -> None:
    """Envia mensagem ao chat configurado, se possível."""
    if not TOKEN or not CHAT_ID:
        logger.warning("Credenciais do Telegram ausentes - notificacao ignorada")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": mensagem}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as exc:
        logger.error("Falha ao enviar mensagem Telegram: %s", exc)
