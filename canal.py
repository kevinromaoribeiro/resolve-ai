# -*- coding: utf-8 -*-
"""
canal.py — Escolhe por qual canal o bot fala com o WhatsApp.
============================================================
O wa_bot.py nao deve saber se esta falando com a WasenderAPI (nao-oficial) ou
com a Cloud API da Meta (oficial). Ele importa daqui e pronto:

    import canal as wasender      # <- unica mudanca no wa_bot.py

POR QUE UMA CAMADA A MAIS
-------------------------
Migracao de canal e o tipo de mudanca que quebra em producao as 3h da manha.
Com este arquivo, voltar atras e UMA variavel de ambiente — nao um rollback
de deploy com usuario no vacuo no meio do caminho.

    CANAL=meta        forca a Cloud API oficial
    CANAL=wasender    forca a WasenderAPI (nao-oficial)
    CANAL=auto        (padrao) usa a Meta se configurada; senao Wasender

O "auto" existe pra que colar META_TOKEN e META_PHONE_NUMBER_ID no EasyPanel
ja vire a chave. Sem token da Meta, nada muda e o bot segue no canal antigo.
"""
from __future__ import annotations

import logging
import os

import meta_cloud
import wasender

log = logging.getLogger("resolveai")

_ESCOLHA = (os.environ.get("CANAL", "auto") or "auto").strip().lower()

if _ESCOLHA == "meta":
    _mod = meta_cloud
    if not meta_cloud.configurado():
        # Falha ALTA e cedo. O contrario — cair calado pro Wasender — faz o
        # dono achar que migrou quando nao migrou, e so descobrir na fatura
        # (ou no proximo bloqueio).
        log.error("[canal] CANAL=meta mas META_TOKEN/META_PHONE_NUMBER_ID "
                  "estao vazios. O envio VAI FALHAR ate configurar.")
elif _ESCOLHA == "wasender":
    _mod = wasender
else:
    _mod = meta_cloud if meta_cloud.configurado() else wasender

NOME = "meta" if _mod is meta_cloud else "wasender"
OFICIAL = _mod is meta_cloud

log.info("[canal] falando pelo canal: %s", NOME)


# --- interface que o wa_bot.py consome -------------------------------------
send_text = _mod.send_text
to_evolution_shape = _mod.to_evolution_shape
baixar_midia = _mod.baixar_midia
instance_state = _mod.instance_state
fetch_media_base64 = _mod.fetch_media_base64


# --- so existe na Meta; no Wasender vira no-op honesto ---------------------
def send_template(number: str, nome_template: str, variaveis: list,
                  idioma: str = "pt_BR") -> bool:
    """Mensagem proativa fora da janela de 24h.

    No canal nao-oficial nao existe template: manda-se texto livre e torce.
    Foi exatamente essa liberdade que rendeu 2 restricoes da Meta em 24h.
    Aqui a diferenca fica explicita em vez de escondida.
    """
    if OFICIAL:
        return meta_cloud.send_template(number, nome_template, variaveis, idioma)
    log.warning("[canal] send_template no Wasender: nao existe template")
    return False


# ---------------------------------------------------------------------------
# M2.0 — A PORTA UNICA DE SAIDA PROATIVA
# ---------------------------------------------------------------------------
# Quais templates a Meta ja aprovou. A fonte da verdade e o Business Manager,
# nao o nosso banco — banco daria a ilusao de "aprovado" sem a Meta ter
# aprovado. Default VAZIO de proposito: enquanto ninguem configurar, nada sai
# fora da janela e a falha aparece no log, em vez de virar mensagem que a
# pessoa nunca recebe.
def _aprovados() -> set:
    bruto = os.environ.get("TEMPLATES_APROVADOS", "") or ""
    return {p.strip() for p in bruto.split(",") if p.strip()}


def falar(telefone: str, texto: str, *, user_id=None, template=None,
          variaveis=None, tipo: str = "texto") -> dict:
    """Fala com o usuario respeitando a janela de 24h. Devolve o que houve.

    {"enviado": bool, "via": "texto"|"template"|None, "motivo": str}

    DENTRO da janela  -> texto livre (a pessoa falou com o bot ha pouco).
    FORA da janela    -> so template aprovado. Sem ele, NAO SAI.

    O "nao sai" e a parte que importa. Antes do M2.0 o codigo mandava texto
    livre nos dois casos; no canal oficial a Meta recusa com 131047 e o
    lembrete morre calado. Agora a recusa e nossa, e ela e explicita: quem
    chamou recebe o motivo e registra.

    Regra em Python, nunca no prompt: o LLM nao decide se pode falar fora da
    janela, e caminho novo que queira mandar proativa passa por aqui.
    """
    import db

    variaveis = list(variaveis or [])
    # telefone vai junto porque o webhook grava msg_log com user_id nulo —
    # sem ele a janela nunca abre e NADA sai (auditoria M2.0, P0-1).
    if db.dentro_da_janela(user_id, telefone):
        ok = send_text(telefone, texto)
        return {"enviado": bool(ok), "via": "texto" if ok else None,
                "motivo": "" if ok else "falha_no_envio"}

    if not template:
        return {"enviado": False, "via": None,
                "motivo": "fora_da_janela_sem_template"}

    import templates as _cat
    if template not in _cat.CATALOGO:
        log.error("[canal] template %r nao esta no catalogo do repo", template)
        return {"enviado": False, "via": None,
                "motivo": "template_fora_do_catalogo"}

    if template not in _aprovados():
        log.warning("[canal] template %r ainda nao aprovado — nao enviei "
                    "(configure TEMPLATES_APROVADOS depois do Business "
                    "Manager)", template)
        return {"enviado": False, "via": None,
                "motivo": "template_nao_aprovado"}

    if not OFICIAL:
        # Canal reserva nao tem template. Mandar texto livre aqui seria
        # repetir exatamente o que rendeu duas restricoes da Meta em 24h.
        log.warning("[canal] fora da janela no canal reserva: nao envio")
        return {"enviado": False, "via": None, "motivo": "canal_sem_template"}

    idioma = _cat.CATALOGO[template].idioma
    ok = send_template(telefone, template, variaveis, idioma)
    return {"enviado": bool(ok), "via": "template" if ok else None,
            "motivo": "" if ok else "template_recusado"}


def suporta_botoes() -> bool:
    """True se o canal aceita botao de resposta rapida.

    Botao so existe na API oficial. E o que transforma "responda FEITO" em
    um toque — e a diferenca entre o usuario responder e o usuario deixar
    pra depois e justamente esse atrito.
    """
    return OFICIAL


def diagnostico() -> dict:
    """Pro /health e pro painel: por onde falamos e como vai a saude."""
    d = {"canal": NOME, "oficial": OFICIAL, "estado": instance_state()}
    if OFICIAL:
        d.update(meta_cloud.qualidade_numero())
    return d
