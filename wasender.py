# -*- coding: utf-8 -*-
"""
wasender.py — Camada de integração com a WasenderAPI (substitui o Whapi).
==========================================================================
Isola TUDO que fala com a WasenderAPI. O wa_bot.py só chama:
    - wasender.send_text(number, text)      -> enviar mensagem
    - wasender.to_evolution_shape(payload)  -> traduzir webhook p/ formato interno
    - wasender.baixar_midia(...)             -> áudio/foto/doc em base64 (já limpo)
    - wasender.instance_state()              -> 'open' se a sessão está conectada

MÍDIA — por que não basta baixar a URL:
O WhatsApp entrega mídia CRIPTOGRAFADA. O webhook traz `url` (arquivo .enc) e
`mediaKey`. Baixar a url direto devolve bytes cifrados — foi o que acontecia
antes: o áudio chegava, era classificado certo, e o Whisper recebia lixo e
falhava calado. A Wasender expõe POST /api/decrypt-media, que devolve uma
`publicUrl` já descriptografada (válida por 1 hora). É o caminho usado aqui.

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

# Tipos de mídia que o WhatsApp manda, na ordem em que procuramos no payload.
TIPOS_MIDIA = ("imageMessage", "audioMessage", "voiceMessage", "pttMessage",
               "documentMessage", "videoMessage", "stickerMessage")


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
    """
    event = payload.get("event", "")
    if event not in ("messages.received", "messages.upsert"):
        return None

    data = payload.get("data") or {}
    m = data.get("messages")
    if not isinstance(m, dict):
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

    # O remoteJid pode ser um LID (@lid), não um telefone. A doc manda usar
    # os campos "cleaned" — senão o usuário vira um cadastro novo a cada vez.
    phone = (key.get("cleanedSenderPn")
             or key.get("cleanedParticipantPn")
             or re.sub(r"[^\d]", "", remote.split("@")[0]))
    phone = re.sub(r"[^\d]", "", str(phone or ""))
    if not phone:
        return None
    push_name = m.get("pushName") or data.get("pushName") or ""

    inner = m.get("message") or {}
    body = m.get("messageBody", "")   # texto unificado (inclui legenda)
    msg_id = key.get("id", "")

    # Qual mídia veio (se veio)?
    tipo_midia = ""
    node_midia: dict = {}
    for t in TIPOS_MIDIA:
        if isinstance(inner.get(t), dict):
            tipo_midia, node_midia = t, inner[t]
            break
    # documento com legenda vem aninhado
    if not tipo_midia and isinstance(inner.get("documentWithCaptionMessage"), dict):
        interno = (inner["documentWithCaptionMessage"].get("message") or {})
        if isinstance(interno.get("documentMessage"), dict):
            tipo_midia, node_midia = "documentMessage", interno["documentMessage"]

    message: dict = {}
    if not tipo_midia:
        message = {"conversation": inner.get("conversation", body) or body}
    elif tipo_midia == "imageMessage":
        message = {"imageMessage": {"caption": node_midia.get("caption", "") or ""}}
    elif tipo_midia in ("audioMessage", "voiceMessage", "pttMessage"):
        message = {"audioMessage": {"seconds": node_midia.get("seconds", 0)}}
    elif tipo_midia == "documentMessage":
        message = {"documentMessage": {
            "caption": node_midia.get("caption", "") or "",
            "fileName": node_midia.get("fileName", "") or "",
            "mimetype": node_midia.get("mimetype", "") or ""}}
    elif tipo_midia == "videoMessage":
        message = {"videoMessage": {}}
    elif tipo_midia == "stickerMessage":
        message = {"stickerMessage": {}}

    if "reactionMessage" in inner:
        node = inner["reactionMessage"] or {}
        message = {"reactionMessage": {"text": node.get("text", "") or ""}}

    return {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": f"{phone}@s.whatsapp.net",
                "fromMe": False,
                "id": msg_id,
            },
            "pushName": push_name,
            "message": message,
            # tudo que baixar_midia() precisa para descriptografar:
            "_msg_id": msg_id,
            "_media_tipo": tipo_midia,
            "_media_node": node_midia,
        },
    }


# ---------------------------------------------------------------------------
# 3. DOWNLOAD + DESCRIPTOGRAFIA DE MÍDIA
# ---------------------------------------------------------------------------
def baixar_midia(msg_id: str, tipo: str, node: dict) -> str:
    """Devolve o arquivo em base64, já descriptografado — ou "" se falhar.

    Fluxo: POST /api/decrypt-media  ->  publicUrl (1h)  ->  GET  ->  base64.
    """
    if not (tipo and isinstance(node, dict) and node.get("url")):
        log.warning("[media] payload sem url/mediaKey (tipo=%r)", tipo)
        return ""
    if not node.get("mediaKey"):
        log.warning("[media] payload sem mediaKey — nao da pra descriptografar")
        return ""

    import httpx

    # A API espera o nó como veio no webhook, no envelope original.
    corpo = {"data": {"messages": {
        "key": {"id": msg_id},
        "message": {tipo: node},
    }}}

    try:
        r = httpx.post(f"{WASENDER_URL}/api/decrypt-media",
                       headers=_HEADERS, json=corpo, timeout=45)
        if r.status_code not in (200, 201):
            log.warning("[media] decrypt-media respondeu %s: %s",
                        r.status_code, r.text[:200])
            return ""
        j = r.json() or {}
        url_publica = j.get("publicUrl") or ""
        if not url_publica:
            log.warning("[media] decrypt-media sem publicUrl: %s", str(j)[:200])
            return ""
    except Exception as e:
        log.warning("[media] erro no decrypt-media: %r", e)
        return ""

    try:
        r2 = httpx.get(url_publica, timeout=45, follow_redirects=True)
        if r2.status_code == 200 and r2.content:
            log.info("[media] %s baixado e descriptografado: %d bytes",
                     tipo, len(r2.content))
            return base64.b64encode(r2.content).decode("ascii")
        log.warning("[media] publicUrl respondeu %s", r2.status_code)
    except Exception as e:
        log.warning("[media] erro ao baixar publicUrl: %r", e)
    return ""


def fetch_media_base64(link: str, msg_id: str = "") -> str:
    """(Compat) Assinatura antiga. A mídia do WhatsApp é criptografada, então
    baixar o link cru não serve — use baixar_midia()."""
    log.warning("[media] fetch_media_base64 chamado (obsoleto); use baixar_midia")
    return ""


# ---------------------------------------------------------------------------
# 4. ESTADO DA SESSÃO
# ---------------------------------------------------------------------------
def instance_state() -> str:
    """Consulta o status da sessão. 'open' = conectada."""
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
