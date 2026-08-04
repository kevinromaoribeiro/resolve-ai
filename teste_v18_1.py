"""
teste_v18_1.py — Freio anti-bloqueio.

Em 04/08 a Meta restringiu o número por 3h: "atividade pode caracterizar envio
de spam, mensagens automáticas ou em massa". O gatilho foi RITMO, não conteúdo.

E a configuração permitia coisa muito pior do que os 4/min que aconteceram:
    DISPATCH_MAX_PER_CYCLE = 60   com ciclo de 60s  ->  60 msg em 1 minuto

Quatro freios:
  1. teto por ciclo (60 -> 5)
  2. espaçamento de 8-15s COM VARIAÇÃO (intervalo exato também é assinatura)
  3. teto por usuário/dia (6)
  4. termômetro no /health pra medir antes de bater no limite

Rodar:  python teste_v18_1.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t181.db")
os.environ.setdefault("PAINEL_TOKEN", "teste")

import tempo      # noqa: E402
import db         # noqa: E402
import wa_bot     # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


# ---------------------------------------------------------------------------
print("1. Os limites saíram do vermelho")
check("teto por ciclo <= 8", wa_bot.DISPATCH_MAX_PER_CYCLE <= 8,
      f"{wa_bot.DISPATCH_MAX_PER_CYCLE}")
check("NÃO é mais 60", wa_bot.DISPATCH_MAX_PER_CYCLE != 60)
check("espaçamento mínimo >= 5s", wa_bot.ENVIO_INTERVALO_MIN >= 5,
      f"{wa_bot.ENVIO_INTERVALO_MIN}")
check("tem variação (min != max)",
      wa_bot.ENVIO_INTERVALO_MAX > wa_bot.ENVIO_INTERVALO_MIN,
      f"{wa_bot.ENVIO_INTERVALO_MIN}-{wa_bot.ENVIO_INTERVALO_MAX}")
check("teto por usuário/dia definido",
      1 <= wa_bot.MAX_PROATIVAS_POR_USUARIO_DIA <= 12,
      f"{wa_bot.MAX_PROATIVAS_POR_USUARIO_DIA}")

print("\n1b. Pior caso por minuto")
pior = wa_bot.DISPATCH_MAX_PER_CYCLE
minimo_s = (pior - 1) * wa_bot.ENVIO_INTERVALO_MIN
print(f"    {pior} msgs, espaçadas >= {wa_bot.ENVIO_INTERVALO_MIN:.0f}s "
      f"= no mínimo {minimo_s:.0f}s pra despejar o ciclo inteiro")
check("um ciclo cheio leva >= 30s", minimo_s >= 30, f"{minimo_s}s")
check("antes eram 60 numa tacada (agora não)", pior * 1.0 <= 8)

# ---------------------------------------------------------------------------
print("\n2. Teto por usuário/dia")
db.init_db()
uid = db.create_user(nome="Kevin", telefone="5511900009999")
db.update_user_fields(uid, onboarding_step="done", status="ativo")
check("usuário novo começa em 0", wa_bot._proativas_hoje(uid) == 0)

for i in range(wa_bot.MAX_PROATIVAS_POR_USUARIO_DIA):
    db.log_dispatch(uid, "hora", None)
n = wa_bot._proativas_hoje(uid)
check(f"conta os {wa_bot.MAX_PROATIVAS_POR_USUARIO_DIA} disparos",
      n == wa_bot.MAX_PROATIVAS_POR_USUARIO_DIA, f"{n}")
check("atingiu o teto", n >= wa_bot.MAX_PROATIVAS_POR_USUARIO_DIA)

print("\n2b. Relatório do dono e extensão de trial NÃO contam no teto")
antes = wa_bot._proativas_hoje(uid)
db.log_dispatch(uid, "admin-report", None)
db.log_dispatch(uid, "extensao-trial", None)
check("admin-report não conta", wa_bot._proativas_hoje(uid) == antes,
      f"{wa_bot._proativas_hoje(uid)} vs {antes}")

print("\n2c. Outro usuário tem cota própria")
uid2 = db.create_user(nome="Ana", telefone="5511900001010")
check("cota é por pessoa", wa_bot._proativas_hoje(uid2) == 0)

print("\n2d. Erro no banco não bloqueia envio (fail-open)")
check("devolve 0 em id inexistente", wa_bot._proativas_hoje(999999) == 0)

# ---------------------------------------------------------------------------
print("\n3. Termômetro de risco")
p = db.pulso_envio()
for campo in ("pico_por_minuto", "saidas", "entradas", "proativas",
              "razao_proativa_por_recebida", "risco"):
    check(f"expõe {campo}", campo in p, str(p))

print("\n3b. Classifica o risco pelo RITMO")
# simula a rajada real de 04/08: várias saídas no mesmo minuto
agora = tempo.agora()
with db.get_conn() as conn:
    for i in range(12):
        conn.execute("INSERT INTO msg_log (user_id,telefone,direcao,tipo,"
                     "preview,ts) VALUES (?,?,?,?,?,?)",
                     (uid, "5511900009999", "out", "texto", "x",
                      agora.strftime("%Y-%m-%d %H:%M:") + f"{i:02d}"))
p2 = db.pulso_envio()
print(f"    pico/min={p2['pico_por_minuto']} · "
      f"razao={p2['razao_proativa_por_recebida']} · risco={p2['risco']}")
check("detecta o pico de 12/min", p2["pico_por_minuto"] >= 10,
      str(p2["pico_por_minuto"]))
check("marca risco alto", "alto" in p2["risco"], p2["risco"])

print("\n3c. Sem tráfego nenhum, risco é verde")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "limpo.db")
import importlib
importlib.reload(db)
db.init_db()
check("banco limpo = verde", "ok" in db.pulso_envio()["risco"],
      db.pulso_envio()["risco"])

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
