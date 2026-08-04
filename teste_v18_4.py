"""
teste_v18_4.py — Bruto→líquido e o dono fora da métrica de engajamento.

Duas coisas que o Kevin pediu, e uma delas nasceu de um instinto certo dele:
"não faz sentido contabilizar o que eu faço".

Ele estava certo, e o erro era otimista: o engajamento contava TODAS as
mensagens que entram, e ele passou dias testando. O número dizia que havia
hábito onde só havia o fundador apertando botão.

Distinção que ficou no código:
  • RISCO DE BLOQUEIO  -> as mensagens do dono CONTAM (a Meta não sabe quem é)
  • ENGAJAMENTO        -> as mensagens do dono NÃO contam (ele não é cliente)

Rodar:  python teste_v18_4.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t184.db")
# custos de exemplo pra conferir a aritmética
# zera TUDO primeiro: o default de CUSTO_CLAUDE_MES é 100, e um teste que
# depende de default silencioso quebra na primeira vez que alguém muda o
# default — foi exatamente o que aconteceu aqui.
for _v in ("CUSTO_CLAUDE_MES", "CUSTO_OPENAI_MES", "CUSTO_WASENDER_MES",
           "CUSTO_VPS_MES", "CUSTO_DOMINIO_MES", "CUSTO_CHIP_MES",
           "CUSTO_OUTROS_MES", "CUSTO_MSG_ENVIADA"):
    os.environ[_v] = "0"
os.environ["CUSTO_VPS_MES"] = "40"
os.environ["CUSTO_DOMINIO_MES"] = "5"
os.environ["CUSTO_LLM_POR_MSG"] = "0.02"
os.environ["TAXA_PAGAMENTO_PCT"] = "5"
os.environ["IMPOSTO_PCT"] = "6"
os.environ["PRECO_MENSAL"] = "19.90"

import tempo   # noqa: E402
import db      # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


db.init_db()
HOJE = tempo.hoje().isoformat()
DONO = "5511999990000"


def novo(nome, tel, status, dias=5):
    uid = db.create_user(nome=nome, telefone=tel)
    db.update_user_fields(uid, onboarding_step="done", status=status)
    db.set_created_days_ago(uid, dias)
    return uid


dono = novo("Kevin (dono)", DONO, "ativo", 30)
ana = novo("Ana Cliente", "5511911111111", "ativo", 20)
joao = novo("Joao Cliente", "5511922222222", "trial", 3)

with db.get_conn() as c:
    # o dono testando MUITO (40 mensagens)
    for i in range(40):
        c.execute("INSERT INTO msg_log (user_id,telefone,direcao,tipo,"
                  "preview,ts) VALUES (?,?,?,?,?,?)",
                  (dono, DONO, "in", "texto", "teste",
                   f"{HOJE} 1{i % 10}:0{i % 6}:00"))
    # clientes reais usando pouco (7 + 7 = 14 em 7 dias)
    for uid, tel in ((ana, "5511911111111"), (joao, "5511922222222")):
        for i in range(7):
            c.execute("INSERT INTO msg_log (user_id,telefone,direcao,tipo,"
                      "preview,ts) VALUES (?,?,?,?,?,?)",
                      (uid, tel, "in", "texto", "conta", f"{HOJE} 09:0{i}:00"))

# ---------------------------------------------------------------------------
print("A. O dono inflava a métrica")
com = db.engajamento()
sem = db.engajamento(excluir_telefones=[DONO])
print(f"    com o dono: {com['por_pessoa_dia']}/pessoa/dia "
      f"({com['pessoas']} pessoas, {com['despejos_7d']} msgs)")
print(f"    sem o dono: {sem['por_pessoa_dia']}/pessoa/dia "
      f"({sem['pessoas']} pessoas, {sem['despejos_7d']} msgs)")
check("com o dono o número é MAIOR",
      com["por_pessoa_dia"] > sem["por_pessoa_dia"],
      f"{com['por_pessoa_dia']} vs {sem['por_pessoa_dia']}")
check("sem o dono sobram 2 pessoas", sem["pessoas"] == 2, str(sem["pessoas"]))
check("sem o dono sobram 14 msgs", sem["despejos_7d"] == 14,
      str(sem["despejos_7d"]))
check("marca que excluiu o dono", sem["dono_excluido"] is True)
check("mostra o tamanho do viés", sem["mensagens_do_dono_7d"] == 40,
      str(sem["mensagens_do_dono_7d"]))
check("dono some do ranking",
      all("dono" not in (t["nome"] or "").lower() for t in sem["top"]),
      str(sem["top"]))

print("\nA2. Sem cliente nenhum, o veredito é honesto")
vazio = db.engajamento(excluir_telefones=[DONO, "5511911111111",
                                          "5511922222222"])
check("não diz que 'não virou hábito' sem ter ninguém",
      "sem usuário real" in vazio["veredito"], vazio["veredito"])

print("\nA3. Risco de bloqueio CONTINUA contando o dono")
p = db.pulso_envio()
check("pulso não exclui ninguém", p["entradas"] >= 54, str(p["entradas"]))

# ---------------------------------------------------------------------------
print("\nB. Bruto → líquido")
f = db.financeiro(14)
print(f"    bruto  R$ {f['bruto']:.2f}")
for k, v in f["custos"].items():
    print(f"      - {k:16s} R$ {v:.2f}")
print(f"    líquido R$ {f['liquido']:.2f}")
check("2 assinantes", f["assinantes"] == 2, str(f["assinantes"]))
check("bruto = 2 × 19,90", abs(f["bruto"] - 39.80) < 0.01, str(f["bruto"]))
check("fixos = 40 + 5", abs(f["custos"]["fixos"] - 45.0) < 0.01,
      str(f["custos"]["fixos"]))
check("LLM = 54 msgs × 0,02", abs(f["custos"]["llm"] - 1.08) < 0.01,
      str(f["custos"]["llm"]))
check("taxa = 5% de 39,80", abs(f["custos"]["taxa_pagamento"] - 1.99) < 0.01,
      str(f["custos"]["taxa_pagamento"]))
check("imposto = 6% de 39,80", abs(f["custos"]["imposto"] - 2.39) < 0.01,
      str(f["custos"]["imposto"]))
esperado = round(39.80 - (45.0 + 1.08 + 0 + 1.99 + 2.39), 2)
check(f"líquido = {esperado}", abs(f["liquido"] - esperado) < 0.02,
      str(f["liquido"]))
check("líquido é NEGATIVO (custo fixo > receita)", f["liquido"] < 0,
      str(f["liquido"]))

print("\nB2. Break-even")
print(f"    empata com {f['breakeven_assinantes']} assinante(s)")
check("calcula o break-even", isinstance(f["breakeven_assinantes"], int),
      str(f["breakeven_assinantes"]))
check("break-even > assinantes atuais",
      f["breakeven_assinantes"] > f["assinantes"],
      f"{f['breakeven_assinantes']} vs {f['assinantes']}")

print("\nB3. Sem custo configurado, líquido = bruto")
for k in ("CUSTO_CLAUDE_MES", "CUSTO_OPENAI_MES", "CUSTO_WASENDER_MES",
          "CUSTO_VPS_MES", "CUSTO_DOMINIO_MES", "CUSTO_CHIP_MES",
          "CUSTO_OUTROS_MES", "CUSTO_LLM_POR_MSG", "CUSTO_MSG_ENVIADA",
          "TAXA_PAGAMENTO_PCT", "IMPOSTO_PCT"):
    os.environ[k] = "0"
import importlib          # noqa: E402
importlib.reload(db)
f0 = db.financeiro(14)
check("líquido = bruto quando custo é 0",
      abs(f0["liquido"] - f0["bruto"]) < 0.01,
      f"{f0['liquido']} vs {f0['bruto']}")

print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
