# -*- coding: utf-8 -*-
"""Texto -> áudio. A única porta de síntese de voz do produto.

Existe separado por dois motivos práticos:

1. A ESCOLHA DO PROVEDOR É DO KEVIN, e ele vai testar na mão antes. Trocar
   de fornecedor tem que ser mexer numa variável de ambiente, não reescrever
   o pipeline. Por isso aqui é adaptador, não integração.

2. TTS CUSTA POR MINUTO. Concentrar num arquivo deixa o teto de duração, o
   cache e a contabilidade num lugar só — e não espalhados por quem chama.

O QUE ESTE MÓDULO NÃO FAZ: não decide QUANDO falar (é do `scheduler`), não
escreve o texto (é do `podcast`) e não manda a mensagem (é do `canal`). Ele
recebe texto e devolve bytes.

ESTADO EM 29/08/2026: o provedor padrão é a OpenAI, porque é a chave que o
projeto já tem (`ai_engine` usa `OPENAI_API_KEY` ou `ANTHROPIC_API_KEY`, e a
Anthropic não faz TTS). `VOZ_PROVEDOR=gemini` ou `=elevenlabs` trocam sem
tocar em código, desde que a chave correspondente exista. Sem chave nenhuma,
`disponivel()` devolve False e o podcast simplesmente não é oferecido — o bot
não promete o que não pode entregar.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("resolveai")

PROVEDOR = (os.environ.get("VOZ_PROVEDOR") or "openai").strip().lower()

# Voz feminina, pt-BR, tom de conversa. "nova" e "shimmer" na OpenAI soam
# menos locutor-de-rádio que "onyx"/"echo" — e locutor-de-rádio num áudio de
# WhatsApp soa como propaganda, que é a última coisa que este número precisa.
VOZ_OPENAI = os.environ.get("VOZ_OPENAI") or "nova"
MODELO_OPENAI = os.environ.get("VOZ_MODELO_OPENAI") or "gpt-4o-mini-tts"

# Opus em OGG é o formato de mensagem de voz do WhatsApp. Mandar mp3 faz a
# mensagem chegar como ARQUIVO, com ícone de download — e ninguém baixa
# arquivo de bot. Como nota de voz, toca com um toque.
FORMATO = "opus"
MIME = "audio/ogg"

# Teto de segurança em caracteres. Três minutos de locução são ~2.500
# caracteres; 6.000 é o dobro com folga. Acima disso alguma coisa deu errado
# a montante, e sintetizar seria pagar por um erro.
MAX_CARACTERES = 6000

TIMEOUT_S = 120


def provedor_configurado() -> Optional[str]:
    """Qual provedor tem chave AGORA. None quando nenhum tem.

    Olha só a EXISTÊNCIA da variável, nunca o valor — chave de API não
    precisa passar por lugar nenhum além da biblioteca que a usa.
    """
    if PROVEDOR == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if PROVEDOR == "gemini" and (os.environ.get("GEMINI_API_KEY")
                                 or os.environ.get("GOOGLE_API_KEY")):
        return "gemini"
    if PROVEDOR == "elevenlabs" and os.environ.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    # Sem provedor pedido explicitamente, aceita o que houver.
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def disponivel() -> bool:
    """Dá pra gerar áudio hoje? O podcast só é oferecido quando sim."""
    return provedor_configurado() is not None


def sintetizar(texto: Optional[str]) -> Optional[bytes]:
    """Texto -> bytes de áudio (OGG/Opus). None quando não deu.

    None NÃO É ERRO SILENCIOSO: quem chama trata como "não tem episódio hoje"
    e não manda nada. Mandar um áudio quebrado é pior que não mandar — a
    pessoa toca, não sai som, e conclui que o produto não funciona.
    """
    t = (texto or "").strip()
    if not t:
        return None
    if len(t) > MAX_CARACTERES:
        log.warning("[voz] texto de %d chars acima do teto (%d) — nao sintetizo",
                    len(t), MAX_CARACTERES)
        return None

    prov = provedor_configurado()
    if not prov:
        log.info("[voz] nenhum provedor de voz configurado")
        return None
    try:
        if prov == "openai":
            return _openai(t)
        if prov == "gemini":
            return _gemini(t)
        if prov == "elevenlabs":
            return _elevenlabs(t)
    except Exception:
        log.warning("[voz] %s falhou ao sintetizar", prov, exc_info=True)
        return None
    return None


def _openai(texto: str) -> Optional[bytes]:
    from openai import OpenAI
    cliente = OpenAI(timeout=TIMEOUT_S)
    r = cliente.audio.speech.create(
        model=MODELO_OPENAI, voice=VOZ_OPENAI, input=texto,
        response_format=FORMATO,
        # A instrução de estilo vale mais que a escolha da voz: sem ela o
        # modelo lê como quem narra documentário.
        instructions=("Fale em português do Brasil, ritmo de conversa, "
                      "como quem conta uma novidade pra um amigo. "
                      "Sem entonação de propaganda."))
    dados = r.read() if hasattr(r, "read") else getattr(r, "content", None)
    return dados or None


def _gemini(texto: str) -> Optional[bytes]:
    """Deixado pronto porque o Kevin testou o Gemini na mão.

    Não é o padrão só porque a chave que o projeto tem hoje é a da OpenAI.
    """
    import base64
    import httpx
    chave = (os.environ.get("GEMINI_API_KEY")
             or os.environ.get("GOOGLE_API_KEY"))
    modelo = os.environ.get("VOZ_MODELO_GEMINI") or "gemini-2.5-flash-preview-tts"
    r = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "%s:generateContent" % modelo,
        headers={"x-goog-api-key": chave, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": texto}]}],
              "generationConfig": {
                  "responseModalities": ["AUDIO"],
                  "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                      "voiceName": os.environ.get("VOZ_GEMINI") or "Kore"}}}}},
        timeout=TIMEOUT_S)
    r.raise_for_status()
    partes = (r.json().get("candidates") or [{}])[0] \
        .get("content", {}).get("parts") or []
    for parte in partes:
        dados = (parte.get("inlineData") or {}).get("data")
        if dados:
            return base64.b64decode(dados)
    return None


def _elevenlabs(texto: str) -> Optional[bytes]:
    import httpx
    voz = os.environ.get("VOZ_ELEVENLABS") or "21m00Tcm4TlvDq8ikWAM"
    r = httpx.post(
        "https://api.elevenlabs.io/v1/text-to-speech/%s" % voz,
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                 "Content-Type": "application/json"},
        json={"text": texto, "model_id": "eleven_multilingual_v2",
              "output_format": "opus_48000_64"},
        timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.content or None


# ---------------------------------------------------------------------------
# QUANTO ISSO CUSTA
# ---------------------------------------------------------------------------
# Preço da OpenAI em 08/2026 pro `gpt-4o-mini-tts`: cobrado por caractere de
# entrada. Um episódio de 3 min tem ~2.500 caracteres.
#
# Não é decoração: com 4 episódios por mês por pessoa, o custo do podcast
# entra na mesma conta que decide se R$ 19,90 fecha. Deixar isso implícito é
# como se descobre no fim do mês que a feature comeu a margem.
CUSTO_POR_MILHAO_CARACTERES_USD = 12.0


def custo_estimado_usd(texto: Optional[str]) -> float:
    return (len(texto or "") / 1_000_000.0) * CUSTO_POR_MILHAO_CARACTERES_USD


def custo_mensal_estimado_usd(pessoas: int, episodios_por_mes: int = 4,
                              caracteres_por_episodio: int = 2500) -> float:
    """O que o podcast custa por mês com N pessoas ouvindo."""
    total = max(0, pessoas) * max(0, episodios_por_mes) * caracteres_por_episodio
    return (total / 1_000_000.0) * CUSTO_POR_MILHAO_CARACTERES_USD
