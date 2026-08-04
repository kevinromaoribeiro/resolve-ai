"""
teste_v18_5.py — Todos os custos e margem por cliente.

Pedido do Kevin: "preciso que todos os custos, exceto minhas horas, estejam na
conta, e no fim de cada mês a gente dilui custo por usuário pra ver margem por
cliente".

Custos que apareceram nas conversas e viraram linha nomeada:
  Claude (~R$100/mês) · OpenAI · WasenderAPI · VPS · domínio · chip do bot
  + variável de LLM por mensagem + envio + taxa de pagamento + imposto

A distinção que o teste protege:
  MARGEM DE CONTRIBUIÇÃO -> o que cada cliente NOVO acrescenta
  SOBRA POR CLIENTE      -> contribuição − fixo rateado
Confundir as duas leva a decisão errada: sobra negativa com contribuição
positiva é falta de VOLUME. Contribuição negativa é problema de MODELO, e aí
crescer piora.

Rodar:  python teste_v18_5.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t185.db")
os.environ["PRECO_MENSAL"] = "19.90"
os.environ["CUSTO_CLAUDE_MES"] = "100"
os.environ["CUSTO_WASENDER_MES"] = "50"
os.environ["CUSTO_VPS_MES"] = "40"
os.environ["CUSTO_DOMINIO_MES"] = "4"
os.environ["CUSTO_CHIP_MES"] = "30"
os.environ["CUSTO_OPENAI_MES"] = "0"
os.environ["CUSTO_OUTROS_MES"] = "0"
os.environ["CUSTO_LLM_POR_MSG"] = "0.02"
os.environ["CUSTO_MSG_ENVIADA"] = "0"
os.environ["TAXA_PAGAMENTO_PCT"] = "8"
os.environ["IMPOSTO_PCT"] = "6"

import tempo   # noqa: E402
import db      # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


def R(v):
    return f"R$ {v:>8.2f}"


db.init_db()
HOJE = tempo.hoje().isoformat()


def novo(nome, status):
    uid = db.create_user(nome=nome, telefone="5511" +
                         str(abs(hash(nome)) % 10**9).zfill(9))
    db.update_user_fields(uid, onboarding_step="done", status=status)
    return uid


# 10 assinantes, cada um mandando 60 mensagens/mês (uso médio)
assinantes = [novo(f"Cliente {i}", "ativo") for i in range(10)]
with db.get_conn() as c:
    for uid in assinantes:
        for i in range(60):
            c.execute("INSERT INTO msg_log (user_id,telefone,direcao,tipo,"
                      "preview,ts) VALUES (?,?,?,?,?,?)",
                      (uid, "x", "in", "texto", "x", f"{HOJE} 10:00:00"))

f = db.financeiro(14)
mg = f["margem"]

print("1. Todos os custos nomeados, nada em 'outros'")
for x in f["fixos_detalhe"]:
    print(f"    {x['nome']:22s} {R(x['valor'])}")
nomes = {x["nome"] for x in f["fixos_detalhe"]}
for esperado in ["Claude (dev)", "WasenderAPI", "Servidor (VPS)",
                 "Domínio", "Chip do bot"]:
    check(f"tem linha '{esperado}'", esperado in nomes, str(nomes))
check("soma dos fixos = 224", abs(f["custos"]["fixos"] - 224.0) < 0.01,
      str(f["custos"]["fixos"]))
check("custo zerado não polui a lista", "OpenAI (assinatura)" not in nomes)

print("\n2. Bruto → líquido")
print(f"    bruto        {R(f['bruto'])}")
print(f"    fixos        {R(-f['custos']['fixos'])}")
print(f"    IA           {R(-f['custos']['llm'])}  ({f['msgs_30d']['recebidas']} msgs)")
print(f"    taxa 8%      {R(-f['custos']['taxa_pagamento'])}")
print(f"    imposto 6%   {R(-f['custos']['imposto'])}")
print(f"    LÍQUIDO      {R(f['liquido'])}")
check("bruto = 10 × 19,90", abs(f["bruto"] - 199.0) < 0.01, str(f["bruto"]))
check("IA = 600 msgs × 0,02", abs(f["custos"]["llm"] - 12.0) < 0.01,
      str(f["custos"]["llm"]))
check("taxa = 8% de 199", abs(f["custos"]["taxa_pagamento"] - 15.92) < 0.01,
      str(f["custos"]["taxa_pagamento"]))
esperado = round(199.0 - (224.0 + 12.0 + 15.92 + 11.94), 2)
check(f"líquido = {esperado}", abs(f["liquido"] - esperado) < 0.02,
      str(f["liquido"]))

print("\n3. Margem por cliente")
print(f"    preço                  {R(mg['preco'])}")
print(f"    − taxa e imposto       {R(mg['receita_liquida_unit'])}")
print(f"    − variável dele        {R(-mg['custo_variavel_cliente'])}")
print(f"    = CONTRIBUIÇÃO         {R(mg['margem_contribuicao'])}  "
      f"({mg['margem_contribuicao_pct']}%)")
print(f"    − fixo rateado         {R(-mg['fixo_rateado'])}")
print(f"    = SOBRA POR CLIENTE    {R(mg['margem_liquida_cliente'])}")
print(f"    -> {mg['leitura']}")
check("receita líquida = 19,90 − 14%",
      abs(mg["receita_liquida_unit"] - 17.11) < 0.02,
      str(mg["receita_liquida_unit"]))
check("variável por cliente = 12/10",
      abs(mg["custo_variavel_cliente"] - 1.20) < 0.01,
      str(mg["custo_variavel_cliente"]))
check("contribuição POSITIVA", mg["margem_contribuicao"] > 0,
      str(mg["margem_contribuicao"]))
check("fixo rateado = 224/10", abs(mg["fixo_rateado"] - 22.4) < 0.01,
      str(mg["fixo_rateado"]))
check("sobra negativa (falta volume)", mg["margem_liquida_cliente"] < 0,
      str(mg["margem_liquida_cliente"]))
check("leitura diz que falta volume, não que o produto é ruim",
      "volume" in mg["leitura"], mg["leitura"])

print("\n4. Break-even bate com a contribuição")
print(f"    empata com {f['breakeven_assinantes']} assinantes")
esp_be = int(-(-224.0 // mg["margem_contribuicao"]))
check(f"break-even = {esp_be}", f["breakeven_assinantes"] == esp_be,
      str(f["breakeven_assinantes"]))
check("break-even > 10 atuais", f["breakeven_assinantes"] > 10)

print("\n5. Usuário PESADO derruba a contribuição")
os.environ["CUSTO_LLM_POR_MSG"] = "0.30"      # cenário caro
import importlib      # noqa: E402
importlib.reload(db)
f2 = db.financeiro(14)
mg2 = f2["margem"]
print(f"    a R$0,30/msg -> contribuição {R(mg2['margem_contribuicao'])}")
print(f"    -> {mg2['leitura']}")
check("contribuição vira NEGATIVA", mg2["margem_contribuicao"] < 0,
      str(mg2["margem_contribuicao"]))
check("avisa pra NÃO escalar", "não escale" in mg2["leitura"],
      mg2["leitura"])
check("sem break-even possível", f2["breakeven_assinantes"] is None,
      str(f2["breakeven_assinantes"]))

print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
