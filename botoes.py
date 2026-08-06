# -*- coding: utf-8 -*-
"""
botoes.py — Botoes de resposta rapida na conversa.
==================================================
O que muda pro usuario: em vez de digitar "feito", ele toca num botao.
Parece detalhe. Nao e. A diferenca entre responder e deixar pra depois e
exatamente esse atrito — e "deixar pra depois" num app de lembrete e a
morte do produto.

POR QUE ISSO CABE AGORA (e nao depende de aprovacao da Meta)
------------------------------------------------------------
Existe uma confusao comum: "botao na Cloud API precisa de template
aprovado". Nao precisa — isso vale so para mensagem PROATIVA, fora da
janela de 24h. DENTRO da janela (o usuario mandou algo nas ultimas 24h)
da pra mandar mensagem `interactive` com botoes, sem revisao nenhuma.

Como quase tudo que o Resolve AI faz e RESPOSTA, da pra colocar botao
hoje na maior parte da jornada. Template com botao fica so pro lembrete
das 8h, que e o unico caso realmente proativo.

REGRA DE OURO DESTE ARQUIVO
---------------------------
Quem decide qual botao aparece e PYTHON, nao o LLM. O motor continua
escrevendo texto livre; aqui a gente olha o texto pronto e decide, de
forma deterministica e testavel, quais botoes combinam. Se o LLM
decidisse, ia acertar 80% das vezes e a gente nunca saberia quais 20%.

LIMITES DA META (violar = a mensagem some sem erro)
---------------------------------------------------
  - no maximo 3 botoes
  - titulo do botao: 20 caracteres
  - corpo: 1024 caracteres  -> acima disso, cai pra texto puro
  - ids unicos dentro da mensagem
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("resolveai")

MAX_BOTOES = 3
MAX_TITULO = 20
MAX_CORPO = 1024


# ---------------------------------------------------------------------------
# 1. AS REGRAS — texto que vai sair  ->  botoes que combinam
# ---------------------------------------------------------------------------
# Os titulos usam as MESMAS palavras que o motor ja entende digitadas. Assim
# o clique entra pelo caminho ja testado, sem criar um segundo caminho de
# interpretacao que ninguem lembraria de manter.
# ATENCAO antes de mexer aqui:
# Estas regras foram reescritas em 05/08 contra o texto REAL do bot, lido
# numa conversa de verdade no WhatsApp. A primeira versao usava frases que
# eu SUPUS que o bot escrevia ("Guardei:", "de manha ou a noite") e nenhum
# botao apareceu pro usuario — o bot na verdade escreve "Anotado." e
# "Que horas te aviso? Responde manha (08:00) ou noite (20:00)".
# Regra: leia uma conversa antes de escrever regex.
_REGRAS = [
    (
        "alarme_na_hora",
        # "\u23f0 DC, chegou a hora: *Comprar racao* — voce me pediu pra
        #  avisar as 17:30. Responda *feito* que eu dou baixa, ou *adiar 1h*."
        # E o disparo MAIS importante do produto: e o momento em que a
        # pessoa sente o valor. Sair so como texto obrigava a digitar.
        re.compile(r"chegou a hora|responda\s+\*?feito|me pediu pra avisar", re.I),
        [("Feito", "Feito"), ("Adiar 1h", "adiar 1h"),
         ("Adiar amanh\u00e3", "adiar")],
    ),
    (
        "escolher_periodo",
        # "Que horas te aviso? Responde *manh\u00e3* (08:00) ou *noite* (20:00)"
        re.compile(r"que horas te aviso|responde\s+\*?manh", re.I),
        [("Manh\u00e3 (08:00)", "manh\u00e3"), ("Noite (20:00)", "noite")],
    ),
    (
        "confirmar_agendamento",
        # "Correto? Vou te avisar amanh\u00e3, dia 06/08, sobre isso."
        re.compile(r"correto\?", re.I),
        [("Isso mesmo", "sim"), ("Mudar a data", "mudar a data")],
    ),
    (
        "confirmar_baixa",
        re.compile(r"posso dar como feito|voc\u00ea j\u00e1 (pagou|fez|resolveu)", re.I),
        [("Feito", "Feito"), ("Ainda n\u00e3o", "Ainda n\u00e3o")],
    ),
    (
        "cobranca_vencimento",
        re.compile(r"vence (hoje|amanh\u00e3)|est\u00e1 vencid|venceu (hoje|ontem)", re.I),
        [("J\u00e1 paguei", "j\u00e1 paguei"), ("Adiar", "adiar"),
         ("Ainda n\u00e3o", "Ainda n\u00e3o")],
    ),
    (
        "acabou_de_anotar",
        # ACABOU DE ANOTAR -> os botoes tem que CONFIRMAR o que foi entendido.
        # Antes aparecia "Feito / Adiar" no exato momento em que a pessoa
        # tinha ACABADO de criar o item — sem sentido, e foi assim que o
        # Fabio perdeu a lista dele em 05/08 (tocou Feito achando que era
        # "terminei de falar"). Confirmar vem antes de concluir.
        re.compile(r"^\s*(\U0001F4CC\s*)?anotado[.:]|^guardei\s*:", re.I | re.M),
        [("\u2705 Isso mesmo", "isso mesmo"),
         ("\u270F\uFE0F Mudar", "quero mudar"),
         ("\u2795 Add outro", "quero adicionar outro")],
    ),
]


def escolher(texto: str) -> Optional[list]:
    """Devolve a lista de botoes que combina com o texto — ou None.

    A ORDEM das regras importa: a primeira que casar vence. As mais
    especificas ficam em cima, porque "Guardei:" aparece junto com muita
    coisa e roubaria o match das outras.
    """
    if not texto or not texto.strip():
        return None
    if len(texto) > MAX_CORPO:
        # Interativo nao aceita corpo gigante. Melhor texto puro que
        # mensagem que a Meta engole sem avisar.
        log.info("[botoes] texto com %d chars — acima do limite, sem botao",
                 len(texto))
        return None

    for nome, padrao, botoes in _REGRAS:
        if padrao.search(texto):
            log.info("[botoes] regra %r casou", nome)
            return botoes[:MAX_BOTOES]
    return None


# ---------------------------------------------------------------------------
# 2. ENVIO
# ---------------------------------------------------------------------------
def enviar(number: str, texto: str, botoes: list) -> bool:
    """Manda mensagem interativa com botoes de resposta rapida.

    Reaproveita as credenciais do meta_cloud pra nao duplicar config.
    Devolve False se a Meta recusar — quem chama decide se cai pra texto.
    """
    import httpx
    import meta_cloud

    if not meta_cloud.configurado():
        return False

    to = re.sub(r"\D", "", str(number or ""))
    if not to or not botoes:
        return False

    lista = []
    for i, (titulo, _payload) in enumerate(botoes[:MAX_BOTOES]):
        lista.append({"type": "reply",
                      "reply": {"id": f"b{i}", "title": titulo[:MAX_TITULO]}})

    corpo = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {"buttons": lista},
        },
    }

    try:
        r = httpx.post(
            f"{meta_cloud.GRAPH}/{meta_cloud.PHONE_NUMBER_ID}/messages",
            headers=meta_cloud._HEADERS, json=corpo, timeout=25,
        )
        if r.status_code == 200 and (r.json().get("messages") or [{}])[0].get("id"):
            return True
        log.warning("[botoes] Meta recusou (%s): %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        log.warning("[botoes] erro ao enviar: %r", e)
        return False


# ---------------------------------------------------------------------------
# 3. PONTO DE ENTRADA UNICO — e isto que o wa_bot chama
# ---------------------------------------------------------------------------
def enviar_resposta(number: str, texto: str, send_text_fallback) -> bool:
    """Manda com botao se couber; senao manda texto puro.

    NUNCA deixa de enviar. Se o interativo falhar por qualquer motivo, cai
    pro texto — porque mensagem que nao chega e o pior defeito possivel
    deste produto, e ja aconteceu demais neste projeto.
    """
    import canal

    if getattr(canal, "OFICIAL", False):
        botoes = escolher(texto)
        if botoes:
            if enviar(number, texto, botoes):
                return True
            log.info("[botoes] interativo falhou — caindo pra texto puro")
    return send_text_fallback(number, texto)
