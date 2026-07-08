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

    # dia 3 — planta a semente: cria um lembrete de teste pra amanhã
    if day == 3 and not db.nudge_already_sent(user, "d3_semente"):
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

    # dia 4 — o momento de valor: o lembrete de teste "chega"
    if day == 4 and not db.nudge_already_sent(user, "d4_momento"):
        return {
            "nudge_id": "d4_momento",
            "message": (f"⏰ Oi, {first}! Lembra do lembrete que criei ontem? "
                        f"*Aqui está ele, na hora certa.* ✅\n\nÉ exatamente "
                        f"assim que eu funciono com as suas contas, consultas "
                        f"e compras: você fala uma vez e esquece. Eu apareço "
                        f"na hora exata.\n\nGostou? Me manda mais uma coisa "
                        f"pra eu lembrar. 🙂"),
        }

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
        corpo = "\n".join(linhas)
        return {
            "nudge_id": "d5_resumo",
            "message": (f"{first}, olha o que a gente já fez juntos esses "
                        f"dias:\n\n{corpo}\n\nE isso é só o começo. Imagina com "
                        f"o mês inteiro. 💚"),
        }

    # dia 6 — aviso de fim + valor entregue + assinatura
    if day == 6 and not db.nudge_already_sent(user, "d6_fim"):
        n_total = len(db.list_items(user["id"]))
        return {
            "nudge_id": "d6_fim",
            "message": (f"{first}, seu teste grátis termina *amanhã*. ⏳\n\n"
                        f"Nesses dias eu já organizei *{n_total} coisa(s)* pra "
                        f"você — e tirei um tanto de peso da sua cabeça.\n\n"
                        f"Pra continuar sem interrupção (R$ 19,90/mês, cancela "
                        f"quando quiser):\n💳 {PAYMENT_LINK}\n\nSeus dados "
                        f"ficam guardados te esperando."),
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
