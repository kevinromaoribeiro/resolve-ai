"""
scheduler.py — Motor de Disparo Proativo & Anti-Churn do RESOLVE AI.

Executado sob demanda pelo botão de testes na sidebar do app
("⚡ Executar Motor de Disparo Proativo"), simulando o cronjob diário.

Duas checagens:
1. Vencimentos em D+3 com 1-Click Buy (link de afiliado) para itens físicos.
2. Anti-Churn: usuários inativos há mais de 10 dias recebem mensagem
   de reativação de utilidade imediata.

Retorna uma lista de "dispatches" (dicts) que o app renderiza como se
fossem mensagens proativas de WhatsApp.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import db

DUE_WINDOW_DAYS = 3
CHURN_THRESHOLD_DAYS = 10


def _fmt_br(iso: Optional[str]) -> str:
    if not iso:
        return "sem data"
    y, m, d = iso.split("-")
    return f"{d}/{m}"


def check_due_items(ref: Optional[date] = None) -> list[dict]:
    """Checagem 1: itens pendentes vencendo em até 3 dias, por usuário."""
    ref = ref or date.today()
    dispatches: list[dict] = []
    for user in db.list_users():
        due = db.items_due_within(user["id"], days=DUE_WINDOW_DAYS, ref=ref)
        for item in due:
            first_name = user["nome"].split()[0]
            venc = _fmt_br(item["data_vencimento"])
            if item.get("link_afiliado"):
                msg = (
                    f"⏰ {first_name}, seu item *{item['descricao']}* está "
                    f"programado para {venc}.\n\n"
                    f"🛒 *Resolver em 1 clique* (reposição com o melhor preço):\n"
                    f"{item['link_afiliado']}\n\n"
                    f"Responda *feito* quando resolver que eu baixo da sua lista."
                )
                kind = "1-click-buy"
            else:
                valor = (f" de R$ {item['valor_reais']:.2f}".replace(".", ",")
                         if item.get("valor_reais") else "")
                msg = (
                    f"⏰ {first_name}, lembrete: *{item['descricao']}*{valor} "
                    f"vence em {venc}. Quer que eu marque como pago ou "
                    f"adie o lembrete?"
                )
                kind = "vencimento"
            dispatches.append({
                "user_id": user["id"],
                "user_nome": user["nome"],
                "telefone": user["telefone"],
                "item_id": item["id"],
                "kind": kind,
                "message": msg,
            })
    return dispatches


def check_churn(ref: Optional[datetime] = None) -> list[dict]:
    """Checagem 2: gatilho D+10 de inatividade -> reativação por utilidade."""
    dispatches: list[dict] = []
    for user in db.inactive_users(days=CHURN_THRESHOLD_DAYS, ref=ref):
        first_name = user["nome"].split()[0]
        msg = (
            f"Opa, {first_name}! Vi que sua semana foi corrida. Que tal "
            f"esvaziar a cabeça agora? Manda por áudio em 10 segundos ou "
            f"tira print de alguma encomenda para rastrear ou da "
            f"quilometragem do carro. Eu organizo tudo."
        )
        dispatches.append({
            "user_id": user["id"],
            "user_nome": user["nome"],
            "telefone": user["telefone"],
            "item_id": None,
            "kind": "anti-churn",
            "message": msg,
        })
    return dispatches


def check_trial_ending() -> list[dict]:
    """Checagem 3: trial termina amanhã -> aviso com link de pagamento."""
    import os
    payment = os.environ.get("PAYMENT_LINK", "https://SEU-LINK-DE-PAGAMENTO")
    dispatches: list[dict] = []
    for user in db.trial_ending_users(days_left=1):
        first_name = user["nome"].split()[0]
        msg = (
            f"⏳ {first_name}, seu teste grátis termina *amanhã*. "
            f"Curtiu ter a cabeça mais leve?\n\n"
            f"💳 Continue por R$ 19,90/mês: {payment}\n\n"
            f"Se não assinar, tudo bem — seus dados ficam guardados 30 dias "
            f"caso mude de ideia."
        )
        dispatches.append({
            "user_id": user["id"],
            "user_nome": user["nome"],
            "telefone": user["telefone"],
            "item_id": None,
            "kind": "trial-ending",
            "message": msg,
        })
    return dispatches


def run_proactive_engine(
    ref_date: Optional[date] = None,
    ref_datetime: Optional[datetime] = None,
) -> dict:
    """
    Executa o ciclo completo do cronjob.
    `ref_date`/`ref_datetime` permitem simular 'o dia seguinte' nos testes.
    """
    due = check_due_items(ref=ref_date)
    churn = check_churn(ref=ref_datetime)
    trial = check_trial_ending()
    return {
        "executed_at": (ref_datetime or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
        "due_dispatches": due,
        "churn_dispatches": churn,
        "trial_dispatches": trial,
        "total": len(due) + len(churn) + len(trial),
    }


def simulate_next_day() -> dict:
    """Simula a execução do cronjob no dia seguinte (D+1)."""
    tomorrow = date.today() + timedelta(days=1)
    tomorrow_dt = datetime.now() + timedelta(days=1)
    return run_proactive_engine(ref_date=tomorrow, ref_datetime=tomorrow_dt)
