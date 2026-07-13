"""
trial_guiado.py — Trial ATIVO e guiado do RESOLVE AI (7 dias).

A percepção de valor de um produto de carga mental não vem do cadastro —
vem do momento em que o app AVISA algo que a pessoa tinha esquecido.
Este módulo faz o app provocar esse momento dentro dos 7 dias, em vez de
esperar passivamente.

Sequência (disparada 1x/dia pelo scheduler, sem duplicar):
- dia 0: já é o onboarding (boas-vindas). Não repete aqui.
- dia 1: convida o primeiro teste real (foto de boleto)
- dia 2: pede pra cadastrar um aniversário que sempre esquece
- dia 3: cria um LEMBRETE DE TESTE que dispara no dia 4 (semente do "momento")
- dia 4: o lembrete de teste chega → primeiro momento de valor real
- dia 5: mostra o resumo ("olha o que você já organizou")
- dia 6: aviso de fim de trial + o quanto já ajudou + link de assinatura

Cada nudge é enviado só uma vez (controle em users.trial_nudges_sent).
Personaliza pelos interesses escolhidos no onboarding quando possível.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional

import db

PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://SEU-LINK-DE-PAGAMENTO")
PAYMENT_LINK_ANUAL = os.environ.get("PAYMENT_LINK_ANUAL", "")
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))

# Exemplos de primeiro teste, escolhidos pelo interesse do usuário
PRIMEIRO_TESTE = {
    "contas": "manda uma *foto de qualquer boleto* (luz, água, internet) que eu leio o valor e o vencimento e te lembro antes.",
    "mercado": "manda um áudio tipo _\"comprei arroz, café e detergente hoje\"_ que eu registro e te aviso quando for hora de repor.",
    "carro": "me fala a quilometragem do seu carro (ex.: _\"troquei o óleo, tô com 45 mil km\"_) que eu calculo a próxima troca.",
    "saude": "me fala uma consulta ou exame que você tem marcado (ex.: _\"dentista dia 20 às 14h\"_) que eu te lembro na hora certa.",
    "datas": "me conta um aniversário que você sempre esquece (ex.: _\"aniversário da minha mãe é 03/09\"_) que eu nunca deixo passar.",
    "encomendas": "me fala de alguma encomenda a caminho (ex.: _\"comprei um tênis, chega sexta\"_) que eu cobro o prazo por você.",
    "pet": "me fala da última compra do seu pet (ex.: _\"comprei ração hoje\"_) que eu te aviso quando estiver acabando.",
    "burocracia": "me fala de um documento com prazo (ex.: _\"IPVA vence dia 15\"_) que eu te lembro com antecedência.",
}


def _first_interest(user: dict) -> str:
    ints = (user.get("interesses") or "").split(",")
    ints = [i for i in ints if i]
    return ints[0] if ints else "contas"


def _first_name(user: dict) -> str:
    return user["nome"].split()[0]


def build_nudge(user: dict) -> Optional[dict]:
    """
    Decide qual nudge (se algum) enviar ao usuário hoje, conforme o dia do
    trial e o que já foi enviado. Retorna dict de dispatch ou None.
    Também pode CRIAR um item de teste (dia 3) como efeito colateral.
    """
    day = db.trial_day_number(user)
    first = _first_name(user)

    # dia 1 — primeiro teste real, personalizado pelo interesse
    if day == 1 and not db.nudge_already_sent(user, "d1_teste"):
        interesse = _first_interest(user)
        exemplo = PRIMEIRO_TESTE.get(interesse, PRIMEIRO_TESTE["contas"])
        return {
            "nudge_id": "d1_teste",
            "message": (f"Bom dia, {first}! 🌱 Bora fazer seu primeiro teste "
                        f"de verdade?\n\nAgora {exemplo}\n\nÉ só mandar aqui. "
                        f"Leva 5 segundos."),
        }

    # dia 2 — cadastrar um aniversário (uso emocional, fácil de topar)
    if day == 2 and not db.nudge_already_sent(user, "d2_aniversario"):
        return {
            "nudge_id": "d2_aniversario",
            "message": (f"{first}, uma coisa que quase todo mundo esquece: "
                        f"*datas importantes*. 🎂\n\nMe conta um aniversário "
                        f"que você não pode deixar passar (ex.: _\"aniversário "
                        f"do meu pai é 12/10\"_) que eu te aviso com "
                        f"antecedência, todo ano."),
        }

    # dia 3 — semente: se já há lembrete REAL a caminho, amplifica ele
    if day == 3 and not db.nudge_already_sent(user, "d3_semente"):
        reais = [i for i in db.list_items(user["id"], status="pendente")
                 if i.get("data_vencimento")
                 and i["data_vencimento"] > date.today().isoformat()]
        if reais:
            prox = min(reais, key=lambda i: i["data_vencimento"])
            y, m, d = prox["data_vencimento"].split("-")
            return {
                "nudge_id": "d3_semente",
                "message": (f"{first}, só passando pra dizer: *{prox['descricao']}* "
                            f"tá no meu radar — dia {d}/{m} eu te chamo aqui, "
                            f"antes de vencer. 👀\n\nVocê não precisa fazer "
                            f"nada. É assim que funciona: você fala uma vez, "
                            f"eu carrego a preocupação."),
            }
        amanha = (date.today() + timedelta(days=1)).isoformat()
        db.add_item(
            user_id=user["id"], tipo="lembrete", categoria="Outros",
            descricao="testar o Resolve AI (lembrete de demonstração)",
            valor_reais=None, data_vencimento=amanha, status="pendente",
        )
        return {
            "nudge_id": "d3_semente",
            "message": (f"{first}, deixa eu te mostrar como funciona na "
                        f"prática. 👀\n\nAcabei de criar um *lembrete de "
                        f"demonstração* pra você. Amanhã, nesse mesmo horário, "
                        f"eu vou te avisar dele — pra você sentir como é ter "
                        f"alguém lembrando por você.\n\nPode continuar sua "
                        f"vida. Eu te aviso. 😉"),
        }

    # dia 4 — o momento de valor (demo entregue OU valor real referenciado)
    if day == 4 and not db.nudge_already_sent(user, "d4_momento"):
        demo = None
        for i in db.list_items(user["id"], status="pendente"):
            if "(lembrete de demonstração)" in (i.get("descricao") or ""):
                demo = i
                db.update_item_status(i["id"], "concluido")
        if demo:
            msg = (f"⏰ Oi, {first}! Lembra do lembrete que criei ontem? "
                   f"*Aqui está ele, na hora certa.* ✅\n\nÉ exatamente assim "
                   f"que eu funciono com as suas contas, consultas e compras: "
                   f"você fala uma vez e esquece. Eu apareço na hora exata."
                   f"\n\nGostou? Me manda mais uma coisa pra eu lembrar. 🙂")
        else:
            pend = [i for i in db.list_items(user["id"], status="pendente")
                    if i.get("data_vencimento")]
            alvo = min(pend, key=lambda i: i["data_vencimento"]) if pend else None
            gancho = (f"*{alvo['descricao']}* tá comigo — no dia certo eu te "
                      f"chamo aqui, sem você pedir de novo. "
                      if alvo else "")
            msg = (f"{first}, é assim que eu funciono: {gancho}Você fala uma "
                   f"vez e esquece; eu apareço na hora exata. ✅\n\nMe manda "
                   f"mais uma coisa que pesa na sua cabeça — conta, consulta, "
                   f"data — que eu assumo ela também. 🙂")
        return {"nudge_id": "d4_momento", "message": msg}

    # dia 5 — resumo do que já foi organizado
    if day == 5 and not db.nudge_already_sent(user, "d5_resumo"):
        itens = db.list_items(user["id"])
        n_total = len(itens)
        n_pend = len(db.list_items(user["id"], status="pendente"))
        gasto = db.month_spend(user["id"])
        linhas = [f"📋 {n_total} coisa(s) organizadas por mim"]
        if n_pend:
            linhas.append(f"⏰ {n_pend} lembrete(s) ativos te esperando")
        if gasto > 0:
            linhas.append(f"💰 R$ {gasto:.2f} em gastos registrados".replace(".", ","))
        pend_datados = [i for i in db.list_items(user["id"], status="pendente")
                        if i.get("data_vencimento")
                        and i["data_vencimento"] >= date.today().isoformat()]
        if pend_datados:
            prox = min(pend_datados, key=lambda i: i["data_vencimento"])
            y, m, d = prox["data_vencimento"].split("-")
            linhas.append(f"🔜 Próximo na mira: *{prox['descricao']}* "
                          f"(dia {d}/{m} eu te aviso)")
        corpo = "\n".join(linhas)
        return {
            "nudge_id": "d5_resumo",
            "message": (f"{first}, olha o que a gente já fez juntos esses "
                        f"dias:\n\n{corpo}\n\nE isso é só o começo. Imagina com "
                        f"o mês inteiro. 💚"),
        }

    # dia 6 — ÚNICO push do dia (o aviso genérico do scheduler vira fallback)
    if day == 6 and not db.nudge_already_sent(user, "d6_fim"):
        n_total = len(db.list_items(user["id"]))
        n_pend = len(db.list_items(user["id"], status="pendente"))
        prova = (f"Nesses 6 dias eu organizei *{n_total} coisa(s)* pra você"
                 + (f" e ainda tenho *{n_pend} lembrete(s)* armado(s) te "
                    f"esperando" if n_pend else "") + ".")
        anual = (f"\n📅 Ou 1 ano por R$ 149 (sai R$ 12,40/mês):"
                 f"\n{PAYMENT_LINK_ANUAL}" if PAYMENT_LINK_ANUAL else "")
        return {
            "nudge_id": "d6_fim",
            "message": (f"{first}, seu teste termina *amanhã*. ⏳\n\n{prova}"
                        f"\n\nPra isso continuar rodando sem interrupção:"
                        f"\n💳 R$ 19,90/mês (cancela com uma mensagem):"
                        f"\n{PAYMENT_LINK}{anual}\n\nSem pressão: se não "
                        f"assinar, seus dados ficam guardados 30 dias."),
        }

    # dia 7 — último dia: aversão à perda, 1 única mensagem
    if day == 7 and not db.nudge_already_sent(user, "d7_ultimo"):
        n_pend = len(db.list_items(user["id"], status="pendente"))
        perda = (f"seu{'s' if n_pend>1 else ''} *{n_pend} lembrete{'s' if n_pend>1 else ''} ativo{'s' if n_pend>1 else ''}* para{'m' if n_pend>1 else ''} de tocar amanhã"
                 if n_pend else "amanhã eu paro de te avisar das coisas")
        anual = (f" — ou R$ 149 no ano (R$ 12,40/mês): {PAYMENT_LINK_ANUAL}"
                 if PAYMENT_LINK_ANUAL else "")
        return {
            "nudge_id": "d7_ultimo",
            "message": (f"{first}, hoje é o *último dia* do seu teste — "
                        f"{perda}. 🥲\n\nSe eu fiz falta essa semana, é um "
                        f"clique pra tudo continuar: {PAYMENT_LINK}{anual}"
                        f"\n\nSe não fez, tudo bem de verdade — obrigado "
                        f"por testar. E o *apagar meus dados* está sempre "
                        f"à disposição. 🤝"),
        }

    return None


def run_trial_nudges() -> list[dict]:
    """
    Percorre os usuários em trial ativo e monta os nudges do dia.
    Retorna lista de dispatches {user_id, telefone, user_nome, nudge_id, message}.
    Marca cada nudge como enviado (idempotente).
    """
    dispatches = []
    for user in db.active_trial_users(TRIAL_DAYS):
        nudge = build_nudge(user)
        if nudge:
            db.mark_nudge_sent(user["id"], nudge["nudge_id"])
            dispatches.append({
                "user_id": user["id"],
                "telefone": user["telefone"],
                "user_nome": user["nome"],
                "kind": "trial-guiado",
                "nudge_id": nudge["nudge_id"],
                "message": nudge["message"],
            })
    return dispatches
