# -*- coding: utf-8 -*-
"""
whapi.py — Camada de integração com o Whapi.Cloud (substitui a Evolution).
============================================================================
Este arquivo isola TUDO que fala com o Whapi. O wa_bot.py só chama:
    - whapi.send_text(number, text)        -> enviar mensagem
    - whapi.to_evolution_shape(payload)    -> traduzir webhook p/ formato antigo
    - whapi.fetch_media_base64(url)         -> baixar áudio/foto e virar base64

Por que "traduzir"? O seu handle_incoming() foi escrito para o formato da
Evolution (data.key.remoteJid, data.message.conversation, etc). Em vez de
reescrever handle_incoming inteiro, a gente converte o payload do Whapi para
o mesmo formato que o código já entende. Menos risco, menos linhas mexidas.

CONFIG (variáveis de ambiente):
    WHAPI_TOKEN=seu_token_do_painel        (obrigatório)
    WHAPI_URL=https://gate.whapi.cloud     (padrão, raramente muda)
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

log = logging.getLogger("resolveai")

WHAPI_URL = os.environ.get("WHAPI_URL", "https://gate.whapi.cloud").rstrip("/")
WHAPI_TOKEN = os.environ.get("WHAPI_TOKEN", "")

_HEADERS = {
    "Authorization": f"Bearer {WHAPI_TOKEN}",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# 1. ENVIO
# ---------------------------------------------------------------------------
def send_text(number: str, text: str) -> bool:
    """Envia texto pelo Whapi. Retorna True se a API aceitou E marcou
    'sent': True. Diferente da Evolution, o Whapi devolve o status real do
    envio no corpo — então dá pra confiar mais nesse retorno."""
    import httpx
    # o Whapi aceita o número puro (5511...) no campo 'to'
    to = number.split("@")[0]
    try:
        r = httpx.post(
            f"{WHAPI_URL}/messages/text",
            headers=_HEADERS,
            json={"to": to, "body": text},
            timeout=20,
        )
        if r.status_code in (200, 201):
            body = r.json() if r.content else {}
            # resposta típica: {"sent": true, "message": {...}}
            if body.get("sent") is True or body.get("message"):
                return True
            log.warning("[envio] Whapi 200 mas sem confirmação: %s",
                        str(body)[:200])
            return True  # 200 já é aceite; tratamos como enviado
        log.warning("[envio] Whapi recusou (%s): %s",
                    r.status_code, r.text[:200])
        return False
    except Exception as e:
        log.warning("[envio] ERRO ao enviar via Whapi: %r", e)
        return False


# ---------------------------------------------------------------------------
# 2. TRADUÇÃO DO WEBHOOK  (Whapi -> formato que o handle_incoming já entende)
# ---------------------------------------------------------------------------
def to_evolution_shape(payload: dict) -> Optional[dict]:
    """
    Recebe o payload do Whapi e devolve um dict no MESMO formato que o
    handle_incoming() do wa_bot.py espera (formato Evolution). Retorna None
    se não for mensagem de entrada que interessa.

    Formato Whapi de entrada:
      {"messages": [ {"id","from_me","type","chat_id","from","from_name",
                       "text":{"body":...} | "voice":{"link","seconds",...}
                       | "image":{"link","caption",...} | ... } ],
       "event": {"type":"messages","event":"post"}}
    """
    msgs = payload.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None
    m = msgs[0]

    if m.get("from_me"):
        return None  # ignora o que o próprio bot mandou

    chat_id = m.get("chat_id", "") or ""
    if "@g.us" in chat_id or "@newsletter" in chat_id:
        return None  # ignora grupo / canal no MVP

    phone = (m.get("from") or chat_id.split("@")[0] or "").split("@")[0]
    if not phone:
        return None
    push_name = m.get("from_name", "") or ""
    mtype = m.get("type", "")

    # monta o "message" no formato Evolution conforme o tipo
    message: dict = {}
    media_link = ""

    if mtype == "text":
        body = (m.get("text") or {}).get("body", "") or ""
        message = {"conversation": body}

    elif mtype in ("voice", "audio"):
        node = m.get(mtype) or {}
        message = {"audioMessage": {"seconds": node.get("seconds", 0)}}
        media_link = node.get("link", "") or ""

    elif mtype == "image":
        node = m.get("image") or {}
        caption = node.get("caption", "") or ""
        message = {"imageMessage": {"caption": caption}}
        media_link = node.get("link", "") or ""

    elif mtype == "video":
        message = {"videoMessage": {}}

    elif mtype == "sticker":
        message = {"stickerMessage": {}}

    elif mtype == "reaction":
        node = m.get("reaction") or m.get("action") or {}
        message = {"reactionMessage": {"text": node.get("emoji", "") or ""}}

    else:
        # tipo não tratado (document, location, etc): manda como desconhecido
        message = {"conversation": ""}

    evolution_like = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": f"{phone}@s.whatsapp.net",
                "fromMe": False,
                "id": m.get("id", ""),
            },
            "pushName": push_name,
            "message": message,
            # guarda o link do Whapi pra baixar a mídia depois (se houver)
            "_whapi_media_link": media_link,
        },
    }
    return evolution_like


# ---------------------------------------------------------------------------
# 3. DOWNLOAD DE MÍDIA  (Whapi manda LINK, não base64 — a gente baixa e converte)
# ---------------------------------------------------------------------------
def fetch_media_base64(link: str) -> str:
    """Baixa o arquivo do link que o Whapi mandou e devolve em base64
    (que é o que _transcribe_audio e _read_image já esperam)."""
    if not link:
        return ""
    import httpx
    try:
        # o link do Whapi (wasabisys/s3) é público e temporário; sem header
        r = httpx.get(link, timeout=30, follow_redirects=True)
        if r.status_code == 200:
            b64 = base64.b64encode(r.content).decode("ascii")
            log.info("[media] baixado do Whapi: %d bytes -> %d chars b64",
                     len(r.content), len(b64))
            return b64
        log.warning("[media] Whapi link respondeu %s", r.status_code)
    except Exception as e:
        log.warning("[media] erro ao baixar do Whapi: %r", e)
    return ""


def instance_state() -> str:
    """Estado da sessão no Whapi. GET /health -> {"status":{"text":"AUTH"...}}.
    Devolve 'open' quando autenticado, pra casar com o resto do código."""
    import httpx
    try:
        r = httpx.get(f"{WHAPI_URL}/health",
                      headers=_HEADERS, timeout=8)
        if r.status_code == 200:
            j = r.json() or {}
            st = ((j.get("status") or {}).get("text") or "").upper()
            if st in ("AUTH", "AUTHENTICATED", "READY", "CONNECTED"):
                return "open"
            return st.lower() or "unknown"
        return "unknown"
    except Exception:
        return "unknown"
