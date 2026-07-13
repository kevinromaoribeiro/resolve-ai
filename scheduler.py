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
import tempo
from typing import Optional

import db

DUE_WINDOW_DAYS = 3
CHURN_THRESHOLD_DAYS = 10
CHURN_COOLDOWN_DAYS = 7          # anti-churn no máx. 1x por semana
DUE_ALERT_DAYS = {3, 1, 0}       # vencimento avisa em D-3, D-1 e no dia
QUIET_START, QUIET_END = 21, 8   # silêncio 21h–8h (exceto alarme com hora)


def _in_quiet_hours(now: Optional[datetime] = None) -> bool:
    h = (now or tempo.agora()).hour
    return h >= QUIET_START or h < QUIET_END


def _fmt_br(iso: Optional[str]) -> str:
    if not iso:
        return "sem data"
    y, m, d = iso.split("-")
    return f"{d}/{m}"


def check_due_items(ref: Optional[date] = None) -> list[dict]:
    """Checagem 1: vencimentos — avisa em D-3, D-1 e no dia, 1x por dia
    por item (dedup via log de disparos)."""
    ref = ref or date.today()
    dispatches: list[dict] = []
    for user in db.list_users():
        if not db.user_can_receive(user):
            continue  # trial expirado sem pagamento: silêncio (exceto winback)
        due = db.items_due_within(user["id"], days=DUE_WINDOW_DAYS, ref=ref)
        for item in due:
            if "(lembrete de demonstração)" in (item.get("descricao") or ""):
                continue  # o trial guiado entrega esse momento (d4)
            rec = item.get("recorrencia") or ""
            if rec == "diaria" or rec.startswith("horas"):
                continue  # o alarme de hora já cobre; evita aviso duplo
            if item.get("data_vencimento"):
                y, m, d = map(int, item["data_vencimento"].split("-"))
                days_left = (date(y, m, d) - ref).days
                if days_left not in DUE_ALERT_DAYS:
                    continue
                if days_left == 0 and item.get("hora_alvo"):
                    continue  # D-0 com hora marcada: o alarme ⏰ é o aviso
            kind = "1-click-buy" if item.get("link_afiliado") else "vencimento"
            if db.dispatched_today(kind, user["id"], item["id"]):
                continue
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
            else:
                is_conta = (item.get("tipo") == "despesa"
                            or item.get("valor_reais"))
                valor = (f" de *R$ {item['valor_reais']:.2f}*".replace(".", ",")
                         if item.get("valor_reais") else "")
                if is_conta:
                    msg = (
                        f"💡 {first_name}, passando pra lembrar: "
                        f"*{item['descricao']}*{valor} vence em *{venc}*.\n\n"
                        f"Quando pagar, é só me dizer *paguei* que eu dou "
                        f"baixa. Se quiser adiar o aviso, responda *adiar*."
                    )
                else:
                    msg = (
                        f"📌 {first_name}, lembrete: *{item['descricao']}* "
                        f"— marcado para *{venc}*.\n\n"
                        f"Já resolveu? Responda *feito* que eu tiro da lista. "
                        f"Quer adiar? É só dizer *adiar*."
                    )
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
    """Checagem 2: gatilho D+10 de inatividade — no máx. 1x por semana."""
    dispatches: list[dict] = []
    for user in db.inactive_users(days=CHURN_THRESHOLD_DAYS, ref=ref):
        if not db.user_can_receive(user):
            continue
        if db.dispatched_within("anti-churn", user["id"],
                                days=CHURN_COOLDOWN_DAYS):
            continue
        if db.dispatch_count("anti-churn", user["id"]) >= CHURN_MAX_ATTEMPTS:
            continue  # desiste com elegância após 3 tentativas
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
        if db.dispatched_ever("trial-ending", user["id"]):
            continue
        if db.nudge_already_sent(user, "d6_fim"):
            continue  # o trial guiado já fez o push do D6 (evita msg dupla)
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


OVERDUE_NUDGE_DAYS = 1           # cobrança única D+1 pós-vencimento
ARCHIVE_AFTER_DAYS = 15          # cadáver arquivado (e avisado) em D+15
CHURN_MAX_ATTEMPTS = 3           # anti-churn desiste após 3 tentativas


def roll_recurring(ref: Optional[date] = None) -> int:
    """Rola itens recorrentes vencidos/concluídos para a próxima ocorrência."""
    ref = ref or date.today()
    rolls: list[tuple] = []
    for item in db.recurring_to_roll(ref):
        rec = item["recorrencia"]
        venc = item.get("data_vencimento") or ref.isoformat()
        y, m, d = map(int, venc.split("-"))
        base = date(y, m, d)
        hora = item.get("hora_alvo")
        if rec == "diaria":
            nxt = max(base + timedelta(days=1), ref)
        elif rec.startswith("semanal:"):
            alvo = int(rec.split(":")[1])
            nxt = base + timedelta(days=1)
            while nxt.weekday() != alvo or nxt < ref:
                nxt += timedelta(days=1)
        elif rec.startswith("mensal:"):
            dd = int(rec.split(":")[1])
            y2, m2 = base.year + (base.month == 12), base.month % 12 + 1
            try:
                nxt = date(y2, m2, dd)
            except ValueError:
                nxt = date(y2, m2, 28)
            while nxt < ref:
                y2, m2 = nxt.year + (nxt.month == 12), nxt.month % 12 + 1
                try:
                    nxt = date(y2, m2, dd)
                except ValueError:
                    nxt = date(y2, m2, 28)
        elif rec.startswith("horas:"):
            step = int(rec.split(":")[1])
            if hora:
                h, mi = map(int, hora.split(":"))
                prox = datetime(base.year, base.month, base.day, h, mi) \
                    + timedelta(hours=step)
                while prox < tempo.agora():
                    prox += timedelta(hours=step)
                rolls.append((item["id"], prox.date().isoformat(),
                              prox.strftime("%H:%M")))
            continue
        else:
            continue
        rolls.append((item["id"], nxt.isoformat(), hora))
    db.roll_items_batch(rolls)
    return len(rolls)


def check_overdue(ref: Optional[date] = None) -> list[dict]:
    """Cobrança única D+1 e arquivamento com aviso em D+15 (não-recorrentes)."""
    ref = ref or date.today()
    dispatches: list[dict] = []
    for item in db.overdue_items(days_ago=OVERDUE_NUDGE_DAYS, ref=ref):
        if "(lembrete de demonstração)" in (item.get("descricao") or ""):
            continue
        u = db.get_user(item["user_id"])
        if not u or not db.user_can_receive(u):
            continue
        venc = item["data_vencimento"]
        y, m, d = map(int, venc.split("-"))
        atraso = (ref - date(y, m, d)).days
        first = (item.get("user_nome") or "").split()[0] or "Oi"
        if atraso >= ARCHIVE_AFTER_DAYS:
            if not db.dispatched_ever_item("arquivado", item["id"]):
                db.archive_item(item["id"])
                dispatches.append({
                    "user_id": item["user_id"], "telefone": item["telefone"],
                    "item_id": item["id"], "kind": "arquivado",
                    "message": (f"Arquivei *{item['descricao']}* — venceu há "
                                f"{atraso} dias sem baixa. Se ainda estiver em "
                                f"aberto, me manda de novo que eu reagendo. 🗂️")})
        elif atraso >= OVERDUE_NUDGE_DAYS:
            if not db.dispatched_ever_item("vencido", item["id"]):
                valor = (f" (R$ {item['valor_reais']:.2f})".replace(".", ",")
                         if item.get("valor_reais") else "")
                dispatches.append({
                    "user_id": item["user_id"], "telefone": item["telefone"],
                    "item_id": item["id"], "kind": "vencido",
                    "message": (f"{first}, *{item['descricao']}*{valor} venceu "
                                f"{'ontem' if atraso == 1 else f'há {atraso} dias'} "
                                f"e não vi a baixa. Já pagou? Responda *pago* — "
                                f"ou *adiar* se precisar de fôlego.")})
    return dispatches


def check_time_alarms(ref: Optional[datetime] = None) -> list[dict]:
    """Checagem 0 (v6.3): alarmes com hora — itens de HOJE cuja hora_alvo
    chegou. Dispara no minuto (rodando o cron a cada 5-15 min), 1x por item.
    Ignora horário de silêncio: hora explícita é pedido explícito."""
    now = ref or tempo.agora()
    dispatches: list[dict] = []
    for item in db.items_due_at_time(now):
        u = db.get_user(item["user_id"])
        if not u or not db.user_can_receive(u):
            continue
        if db.dispatched_today("hora", item["user_id"], item["id"]):
            continue
        first_name = (item.get("user_nome") or "").split()[0] or "Oi"
        valor = (f" ({'R$ %.2f' % item['valor_reais']})".replace(".", ",")
                 if item.get("valor_reais") else "")
        msg = (f"⏰ {first_name}, chegou a hora: *{item['descricao']}*"
               f"{valor} — você me pediu pra avisar às {item['hora_alvo']}.\n"
               f"Responda *feito* que eu dou baixa, ou *adiar 1h*.")
        dispatches.append({
            "user_id": item["user_id"],
            "user_nome": item.get("user_nome", ""),
            "telefone": item["telefone"],
            "item_id": item["id"],
            "kind": "hora",
            "message": msg,
        })
    return dispatches


def check_winback() -> list[dict]:
    """1 única mensagem 3 dias após o trial expirar sem conversão."""
    dispatches: list[dict] = []
    for user in db.winback_candidates():
        if db.dispatched_ever("winback", user["id"]):
            continue
        first_name = user["nome"].split()[0]
        pend = db.list_items(user["id"], status="pendente")
        gancho = (f"Seu *{pend[0]['descricao']}* continua aqui me esperando. "
                  if pend else "")
        dispatches.append({
            "user_id": user["id"], "user_nome": user["nome"],
            "telefone": user["telefone"], "item_id": None, "kind": "winback",
            "message": (f"Oi {first_name}! Seu teste do Resolve AI acabou "
                        f"há alguns dias. {gancho}Se fez falta, é só assinar "
                        f"que tudo volta na hora — e se não fez, sem "
                        f"problema: essa é a última mensagem que te mando. 🤝"),
        })
    return dispatches


def run_proactive_engine(
    ref_date: Optional[date] = None,
    ref_datetime: Optional[datetime] = None,
) -> dict:
    """
    Executa o ciclo completo. Pode rodar a cada 5-15 minutos com segurança:
    o log de disparos garante que ninguém recebe mensagem repetida.
    Alarmes com hora furam o silêncio; o resto respeita 8h-21h.
    """
    now = ref_datetime or tempo.agora()
    roll_recurring(ref=ref_date)          # recorrentes rolam ANTES de tudo
    alarms = check_time_alarms(ref=now)
    if _in_quiet_hours(now):
        due, churn, trial, guided, overdue = [], [], [], [], []
    else:
        overdue = check_overdue(ref=ref_date) + check_winback()
        due = check_due_items(ref=ref_date)
        churn = check_churn(ref=ref_datetime)
        try:
            import trial_guiado
            guided = trial_guiado.run_trial_nudges()
        except Exception:
            guided = []
        trial = check_trial_ending()  # fallback: só quem NÃO recebeu o d6
    return {
        "executed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "alarm_dispatches": alarms,
        "overdue_dispatches": overdue,
        "due_dispatches": due,
        "churn_dispatches": churn,
        "trial_dispatches": trial,
        "guided_dispatches": guided,
        "total": (len(alarms) + len(overdue) + len(due) + len(churn)
                  + len(trial) + len(guided)),
    }


def simulate_next_day() -> dict:
    """Simula a execução do cronjob no dia seguinte (D+1)."""
    tomorrow = date.today() + timedelta(days=1)
    tomorrow_dt = tempo.agora() + timedelta(days=1)
    return run_proactive_engine(ref_date=tomorrow, ref_datetime=tomorrow_dt)
