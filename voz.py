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

# MP3, e a razão é o DIÁLOGO (M5.0).
#
# O episódio agora tem duas vozes conversando, e isso significa uma chamada
# de síntese por FALA — depois é preciso colar tudo num arquivo só. Colar
# OGG/Opus exige reescrever as páginas do container ou ter ffmpeg, e a
# imagem é `python:3.12-slim`: não tem ffmpeg e botar um só pra isso são
# ~100 MB no build.
#
# MP3 cola por concatenação de frames, em Python puro. A Meta aceita
# `audio/mpeg` em `type: audio`, então a mensagem chega tocável — com botão
# de play, não como card de download. O que se perde em relação ao Opus é a
# onda de voz e o controle de velocidade da nota de voz nativa; o que se
# ganha é a conversa entre duas pessoas, que era o pedido.
FORMATO = "mp3"
MIME = "audio/mpeg"

# QUEM CONVERSA. Um homem e uma mulher, como o Kevin pediu.
#
# "shimmer" e "onyx" foram escolhidas por soarem CONVERSA e não locução:
# "nova" e "echo" têm entonação de quem lê um texto, que é exatamente o
# "robótico" que ele apontou na primeira amostra.
VOZ_MULHER = os.environ.get("VOZ_MULHER") or "shimmer"
VOZ_HOMEM = os.environ.get("VOZ_HOMEM") or "onyx"

# A instrução de estilo pesa mais que a escolha da voz. Sem ela o modelo lê
# como quem narra documentário — e foi assim que soou robótico.
ESTILO = ("Você está gravando um podcast curto em português do Brasil, "
          "conversando com outra pessoa. Fale como quem conta uma novidade "
          "pra um amigo no sofá: ritmo natural, sem pressa, com as pausas "
          "de quem pensa enquanto fala. Nada de entonação de locutor de "
          "rádio, nada de voz de propaganda, nada de solenidade.")

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


def _tem_chave(prov: Optional[str]) -> bool:
    """Existe chave pro provedor pedido? Só a EXISTÊNCIA, nunca o valor."""
    if prov == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if prov == "elevenlabs":
        return bool(os.environ.get("ELEVENLABS_API_KEY"))
    if prov == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY")
                    or os.environ.get("GOOGLE_API_KEY"))
    return False


def disponivel() -> bool:
    """Dá pra gerar áudio hoje? O podcast só é oferecido quando sim."""
    return provedor_configurado() is not None


def sintetizar(texto: Optional[str],
               provedor: Optional[str] = None) -> Optional[bytes]:
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

    # PROVEDOR PEDIDO NA HORA, pra comparar um contra o outro sem trocar o
    # default de todo mundo. Só vale se a chave dele existir — pedir um
    # provedor sem chave é pedir o que não dá pra entregar.
    prov = (provedor or "").strip().lower() or provedor_configurado()
    if provedor and not _tem_chave(prov):
        log.warning("[voz] provedor %r pedido mas sem chave", prov)
        return None
    if not prov:
        log.info("[voz] nenhum provedor de voz configurado")
        return None

    import podcast
    falas = podcast.falas(t)
    try:
        if not falas:
            # Texto sem diálogo: uma voz só. Continua servindo pra qualquer
            # outro uso e é o caminho do roteiro determinístico antigo.
            return _uma_voz(prov, t, "mulher")
        return _dialogo(prov, falas)
    except Exception:
        log.warning("[voz] %s falhou ao sintetizar", prov, exc_info=True)
        return None


def _dialogo(prov: str, falas: list) -> Optional[bytes]:
    """Uma chamada por fala, na voz de quem fala, coladas num arquivo só.

    EM PARALELO, com ordem preservada. Um episódio tem 12 a 18 falas; em
    série isso são uns dois minutos de espera, e a pessoa tocou num botão
    esperando um áudio. O `ThreadPoolExecutor` mantém a ordem do `map`, que
    é o que importa: fala fora de ordem vira conversa sem sentido.
    """
    import concurrent.futures as cf

    def _um(par):
        quem, texto = par
        return _uma_voz(prov, texto, quem)

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        pedacos = list(ex.map(_um, falas))

    bons = [p for p in pedacos if p]
    if not bons:
        return None
    if len(bons) != len(falas):
        # UMA FALA QUE FALTA ESTRAGA A CONVERSA. Melhor não mandar do que
        # mandar um diálogo com buraco no meio — a pessoa ouve uma pergunta
        # sem resposta e conclui que o produto está quebrado.
        log.warning("[voz] %d de %d falas sintetizadas — nao mando pela "
                    "metade", len(bons), len(falas))
        return None
    return _colar_mp3(bons)


def _uma_voz(prov: str, texto: str, quem: str) -> Optional[bytes]:
    """`quem` e "mulher" ou "homem"; cada provedor tem o seu par de vozes."""
    if prov == "openai":
        return _openai(texto,
                       VOZ_MULHER if quem == "mulher" else VOZ_HOMEM)
    if prov == "elevenlabs":
        return _elevenlabs(texto,
                           VOZ_11_MULHER if quem == "mulher" else VOZ_11_HOMEM)
    if prov == "gemini":
        return _gemini(texto)
    return None


def _sem_id3(dados: bytes) -> bytes:
    """Tira as etiquetas ID3 pra que os frames colem limpos.

    Um ID3v2 no meio do arquivo faz parte dos players engasgarem ou pularem
    a fala. O ID3v1 do fim tem o mesmo efeito na emenda seguinte.
    """
    if not dados:
        return b""
    if dados[:3] == b"ID3" and len(dados) > 10:
        # tamanho em syncsafe: sete bits por byte
        n = 0
        for b in dados[6:10]:
            n = (n << 7) | (b & 0x7F)
        dados = dados[10 + n:]
    if dados[-128:][:3] == b"TAG":
        dados = dados[:-128]
    return dados


def _colar_mp3(pedacos: list) -> Optional[bytes]:
    """Junta os trechos num MP3 só. Python puro, sem ffmpeg."""
    limpos = [_sem_id3(p) for p in pedacos if p]
    limpos = [p for p in limpos if p]
    return b"".join(limpos) if limpos else None


def _openai(texto: str, voz: Optional[str] = None) -> Optional[bytes]:
    from openai import OpenAI
    cliente = OpenAI(timeout=TIMEOUT_S)
    r = cliente.audio.speech.create(
        model=MODELO_OPENAI, voice=voz or VOZ_OPENAI, input=texto,
        response_format=FORMATO, instructions=ESTILO)
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


# Vozes da ElevenLabs. Os ids são das vozes prontas do catálogo e podem
# mudar com o tempo — por isso são variáveis de ambiente, e não constantes:
# trocar de voz não pode exigir deploy.
VOZ_11_MULHER = os.environ.get("VOZ_ELEVENLABS_MULHER") or "21m00Tcm4TlvDq8ikWAM"
VOZ_11_HOMEM = os.environ.get("VOZ_ELEVENLABS_HOMEM") or "pNInz6obpgDQGcFmaJgB"
MODELO_11 = os.environ.get("VOZ_MODELO_ELEVENLABS") or "eleven_multilingual_v2"


def _elevenlabs(texto: str, voz: Optional[str] = None) -> Optional[bytes]:
    """MP3, e não Opus: o diálogo precisa ser COLADO fala a fala.

    A `stability` baixa e o `style` alto são o que dá variação de entonação —
    é justamente o "robótico" que o Kevin apontou na primeira amostra. Voz
    estável demais lê igual do começo ao fim, e conversa não é assim.
    """
    import httpx
    escolhida = voz or VOZ_11_MULHER
    r = httpx.post(
        "https://api.elevenlabs.io/v1/text-to-speech/%s" % escolhida,
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                 "Content-Type": "application/json"},
        params={"output_format": "mp3_44100_128"},
        json={"text": texto, "model_id": MODELO_11,
              "voice_settings": {"stability": 0.38,
                                 "similarity_boost": 0.80,
                                 "style": 0.55,
                                 "use_speaker_boost": True}},
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
