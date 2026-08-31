# -*- coding: utf-8 -*-
"""
trial_guiado.py — Régua de engajamento do trial de 7 dias (CRM / funil).
=========================================================================
Objetivo: garantir que no D7 a pessoa QUEIRA ficar. Cada dia do trial tem
um objetivo de funil diferente — não é "mandar mensagem", é mover a pessoa
de "testei uma vez" até "não quero mais perder isso".

FILOSOFIA (chapéu de sênior de CRM):
- Nudge é para quem ESFRIOU. Se a pessoa usou nas últimas 24h, não empurra —
  quem já está engajado não precisa de lembrete, precisa de espaço.
- Personalizado pelos INTERESSES do onboarding. Falar de carro pra quem
  escolheu "contas" é ruído. Relevância > frequência.
- 1 toque por dia no MÁXIMO, e só se fizer sentido. Silêncio proposital em
  dias que a pessoa está ativa.
- Cada nudge tem UMA ação clara (CTA), curto, com prova de valor imediata.
- Dedup total via db.mark_nudge_sent: ninguém recebe o mesmo nudge 2x.

RÉGUA (objetivo de funil por dia):
  D1  ATIVAÇÃO   — quem não registrou nada: puxa o 1º uso (o "aha" mais cedo)
  D2  HÁBITO     — sugere 2º caso de uso, dentro do interesse escolhido
  D3  AMPLIAÇÃO  — mostra um interesse que ela escolheu mas ainda não usou
  D4  AHA PROATIVO— evidencia o valor único: "eu te avisei sem você pedir"
  D5  PROVA      — resume o que já tirou da cabeça dela (valor acumulado)
  D6  CONVERSÃO  — fim do trial amanhã + link de pagamento (fecha o funil)

Cada função devolve dispatches no formato do scheduler:
  {"user_id","user_nome","telefone","item_id":None,"kind","message"}
"""
from __future__ import annotations

import os
from typing import Optional

import db
import tempo

# OS MESMOS DEFAULTS REAIS DO `wa_bot`, e pelo mesmo motivo documentado la:
# o default antigo aqui era "https://SEU-LINK-DE-PAGAMENTO", e uma VPS sem a
# variavel entregava esse placeholder literal no fechamento do trial — o
# unico momento em que o produto pede dinheiro. Link de cobranca e publico
# por natureza; versiona-lo nao expoe segredo nenhum.
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://mpago.la/2oashdp")
PAYMENT_LINK_ANUAL = os.environ.get("PAYMENT_LINK_ANUAL",
                                    "https://mpago.la/2n5pEVS")
# MENOR QUE A JANELA DE 24h, e isso não é ajuste fino: é o que faz a régua
# existir. O nudge sai como texto livre (nenhum `trial_d*` tem template, de
# propósito), e texto livre só passa pra quem falou nas ÚLTIMAS 24h. Com
# `INACTIVE_HOURS = 24` as duas condições se excluíam — "calado há 24h+" e
# "dentro da janela de 24h" não acontecem juntos. A mensagem era gerada e
# descartada na linha seguinte, sem erro e sem log.
#
# 18h abre uma faixa real de 6 horas: quem falou ontem à noite recebe o
# toque na tarde seguinte. Quem sumiu de vez só com template aprovado, e não
# existe um pra nudge de trial — mas quem sumiu de vez também não é quem
# esta régua existe pra ativar.
INACTIVE_HOURS = int(os.environ.get("TRIAL_INACTIVE_HOURS", "18"))
# Dia em que a régua fecha e pede a assinatura. Trial de 14 dias -> D13,
# para a mensagem de conversão chegar ANTES do prazo acabar, não depois.
# MESMA ENV QUE O `wa_bot` e o `db` leem. Sem isto o D5 dizia "faltam 2
# dias" — verdade num trial de 7 dias, mentira nos 14 de hoje.
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "14"))
# O MESMO numero que o `wa_bot` libera de verdade. O texto dizia "falo com
# o Kevin e libero alguns dias" — ninguem e consultado (a extensao e
# automatica) e "alguns" deixava a pessoa esperando uma semana pra
# receber dois dias. Promessa vaga no fechamento e a pior hora pra
# frustrar.
TRIAL_EXTENSAO_DIAS = int(os.environ.get("TRIAL_EXTENSAO_DIAS", "2"))
DIA_FECHAMENTO = int(os.environ.get("TRIAL_DIA_FECHAMENTO", "13"))


# ── Sugestões por interesse (CTA único, prova de valor no minuto 1) ──────
# Alinhado com USE_CASE_EXAMPLES do textos.py, mas em tom de reengajamento.
_POR_INTERESSE = {
    "contas": "manda a foto de um boleto (ou digita _\"luz 180 vence dia 20\"_). "
              "Eu te aviso 3 dias antes, 1 dia antes e no dia — multa nunca mais.",
    "mercado": "fala _\"acabou o café\"_ que eu lembro na próxima compra e "
               "aviso quando for hora de repor.",
    "carro": "manda _\"troquei o óleo, 74.200 km\"_ — eu calculo a próxima "
             "troca e te aviso com folga. IPVA e seguro também.",
    "saude": "diz _\"dermato dia 15/08 às 14h\"_ que eu te lembro na véspera "
             "e no dia. Remédio contínuo também.",
    "datas": "manda _\"aniversário da minha mãe é 03/09\"_ — eu te aviso todo "
             "ano com antecedência pra dar tempo do presente.",
    "encomendas": "fala _\"minha encomenda chega até sexta\"_ que eu fico de "
                  "olho no prazo por você.",
    "pet": "manda _\"vacina da Mel dia 30\"_ — eu aviso antes, e lembro da "
           "ração quando estiver acabando.",
    "burocracia": "diz _\"IPVA vence 15/01\"_ ou _\"renovar CNH em março\"_ — "
                  "eu te aviso com folga, sem susto.",
}
_ORDEM_GENERICA = ["contas", "saude", "carro", "datas",
                   "encomendas", "pet", "mercado", "burocracia"]


def _first_name(user: dict) -> str:
    return (user.get("nome") or "").split()[0] or "Oi"


def _interesses(user: dict) -> list[str]:
    raw = (user.get("interesses") or "").strip()
    return [i for i in raw.split(",") if i] if raw else []


def _hours_since_interaction(user: dict) -> float:
    ts = user.get("ultima_interacao")
    if not ts:
        return 9999.0
    try:
        from datetime import datetime
        last = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return (tempo.agora() - last).total_seconds() / 3600.0
    except Exception:
        return 9999.0


def _is_cold(user: dict) -> bool:
    """Só nudge em quem esfriou (não fala há INACTIVE_HOURS+)."""
    return _hours_since_interaction(user) >= INACTIVE_HOURS


def _registrou_algo(user_id: int) -> bool:
    try:
        return len(db.list_items(user_id)) > 0
    except Exception:
        return False


def _interesse_nao_usado(user: dict) -> Optional[str]:
    """Um interesse que a pessoa escolheu mas ainda não gerou item —
    a melhor 'próxima sugestão'. Se não achar, cai no 1º interesse."""
    ints = _interesses(user)
    if not ints:
        return None
    # heurística simples: sugere o 2º interesse (o 1º já foi puxado no D1/D2)
    return ints[1] if len(ints) > 1 else ints[0]


def _sugestao_para(user: dict, prefer: Optional[str] = None) -> tuple[str, str]:
    """Retorna (chave_interesse, texto_cta). Usa o interesse pedido, senão
    o 1º do onboarding, senão o genérico 'contas'."""
    ints = _interesses(user)
    chave = prefer or (ints[0] if ints else "contas")
    if chave not in _POR_INTERESSE:
        chave = next((i for i in _ORDEM_GENERICA if i in _POR_INTERESSE), "contas")
    return chave, _POR_INTERESSE[chave]


def _mk(user: dict, kind: str, message: str, nudge: str = "") -> dict:
    """Monta o disparo. `nudge` e a chave do dedup — e quem marca e QUEM ENVIA.

    AUDITORIA M2.0 (16/08/2026): o `db.mark_nudge_sent` era chamado aqui, na
    GERACAO. Com o M2.0 recusando envio fora da janela, o nudge era queimado
    sem nunca ter saido e nao voltava mais — inclusive o `d6_fim`, que e a
    UNICA mensagem de conversao do trial, com o link de pagamento. Marcar
    antes de enviar e apagar dado do usuario em silencio.
    """
    return {
        "user_id": user["id"],
        "user_nome": user.get("nome", ""),
        "telefone": user["telefone"],
        "item_id": None,
        "kind": kind,
        "message": message,
        "nudge": nudge,
    }


# ── A régua ───────────────────────────────────────────────────────────────
def run_trial_nudges() -> list[dict]:
    """Roda a régua para todos os usuários em trial. Chamada pelo scheduler.
    Retorna a lista de dispatches (o scheduler envia e marca como enviado)."""
    dispatches: list[dict] = []
    for user in db.active_trial_users():
        dia = db.trial_day_number(user)          # 0=entrou hoje, 1=amanhã...
        first = _first_name(user)

        # ÚLTIMO DIA do trial: CONVERSÃO com tom de mordomo. Sempre manda.
        if dia >= DIA_FECHAMENTO and not db.nudge_already_sent(user, "d6_fim"):
            n_itens = len(db.list_items(user["id"]))
            palavra = "coisa" if n_itens == 1 else "coisas"
            prova = (f"Nesses dias, já tirei *{n_itens} {palavra}* da sua "
                     f"cabeça. " if n_itens else "")
            planos = f"*1* — Mensal, R$ 19,90/mês\n{PAYMENT_LINK}"
            if PAYMENT_LINK_ANUAL:
                planos += (f"\n\n*2* — Anual, R$ 149,90/ano _(2 meses grátis)_\n"
                           f"{PAYMENT_LINK_ANUAL}")
            msg = (f"{first}, foram duas semanas boas. 🤝 Eu nunca fiz nada "
                   f"por você — quem paga, compra e resolve continua sendo "
                   f"você. O que eu fiz foi *não deixar passar*.\n\n"
                   f"{prova}Mas por hoje o seu teste "
                   f"chega ao fim. Pra continuar comigo, é só escolher:\n\n"
                   f"{planos}\n\n"
                   f"Toca no plano que preferir e pronto. 💛\n\n"
                   f"E se você ainda não teve tempo de me testar direito, "
                   f"responde *mais tempo* que eu libero mais "
                   f"*{TRIAL_EXTENSAO_DIAS}* dias na hora. Não quero que "
                   f"você decida sem ter visto o que eu faço. Nada some: "
                   f"seus itens ficam aqui.")
            dispatches.append(_mk(user, "trial_d6", msg,
                                         nudge="d6_fim"))
            continue

        # Dias 1–5: só toca quem ESFRIOU (inativo 24h+). Quem usa hoje, deixa em paz.
        if not _is_cold(user):
            continue

        # D1: ATIVAÇÃO — o mais importante. Se não registrou nada, puxa o 1º uso.
        if dia == 1 and not db.nudge_already_sent(user, "d1"):
            if not _registrou_algo(user["id"]):
                chave, cta = _sugestao_para(user)
                msg = (f"{first}, ontem você começou comigo mas ainda não me "
                       f"deu nada pra cuidar. 😊 Bora testar em 10 segundos: "
                       f"{cta}\n\nÉ só mandar — eu faço o resto.")
            else:
                msg = (f"{first}, vi que você já começou a usar. 🙌 Manda mais "
                       f"uma coisa que te preocupa hoje — conta, consulta, "
                       f"compra — que eu tiro da sua cabeça.")
            dispatches.append(_mk(user, "trial_d1", msg,
                                         nudge="d1"))
            continue

        # D2: HÁBITO — 2ª sugestão, dentro do interesse escolhido.
        if dia == 2 and not db.nudge_already_sent(user, "d2"):
            chave, cta = _sugestao_para(user)
            msg = (f"{first}, dica rápida: {cta}\n\nQuanto mais você me conta, "
                   f"menos você precisa lembrar. Esse é o ponto. 🧠")
            dispatches.append(_mk(user, "trial_d2", msg,
                                         nudge="d2"))
            continue

        # D3: AMPLIAÇÃO — mostra um interesse escolhido que ela ainda não usou.
        if dia == 3 and not db.nudge_already_sent(user, "d3"):
            prox = _interesse_nao_usado(user)
            chave, cta = _sugestao_para(user, prefer=prox)
            msg = (f"{first}, você me disse que também se preocupa com isso — "
                   f"então: {cta}\n\nPosso cuidar de várias frentes ao mesmo "
                   f"tempo, sem você se perder.")
            dispatches.append(_mk(user, "trial_d3", msg,
                                         nudge="d3"))
            continue

        # D4: AHA PROATIVO — o diferencial. "Eu te aviso sem você pedir."
        if dia == 4 and not db.nudge_already_sent(user, "d4"):
            msg = (f"{first}, o que me torna diferente de uma listinha: eu "
                   f"*te aviso na hora certa, sozinho*. Você não precisa abrir "
                   f"nada nem lembrar de checar. Me dá um vencimento ou uma "
                   f"data que eu provo isso nos próximos dias. ⏰")
            dispatches.append(_mk(user, "trial_d4", msg,
                                         nudge="d4"))
            continue

        # D5: PROVA — valor acumulado. Quanto ela já tirou da cabeça.
        if dia == 5 and not db.nudge_already_sent(user, "d5"):
            n = len(db.list_items(user["id"]))
            if n > 0:
                msg = (f"{first}, em 5 dias você já colocou *{n} coisa(s)* pra "
                       f"eu cuidar. Isso é {n} preocupação(ões) que saíram da "
                       f"sua cabeça. 🧘 Imagina isso todo mês, no automático.")
            else:
                chave, cta = _sugestao_para(user)
                _faltam = max(1, db.trial_days_left(user, TRIAL_DAYS))
                msg = (f"{first}, faltam *{_faltam}* dias do seu teste e eu "
                       f"ainda não te "
                       f"mostrei o melhor. Testa agora: {cta}")
            dispatches.append(_mk(user, "trial_d5", msg,
                                         nudge="d5"))
            continue

        # ── SEGUNDA SEMANA (trial de 14 dias) ────────────────────────────
        # O trial virou 14 dias porque conta de luz, consulta e revisão não
        # cabem numa janela de 7 — a pessoa saía sem nunca ter vivido o
        # momento pelo qual pagaria. Mas esticar o prazo sem esticar a régua
        # deixaria os dias 6–13 em SILÊNCIO, que é pior que trial curto:
        # sete dias sem o bot falar ensinam que dá pra viver sem ele.
        # Continua valendo a trava do _is_cold: quem está usando hoje não
        # recebe nada.

        # D7: MARCO — uma semana. Prova concreta do que já foi entregue.
        if dia == 7 and not db.nudge_already_sent(user, "d7"):
            n_disp = db.dispatch_count("hora", user["id"]) + \
                db.dispatch_count("vencimento", user["id"])
            if n_disp:
                msg = (f"{first}, uma semana comigo. Nesse tempo eu já te "
                       f"avisei *{n_disp} vez(es)* de coisa que você não "
                       f"precisou lembrar sozinho. 🧠 Essa é a conta que "
                       f"importa.")
            else:
                chave, cta = _sugestao_para(user)
                msg = (f"{first}, faz uma semana e eu ainda não te avisei de "
                       f"nada — porque você ainda não me deu uma data. É aí "
                       f"que eu sou bom. Testa: {cta}")
            dispatches.append(_mk(user, "trial_d7", msg,
                                         nudge="d7"))
            continue

        # D9: ANTECIPAÇÃO — o diferencial que nenhuma agenda tem.
        if dia == 9 and not db.nudge_already_sent(user, "d9"):
            msg = (f"{first}, um truque que quase ninguém usa: me conta o que "
                   f"você compra sempre — *ração, filtro, remédio de uso "
                   f"contínuo, troca de óleo*. Eu calculo quando vai acabar e "
                   f"te aviso ANTES, sem você pedir. 🔄\n\n"
                   f"Manda um: _\"comprei ração de 15kg hoje\"_.")
            dispatches.append(_mk(user, "trial_d9", msg,
                                         nudge="d9"))
            continue

        # D11: DINHEIRO — mostra o retrovisor de gastos que ela nem sabia ter.
        if dia == 11 and not db.nudge_already_sent(user, "d11"):
            gasto = db.month_spend(user["id"])
            if gasto > 0:
                v = f"{gasto:,.2f}".replace(",", "X").replace(".", ",") \
                                   .replace("X", ".")
                msg = (f"{first}, sem você fazer planilha nenhuma, eu já sei "
                       f"que você gastou *R$ {v}* esse mês. 💸 Pergunta "
                       f"_\"quanto eu gastei?\"_ quando quiser — eu separo por "
                       f"categoria na hora.")
            else:
                msg = (f"{first}, além de lembrar, eu somo. Manda os gastos "
                       f"do jeito que vierem — _\"mercado 180\"_, _\"uber "
                       f"32\"_ — e depois pergunta *quanto eu gastei*. Sai "
                       f"tudo separado por categoria, sem planilha. 💸")
            dispatches.append(_mk(user, "trial_d11", msg,
                                         nudge="d11"))
            continue

        # D12: AVISO HONESTO — dois dias antes, sem susto e sem pressão.
        if dia == 12 and not db.nudge_already_sent(user, "d12"):
            msg = (f"{first}, seu teste termina em 2 dias. Sem susto: eu aviso "
                   f"antes, não depois. 🙂\n\n"
                   f"Se ainda não deu pra testar direito, responde *mais "
                   f"tempo* que eu resolvo. Prefiro que você decida tendo "
                   f"visto o serviço funcionando.")
            dispatches.append(_mk(user, "trial_d12", msg,
                                         nudge="d12"))
            continue

    return dispatches
