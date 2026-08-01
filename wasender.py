# -*- coding: utf-8 -*-
"""
wasender.py — Camada de integração com a WasenderAPI (substitui o Whapi).
==========================================================================
Isola TUDO que fala com a WasenderAPI. O wa_bot.py só chama:
    - wasender.send_text(number, text)      -> enviar mensagem
    - wasender.to_evolution_shape(payload)  -> traduzir webhook p/ formato interno
    - wasender.fetch_media_base64(url)       -> baixar áudio/foto e virar base64
    - wasender.instance_state()              -> 'open' se a sessão está conectada

Por que "traduzir"? O handle_incoming() do wa_bot foi escrito para o formato
Evolution (data.key.remoteJid, data.message.conversation). Convertemos o
payload da WasenderAPI para o mesmo formato — menos risco, menos linhas.

CONFIG (variáveis de ambiente):
    WASENDER_API_KEY=<api key da sessão, do dashboard>   (obrigatório)
    WASENDER_URL=https://www.wasenderapi.com             (padrão)
"""
from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional

log = logging.getLogger("resolveai")

WASENDER_URL = os.environ.get("WASENDER_URL", "https://www.wasenderapi.com").rstrip("/")
WASENDER_API_KEY = os.environ.get("WASENDER_API_KEY", "")

_HEADERS = {
    "Authorization": f"Bearer {WASENDER_API_KEY}",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# 1. ENVIO
# ---------------------------------------------------------------------------
def send_text(number: str, text: str, tentativas: int = 3) -> bool:
    """Envia texto pela WasenderAPI. Número em E.164 com '+' (ex.: +5511...).
    Retorna True se a API aceitou (200/201).

    Em 429 (rate limit) NÃO desiste: espera o `retry_after` que a própria API
    informa e tenta de novo. Isso mantém a "Account Protection" ligada (que
    protege o número contra ban) sem perder mensagem quando o bot precisa
    mandar duas seguidas — ex.: alarme do cron + resposta a uma mensagem.
    """
    import time
    import httpx
    to = number.split("@")[0]
    to = re.sub(r"[^\d]", "", to)          # só dígitos
    if not to.startswith("+"):
        to = "+" + to

    for tentativa in range(1, tentativas + 1):
        try:
            r = httpx.post(
                f"{WASENDER_URL}/api/send-message",
                headers=_HEADERS,
                json={"to": to, "text": text},
                timeout=20,
            )
            if r.status_code in (200, 201):
                return True

            if r.status_code == 429 and tentativa < tentativas:
                espera = 5
                try:
                    espera = int((r.json() or {}).get("retry_after") or 5)
                except Exception:
                    pass
                espera = max(1, min(espera, 65))
                log.info("[envio] rate limit (429); aguardando %ds "
                         "(tentativa %d/%d)", espera, tentativa, tentativas)
                time.sleep(espera)
                continue

            log.warning("[envio] Wasender recusou (%s): %s",
                        r.status_code, r.text[:200])
            return False
        except Exception as e:
            log.warning("[envio] ERRO ao enviar via Wasender: %r", e)
            return False

    log.warning("[envio] desistiu apos %d tentativas (rate limit)", tentativas)
    return False


# ---------------------------------------------------------------------------
# 2. TRADUÇÃO DO WEBHOOK  (Wasender -> formato que o handle_incoming entende)
# ---------------------------------------------------------------------------
def to_evolution_shape(payload: dict) -> Optional[dict]:
    """
    Recebe o payload da WasenderAPI e devolve um dict no formato Evolution.
    Retorna None se não for mensagem de entrada que interessa.

    Formato Wasender (messages.received / messages.upsert):
      {"event":"messages.received","data":{"messages":{
          "key":{"id","fromMe","remoteJid","cleanedSenderPn",...},
          "messageBody":"texto",
          "message":{"conversation":"..."} | {"imageMessage":{...}} | ...
      }}}
    """
    event = payload.get("event", "")
    # só tratamos entrada; ignora status de envio/sessão aqui
    if event not in ("messages.received", "messages.upsert"):
        return None

    data = payload.get("data") or {}
    m = data.get("messages")
    if not isinstance(m, dict):
        # em alguns eventos "messages" pode vir como lista
        if isinstance(m, list) and m:
            m = m[0]
        else:
            return None

    key = m.get("key") or {}
    if key.get("fromMe"):
        return None  # ignora o que o próprio bot mandou

    remote = key.get("remoteJid", "") or ""
    if "@g.us" in remote or "@newsletter" in remote or "@broadcast" in remote:
        return None  # ignora grupo/canal no MVP

    # telefone limpo: prefere cleanedSenderPn, senão extrai do remoteJid
    phone = (key.get("cleanedSenderPn")
             or re.sub(r"[^\d]", "", remote.split("@")[0]))
    if not phone:
        return None
    push_name = m.get("pushName") or data.get("pushName") or ""

    # conteúdo: Wasender já traz messageBody achatado; também traz message.*
    inner = m.get("message") or {}
    body = m.get("messageBody", "")

    message: dict = {}
    media_link = ""

    if "conversation" in inner or (body and not _has_media(inner)):
        message = {"conversation": inner.get("conversation", body) or body}
    elif "imageMessage" in inner:
        node = inner["imageMessage"] or {}
        message = {"imageMessage": {"caption": node.get("caption", "") or ""}}
        media_link = node.get("url") or node.get("link") or ""
    elif "audioMessage" in inner or "voiceMessage" in inner:
        node = inner.get("audioMessage") or inner.get("voiceMessage") or {}
        message = {"audioMessage": {"seconds": node.get("seconds", 0)}}
        media_link = node.get("url") or node.get("link") or ""
    elif "videoMessage" in inner:
        message = {"videoMessage": {}}
    elif "stickerMessage" in inner:
        message = {"stickerMessage": {}}
    elif "reactionMessage" in inner:
        node = inner["reactionMessage"] or {}
        message = {"reactionMessage": {"text": node.get("text", "") or ""}}
    else:
        message = {"conversation": body or ""}

    return {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": f"{phone}@s.whatsapp.net",
                "fromMe": False,
                "id": key.get("id", ""),
            },
            "pushName": push_name,
            "message": message,
            "_media_link": media_link,  # reusa o campo já lido pelo wa_bot
        },
    }


def _has_media(inner: dict) -> bool:
    return any(k in inner for k in (
        "imageMessage", "audioMessage", "voiceMessage",
        "videoMessage", "stickerMessage", "documentMessage"))


# ---------------------------------------------------------------------------
# 3. DOWNLOAD DE MÍDIA
# ---------------------------------------------------------------------------
def fetch_media_base64(link: str) -> str:
    """Baixa o arquivo do link que a Wasender mandou e devolve base64."""
    if not link:
        return ""
    import httpx
    try:
        r = httpx.get(link, timeout=30, follow_redirects=True)
        if r.status_code == 200:
            b64 = base64.b64encode(r.content).decode("ascii")
            log.info("[media] baixado da Wasender: %d bytes", len(r.content))
            return b64
        log.warning("[media] Wasender link respondeu %s", r.status_code)
    except Exception as e:
        log.warning("[media] erro ao baixar da Wasender: %r", e)
    return ""


# ---------------------------------------------------------------------------
# 4. ESTADO DA SESSÃO
# ---------------------------------------------------------------------------
def instance_state() -> str:
    """Consulta o status da sessão. 'open' = conectada.
    A Wasender expõe status via /api/status ou similar; tentamos ler e
    normalizamos para o vocabulário do resto do código."""
    import httpx
    try:
        r = httpx.get(f"{WASENDER_URL}/api/status",
                      headers=_HEADERS, timeout=8)
        if r.status_code == 200:
            j = r.json() or {}
            st = str(j.get("status") or (j.get("data") or {}).get("status") or "").lower()
            if st in ("connected", "open", "authenticated", "ready"):
                return "open"
            if st in ("need_scan", "disconnected"):
                return st
            return st or "unknown"
        return "unknown"
    except Exception:
        return "unknown"
