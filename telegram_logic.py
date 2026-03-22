"""Funções de lógica isoladas para uso com Telegram."""

from typing import Any, Optional, Tuple, List
import requests


def enviar_mensagem_teste(token: str, chat_id: str) -> str:
    """Envia uma mensagem de teste utilizando a API do Telegram.

    Parameters
    ----------
    token: str
        Token do bot do Telegram.
    chat_id: str
        Identificador do chat que receberá a mensagem de teste.

    Returns
    -------
    str
        ``"sucesso"`` em caso de envio bem sucedido ou uma descrição do erro
        ocorrido.
    """

    if not token or not chat_id:
        return "Credenciais ausentes"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": "Mensagem de teste"}
    try:
        response = requests.post(url, data=data, timeout=10)
    except Exception as exc:  # pragma: no cover - rede pode falhar em execucao real
        return str(exc)

    if response.ok:
        return "sucesso"

    # Tenta detalhar o erro retornado pela API
    detail: Any
    try:
        detail = response.json()
    except Exception:  # pragma: no cover - resposta pode nao ser JSON
        detail = response.text
    return f"Erro {response.status_code}: {detail}"


def obter_updates(token: str, offset: Optional[int] = None) -> Optional[List[dict]]:
    """Busca atualizac\u0327\u00f5es do bot via Telegram API."""
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=10)
    except Exception:  # pragma: no cover - rede pode falhar em execucao real
        return None
    if not response.ok:
        return None
    data = response.json()
    if not data.get("ok"):
        return None
    return data.get("result", [])


def verificar_status_command(
    token: str, chat_id: str, offset: Optional[int] = None
) -> Tuple[bool, Optional[int]]:
    """Verifica se houve comando /status enviado pelo chat."""
    updates = obter_updates(token, offset)
    if not updates:
        return False, offset

    found = False
    new_offset = offset
    for upd in updates:
        new_offset = upd.get("update_id", new_offset)
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue
        text = msg.get("text", "").lower()
        if text.startswith("/status"):
            found = True
        new_offset += 1
    return found, new_offset
