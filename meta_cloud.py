# -*- coding: utf-8 -*-
"""
meta_cloud.py — Integracao com a WhatsApp Cloud API (Meta oficial).
===================================================================
Substitui o wasender.py mantendo EXATAMENTE a mesma interface publica, pra
que o wa_bot.py nao precise ser reescrito:

    send_text(number, text)        -> texto livre (janela de 24h)
    send_template(number, ...)     -> NOVO: proativa (fora da janela)
    to_evolution_shape(payload)    -> traduz webhook p/ o formato interno
    baixar_midia(msg_id, tipo, no) -> audio/foto/doc em base64
    instance_state()               -> "open" se o numero esta saudavel

A DIFERENCA QUE MUDA O PRODUTO
------------------------------
Na Cloud API existem dois mundos, e confundir os dois e o erro classico:

  1. JANELA DE ATENDIMENTO (24h) — abre quando o USUARIO manda mensagem.
     Dentro dela sai texto livre. E DE GRACA e ilimitado.
     -> Onboarding, confirmacao de item, resposta a pergunta: tudo aqui.

  2. FORA DA JANELA — so sai TEMPLATE pre-aprovado, com variaveis.
     Isso e PAGO (utility ~US$ 0,0068 ~ R$ 0,04 por mensagem).
     -> O lembrete das 8h e a cobranca de vencimento moram aqui.

Mandar texto livre fora da janela nao da erro bonito: a Meta aceita o POST e
descarta a mensagem. O usuario nunca recebe e voce nunca fica sabendo. Por
isso send_text() devolve False explicitamente nesse caso, em vez de mentir
que enviou — foi assim que o painel passou dias dizendo "enviada" para
mensagem que a Wasender tinha recusado.

O NOVE DIGITO BRASILEIRO (armadilha que quebra resposta em producao)
-------------------------------------------------------------------
Para numeros do Brasil a Meta MUITAS VEZES devolve o wa_id sem o 9 extra:
o usuario +55 11 98284-0929 chega como wa_id "551182840929" (12 digitos).
Se voce responder para o numero "como ele deveria ser", a Meta nao entrega.
A regra esta aplicada em send_text():
    SEMPRE responda para o wa_id EXATO que veio no webhook.
Por isso to_evolution_shape() guarda o wa_id original em _wa_id.

CONFIG (variaveis de ambiente):
    META_TOKEN=<token permanente do System User>      (obrigatorio)
    META_PHONE_NUMBER_ID=<id do numero, nao o numero> (obrigatorio)
    META_WABA_ID=<id da WhatsApp Business Account>
    META_APP_SECRET=<segredo do app, valida a assinatura do webhook>
    META_VERIFY_TOKEN=<string que voce inventa, p/ o handshake>
    META_API_VERSION=v23.0                             (padrao)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
from typing import Optional

log = logging.getLogger("resolveai")

API_VERSION = os.environ.get("META_API_VERSION", "v23.0")
GRAPH = f"https://graph.facebook.com/{API_VERSION}"

META_TOKEN = os.environ.get("META_TOKEN", "").strip()
PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "").strip()
WABA_ID = os.environ.get("META_WABA_ID", "").strip()
APP_SECRET = os.environ.get("META_APP_SECRET", "").strip()
VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "").strip()

_HEADERS = {
    "Authorization": f"Bearer {META_TOKEN}",
    "Content-Type": "application/json",
}

# Tipos de midia da Cloud API -> nome interno que o wa_bot ja entende.
_MIDIA_MAP = {
    "image": "imageMessage",
    "audio": "audioMessage",
    "document": "documentMessage",
    "video": "videoMessage",
    "sticker": "stickerMessage",
}


def _so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def configurado() -> bool:
    """True se da pra falar com a Meta. Usado pelo canal.py."""
    return bool(META_TOKEN and PHONE_NUMBER_ID)


# ---------------------------------------------------------------------------
# 1. ENVIO — texto livre (so dentro da janela de 24h)
# ---------------------------------------------------------------------------
def send_text(number: str, text: str, tentativas: int = 3) -> bool:
    """Envia texto livre. So funciona DENTRO da janela de 24h.

    Devolve True apenas se a Meta confirmou o aceite com um message id.

    Em 429/5xx tenta de novo com espera crescente. Em erro de regra de
    negocio (janela fechada, numero invalido) NAO insiste — repetir nao
    muda o resultado e so queima cota.
    """
    import time
    import httpx

    if not configurado():
        log.error("[envio] META_TOKEN/META_PHONE_NUMBER_ID ausentes")
        return False

    to = _so_digitos(number)
    if not to:
        log.warning("[envio] numero vazio")
        return False

    corpo = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        # preview_url=False: link de pagamento virando card gigante polui a
        # conversa e empurra o texto pra fora da tela no celular.
        "text": {"preview_url": False, "body": text},
    }

    for tentativa in range(1, tentativas + 1):
        try:
            r = httpx.post(f"{GRAPH}/{PHONE_NUMBER_ID}/messages",
                           headers=_HEADERS, json=corpo, timeout=25)

            if r.status_code == 200:
                j = r.json() or {}
                msgs = j.get("messages") or []
                if msgs and msgs[0].get("id"):
                    return True
                # 200 sem id e resposta estranha — trata como falha, nao como
                # sucesso. Otimismo aqui vira usuario no vacuo.
                log.warning("[envio] 200 sem message id: %s", str(j)[:200])
                return False

            erro = {}
            try:
                erro = (r.json() or {}).get("error") or {}
            except Exception:
                pass
            code = erro.get("code")
            msg = str(erro.get("message", ""))[:200]

            # 131047 = fora da janela de 24h. Precisa de template.
            if code == 131047:
                log.warning("[envio] JANELA FECHADA p/ ...%s — precisa de "
                            "template (use send_template)", to[-4:])
                return False
            if code == 131026:
                log.warning("[envio] ...%s nao recebe WhatsApp", to[-4:])
                return False

            if r.status_code in (429, 500, 502, 503) and tentativa < tentativas:
                espera = min(2 ** tentativa, 30)
                log.info("[envio] %s da Meta; aguardando %ds (%d/%d)",
                         r.status_code, espera, tentativa, tentativas)
                time.sleep(espera)
                continue

            log.warning("[envio] Meta recusou (%s / code=%s): %s",
                        r.status_code, code, msg)
            return False

        except Exception as e:
            if tentativa < tentativas:
                time.sleep(min(2 ** tentativa, 30))
                continue
            log.warning("[envio] ERRO ao enviar: %r", e)
            return False

    return False


# ---------------------------------------------------------------------------
# 2. ENVIO — template (proativa, fora da janela)
# ---------------------------------------------------------------------------
def send_template(number: str, nome_template: str, variaveis: list,
                  idioma: str = "pt_BR", tentativas: int = 3) -> bool:
    """Manda um template APROVADO. Unico jeito de iniciar conversa.

    `variaveis` preenche {{1}}, {{2}}, ... na ordem.

    Cuidado com a categoria: UTILITY e barato, MARKETING custa muito mais e
    ainda pode ser bloqueado pelo usuario nas preferencias. Lembrete de
    conta a vencer e utility. "Aproveite nossa promocao" e marketing — e
    nao e isso que o Resolve AI faz.
    """
    import time
    import httpx

    if not configurado():
        log.error("[template] credenciais da Meta ausentes")
        return False

    to = _so_digitos(number)
    if not to:
        return False

    componentes = []
    if variaveis:
        componentes.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(v)} for v in variaveis],
        })

    corpo = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": nome_template,
            "language": {"code": idioma},
            "components": componentes,
        },
    }

    for tentativa in range(1, tentativas + 1):
        try:
            r = httpx.post(f"{GRAPH}/{PHONE_NUMBER_ID}/messages",
                           headers=_HEADERS, json=corpo, timeout=25)
            if r.status_code == 200:
                j = r.json() or {}
                if (j.get("messages") or [{}])[0].get("id"):
                    return True
                log.warning("[template] 200 sem id: %s", str(j)[:200])
                return False

            erro = {}
            try:
                erro = (r.json() or {}).get("error") or {}
            except Exception:
                pass
            code = erro.get("code")

            # 132001 = template nao existe ou nao foi aprovado ainda.
            # Falha silenciosa aqui = lembrete que nunca chega.
            if code == 132001:
                log.error("[template] %r NAO EXISTE ou nao aprovado no "
                          "idioma %s", nome_template, idioma)
                return False

            if r.status_code in (429, 500, 502, 503) and tentativa < tentativas:
                time.sleep(min(2 ** tentativa, 30))
                continue

            log.warning("[template] Meta recusou (%s / code=%s): %s",
                        r.status_code, code, str(erro.get("message", ""))[:200])
            return False

        except Exception as e:
            if tentativa < tentativas:
                time.sleep(min(2 ** tentativa, 30))
                continue
            log.warning("[template] ERRO: %r", e)
            return False

    return False


# ---------------------------------------------------------------------------
# 3. TRADUCAO DO WEBHOOK (Cloud API -> formato interno)
# ---------------------------------------------------------------------------
def to_evolution_shape(payload: dict) -> Optional[dict]:
    """Recebe o webhook da Cloud API e devolve o mesmo dict que o
    wasender.to_evolution_shape() devolvia. None se nao interessa.

    Formato da Meta:
      entry[].changes[].value.messages[]   <- mensagem recebida
      entry[].changes[].value.statuses[]   <- entregue/lido (ignorado)
      entry[].changes[].value.contacts[]   <- nome do contato (pushName)
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("object") != "whatsapp_business_account":
        return None

    for entry in (payload.get("entry") or []):
        for change in (entry.get("changes") or []):
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}

            msgs = value.get("messages") or []
            if not msgs:
                # statuses (entregue/lido) caem aqui. Nao e erro.
                return None
            m = msgs[0]

            # wa_id EXATO — ver a nota do 9 digito no topo do arquivo.
            wa_id = _so_digitos(m.get("from"))
            if not wa_id:
                return None

            push_name = ""
            for c in (value.get("contacts") or []):
                if _so_digitos(c.get("wa_id")) == wa_id:
                    push_name = ((c.get("profile") or {}).get("name") or "")
                    break

            msg_id = m.get("id", "")
            tipo = m.get("type", "text")

            message = {}
            media_tipo, media_node = "", {}

            if tipo == "text":
                message = {"conversation": (m.get("text") or {}).get("body", "")}

            elif tipo in _MIDIA_MAP:
                node = m.get(tipo) or {}
                media_tipo = _MIDIA_MAP[tipo]
                media_node = node
                if tipo == "image":
                    message = {"imageMessage": {"caption": node.get("caption", "") or ""}}
                elif tipo == "audio":
                    # a Cloud API nao manda duracao; o wa_bot usa isso so pra
                    # recusar audio longo. 0 = "nao sei", deixa passar.
                    message = {"audioMessage": {"seconds": 0}}
                elif tipo == "document":
                    message = {"documentMessage": {
                        "caption": node.get("caption", "") or "",
                        "fileName": node.get("filename", "") or "",
                        "mimetype": node.get("mime_type", "") or ""}}
                elif tipo == "video":
                    message = {"videoMessage": {}}
                elif tipo == "sticker":
                    message = {"stickerMessage": {}}

            elif tipo == "reaction":
                node = m.get("reaction") or {}
                message = {"reactionMessage": {"text": node.get("emoji", "") or ""}}

            elif tipo == "button":
                # resposta de botao de template ("FEITO", "ADIAR")
                message = {"conversation": (m.get("button") or {}).get("text", "")}

            elif tipo == "interactive":
                inter = m.get("interactive") or {}
                titulo = ((inter.get("button_reply") or {}).get("title")
                          or (inter.get("list_reply") or {}).get("title") or "")
                message = {"conversation": titulo}

            else:
                # tipo desconhecido (localizacao, contato, pedido...). Trata
                # como texto vazio pra nao derrubar o fluxo — o motor responde
                # que nao entendeu, que e melhor que silencio.
                message = {"conversation": ""}

            return {
                "event": "messages.upsert",
                "data": {
                    "key": {
                        "remoteJid": f"{wa_id}@s.whatsapp.net",
                        "fromMe": False,
                        "id": msg_id,
                    },
                    "pushName": push_name,
                    "message": message,
                    "_msg_id": msg_id,
                    "_media_tipo": media_tipo,
                    "_media_node": media_node,
                    "_wa_id": wa_id,
                },
            }
    return None


# ---------------------------------------------------------------------------
# 4. VERIFICACAO DO WEBHOOK
# ---------------------------------------------------------------------------
def verificar_handshake(mode: str, token: str, challenge: str) -> Optional[str]:
    """Handshake que a Meta faz UMA vez ao cadastrar a URL do webhook."""
    if mode == "subscribe" and VERIFY_TOKEN and token == VERIFY_TOKEN:
        log.info("[webhook] handshake da Meta verificado")
        return challenge
    log.warning("[webhook] handshake RECUSADO (mode=%r, token confere=%s)",
                mode, bool(VERIFY_TOKEN and token == VERIFY_TOKEN))
    return None


def assinatura_valida(corpo_bruto: bytes, cabecalho: str) -> bool:
    """Confere o X-Hub-Signature-256.

    O /webhook ficou aberto na porta 8000 sem nenhuma validacao: qualquer um
    que descobrisse o IP podia forjar mensagem em nome de qualquer usuario.

    FAIL-CLOSED: sem META_APP_SECRET, recusa. Abrir quando falta config e
    exatamente como buracos assim nascem.
    """
    if not APP_SECRET:
        log.error("[webhook] META_APP_SECRET ausente — recusando (fail-closed)")
        return False
    if not cabecalho or not cabecalho.startswith("sha256="):
        log.warning("[webhook] sem X-Hub-Signature-256")
        return False
    esperado = hmac.new(APP_SECRET.encode("utf-8"),
                        corpo_bruto, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.split("=", 1)[1])


# ---------------------------------------------------------------------------
# 5. DOWNLOAD DE MIDIA
# ---------------------------------------------------------------------------
def baixar_midia(msg_id: str, tipo: str, node: dict) -> str:
    """Devolve o arquivo em base64 — ou "" se falhar.

    Bem mais simples que no Baileys: a Meta ja entrega descriptografado.
    Fluxo: GET /{media_id} -> url temporaria -> GET com Bearer -> base64.

    A url temporaria EXIGE o header Authorization. Baixar sem ele devolve
    401 com corpo HTML que, se voce nao conferir o status, vira "arquivo"
    de 200 bytes que o Whisper recebe e falha calado.
    """
    import httpx

    if not isinstance(node, dict):
        return ""
    media_id = node.get("id") or ""
    if not media_id:
        log.warning("[media] payload sem media id (tipo=%r)", tipo)
        return ""

    try:
        r = httpx.get(f"{GRAPH}/{media_id}", headers=_HEADERS, timeout=30)
        if r.status_code != 200:
            log.warning("[media] lookup respondeu %s: %s",
                        r.status_code, r.text[:200])
            return ""
        url = (r.json() or {}).get("url") or ""
        if not url:
            log.warning("[media] lookup sem url")
            return ""
    except Exception as e:
        log.warning("[media] erro no lookup: %r", e)
        return ""

    try:
        r2 = httpx.get(url, headers={"Authorization": f"Bearer {META_TOKEN}"},
                       timeout=60, follow_redirects=True)
        if r2.status_code == 200 and r2.content:
            log.info("[media] %s baixado: %d bytes", tipo, len(r2.content))
            return base64.b64encode(r2.content).decode("ascii")
        log.warning("[media] download respondeu %s", r2.status_code)
    except Exception as e:
        log.warning("[media] erro no download: %r", e)
    return ""


def fetch_media_base64(link: str, msg_id: str = "") -> str:
    """(Compat) Mantido so pra assinatura antiga nao quebrar import."""
    log.warning("[media] fetch_media_base64 obsoleto; use baixar_midia")
    return ""


# ---------------------------------------------------------------------------
# 6. ESTADO DO NUMERO
# ---------------------------------------------------------------------------
def instance_state() -> str:
    """"open" se o numero esta registrado e saudavel.

    Nao existe "sessao" na Cloud API — nao tem QR pra cair. O que existe e a
    saude do numero: quality rating. Se a Meta rebaixar por reclamacao,
    aparece aqui ANTES de virar bloqueio.
    """
    import httpx
    if not configurado():
        return "sem_credencial"
    try:
        r = httpx.get(
            f"{GRAPH}/{PHONE_NUMBER_ID}",
            headers=_HEADERS,
            params={"fields": "verified_name,quality_rating,"
                              "code_verification_status,platform_type"},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("[estado] %s: %s", r.status_code, r.text[:200])
            return "erro"
        j = r.json() or {}
        qualidade = str(j.get("quality_rating") or "").upper()
        # GREEN/YELLOW/RED. UNKNOWN e normal em numero novo, sem historico.
        if qualidade == "RED":
            log.error("[estado] QUALIDADE VERMELHA — a Meta vai limitar o "
                      "numero se nao melhorar")
            return "qualidade_ruim"
        return "open"
    except Exception as e:
        log.warning("[estado] erro: %r", e)
        return "erro"


def qualidade_numero() -> dict:
    """Detalhe da saude do numero, pro painel e pro relatorio das 8h."""
    import httpx
    if not configurado():
        return {"ok": False, "erro": "sem credencial"}
    try:
        r = httpx.get(
            f"{GRAPH}/{PHONE_NUMBER_ID}",
            headers=_HEADERS,
            params={"fields": "display_phone_number,verified_name,"
                              "quality_rating,messaging_limit_tier,"
                              "code_verification_status"},
            timeout=10,
        )
        if r.status_code != 200:
            return {"ok": False, "erro": f"http {r.status_code}"}
        j = r.json() or {}
        return {
            "ok": True,
            "numero": j.get("display_phone_number", ""),
            "nome": j.get("verified_name", ""),
            "qualidade": j.get("quality_rating", "UNKNOWN"),
            "limite": j.get("messaging_limit_tier", ""),
            "verificado": j.get("code_verification_status", ""),
        }
    except Exception as e:
        return {"ok": False, "erro": repr(e)}
