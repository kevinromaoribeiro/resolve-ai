"""
teste_v18.py — O que o bot cuspiu sozinho em 04/08 às 07:59–08:00.

Quatro mensagens em UM minuto, uma por item vencido:
    07:59 "condominio venceu ontem... Já pagou?"
    07:59 "definir próxima pós graduação venceu ontem... Já pagou?"   <- pagar o quê?
    08:00 "comprar mamão (R$ 10,00) venceu ontem... Já pagou?"
    08:00 "comprar ração da Nina programado para 05/08
           🛒 Resolver em 1 clique: mercadolivre.com/...&tag=resolveai-20"

Três defeitos:
  A) LINK DE AFILIADO — contraria o guardrail (lembra/organiza/registra, não
     vende) e a decisão de não ligar afiliado antes de ter retenção.
  B) RAJADA — 4 vibrações em 1 minuto. É assim que a conversa é arquivada.
  C) "Já pagou?" sobre um item sem dinheiro nenhum.

Rodar:  python teste_v18.py
"""
import os
import sys
import tempfile
from datetime import date, timedelta

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t18.db")
os.environ.pop("AFILIADOS_ATIVOS", None)

import tempo        # noqa: E402
import db           # noqa: E402
import ai_engine    # noqa: E402
import scheduler    # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


# ---------------------------------------------------------------------------
print("A. Link de afiliado DESLIGADO por padrão")
for termo in ["ração", "racao", "filtro de agua", "oleo", "fralda", "cafe"]:
    check(f"sem link p/ {termo!r}",
          ai_engine.affiliate_link_for(termo) is None,
          str(ai_engine.affiliate_link_for(termo)))
check("flag está off", ai_engine.AFILIADOS_ATIVOS is False)
check("a função interna ainda existe (religável)",
      callable(getattr(ai_engine, "_affiliate_link_for", None)))

# ---------------------------------------------------------------------------
print("\nB. Vencidos viram UMA mensagem, não uma por item")
db.init_db()
uid = db.create_user(nome="Kevin Ribeiro", telefone="5511900007777")
db.update_user_fields(uid, onboarding_step="done", status="ativo")
ontem = (tempo.hoje() - timedelta(days=1)).isoformat()
db.add_item(uid, "lembrete", "Contas", "condominio", data_vencimento=ontem)
db.add_item(uid, "lembrete", "Outros", "definir próxima pós graduação",
            data_vencimento=ontem)
db.add_item(uid, "despesa", "Alimentação", "comprar mamão",
            valor_reais=10.0, data_vencimento=ontem)
db.add_item(uid, "lembrete", "Pet", "comprar ração da Nina",
            data_vencimento=ontem)

d = scheduler.check_overdue()
com_texto = [x for x in d if (x.get("message") or "").strip()]
sem_texto = [x for x in d if not (x.get("message") or "").strip()]
print(f"\n    4 itens vencidos -> {len(com_texto)} mensagem(ns) + "
      f"{len(sem_texto)} registro(s) de dedup")
print("\n--- a ÚNICA mensagem que ele vai receber ---")
print(com_texto[0]["message"] if com_texto else "(nenhuma)")
print("---\n")
check("manda 1 mensagem só", len(com_texto) == 1, f"{len(com_texto)}")
check("registra os 4 pro dedup", len(d) == 4, f"{len(d)}")
msg = com_texto[0]["message"] if com_texto else ""
for nome in ["condominio", "pós graduação", "mamão", "ração"]:
    check(f"cita {nome!r}", nome in msg, msg[:80])
check("mostra o valor de quem tem", "R$ 10,00" in msg, msg)
check("não vaza link de compra", "mercadolivre" not in msg.lower(), msg)
check("ensina como resolver", "feito" in msg.lower(), msg)

print("\nB2. Item único não vira lista")
uid2 = db.create_user(nome="Ana", telefone="5511900008888")
db.update_user_fields(uid2, onboarding_step="done", status="ativo")
db.add_item(uid2, "despesa", "Contas", "conta de luz", valor_reais=187.4,
            data_vencimento=ontem)
d2 = [x for x in scheduler.check_overdue()
      if x["user_id"] == uid2 and (x.get("message") or "").strip()]
check("1 item = 1 mensagem", len(d2) == 1, f"{len(d2)}")
check("não usa formato de lista", "\n• " not in d2[0]["message"],
      d2[0]["message"])
check("valor formatado", "R$ 187,40" in d2[0]["message"], d2[0]["message"])

# ---------------------------------------------------------------------------
print("\nC. 'Já pagou?' só quando existe dinheiro")
check("despesa -> pergunta pago",
      "pago" in scheduler._pergunta_baixa(
          {"tipo": "despesa", "valor_reais": None}))
check("item com valor -> pergunta pago",
      "pago" in scheduler._pergunta_baixa(
          {"tipo": "lembrete", "valor_reais": 90.0}))
check("lembrete sem valor -> pergunta FEITO",
      "feito" in scheduler._pergunta_baixa(
          {"tipo": "lembrete", "valor_reais": None}),
      scheduler._pergunta_baixa({"tipo": "lembrete", "valor_reais": None}))
check("'pós graduação' não leva 'já pagou'",
      "pagou" not in scheduler._pergunta_baixa(
          {"tipo": "lembrete", "valor_reais": None}))

# ---------------------------------------------------------------------------
print("\nD. Dedup segue valendo: não repete no ciclo seguinte")
for x in d:
    db.log_dispatch(x["user_id"], "vencido", x["item_id"])
d3 = [x for x in scheduler.check_overdue() if x["user_id"] == uid]
check("segundo ciclo não manda nada", d3 == [], f"{len(d3)}")

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
