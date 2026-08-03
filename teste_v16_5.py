"""
teste_v16_5.py — Bugs achados no teste AO VIVO no WhatsApp (03/08/2026, 10:45).

Mensagem enviada:
    "comprar ração da Nina sexta e trocar o óleo do carro em 5000 km ou 6 meses"

O que o banco mostrou:
    #64 [lembrete/Pet] "comprar ração da Nina"   venc=2026-08-05
    #65 [lembrete/Pet] "trocar o óleo do carro"  venc=2027-02-02   <-- Pet?!

O que o usuário viu:
    "Anotado a troca de hoje. Te aviso em 02/02 — 6 meses."        <-- e a ração?

Dois defeitos:
  A) item herdou a categoria do vizinho da mesma frase (óleo -> Pet)
  B) gravou 2, confirmou 1 — o usuário não tem como saber que a ração está lá

Rodar:  python teste_v16_5.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t165.db")

import ai_engine     # noqa: E402
import motor_v8      # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


FRASE_REAL = "comprar ração da Nina sexta e trocar o óleo do carro em 5000 km ou 6 meses"

# ---------------------------------------------------------------------------
print("A. Item não herda mais a categoria do vizinho da mesma frase")
i1 = motor_v8._preparar_item({"descricao": "comprar ração da Nina",
                              "tipo": "lembrete"},
                             ai_engine, texto_origem=FRASE_REAL)
i2 = motor_v8._preparar_item({"descricao": "trocar o óleo do carro",
                              "tipo": "lembrete"},
                             ai_engine, texto_origem=FRASE_REAL)
check("ração -> Pet", i1["categoria"] == "Pet", f"veio {i1['categoria']!r}")
check("óleo do carro -> Veículo", i2["categoria"] == "Veículo",
      f"veio {i2['categoria']!r}  (era o bug: vinha 'Pet')")

print("\nA2. Frase simples ainda usa o contexto quando a descrição é muda")
i3 = motor_v8._preparar_item({"descricao": "Cartão de débito",
                              "tipo": "lembrete"},
                             ai_engine, texto_origem="me lembra do cartão de débito dia 20")
check("Cartão de débito -> Contas", i3["categoria"] == "Contas",
      f"veio {i3['categoria']!r}")

i4 = motor_v8._preparar_item({"descricao": "aquilo", "tipo": "lembrete"},
                             ai_engine,
                             texto_origem="me lembra daquilo do veterinário")
check("descrição muda + frase simples usa contexto",
      i4["categoria"] == "Pet", f"veio {i4['categoria']!r}")

i5 = motor_v8._preparar_item({"descricao": "aquilo", "tipo": "lembrete"},
                             ai_engine,
                             texto_origem="marca o veterinário e paga a luz")
check("descrição muda + frase COMPOSTA não chuta",
      i5["categoria"] == "Outros", f"veio {i5['categoria']!r}")

# ---------------------------------------------------------------------------
print("\nB. Gravou 2, confirmou 1 -> a resposta completa sozinha")
ITENS = [
    {"descricao": "comprar ração da Nina", "valor_reais": None,
     "data_vencimento": "2026-08-05", "hora_alvo": None, "tipo": "lembrete",
     "categoria": "Pet", "status": "pendente"},
    {"descricao": "trocar o óleo do carro", "valor_reais": None,
     "data_vencimento": "2027-02-02", "hora_alvo": None, "tipo": "lembrete",
     "categoria": "Veículo", "status": "pendente"},
]
REPLY_RUIM = "Anotado a troca de hoje.\n\n*Te aviso em 02/02* — 6 meses."
corrigida = motor_v8._confirmar_todos_os_itens(REPLY_RUIM, ITENS)
print("\n--- o que o usuário viu (10:46) ---\n" + REPLY_RUIM
      + "\n\n--- o que vai ver agora ---\n" + corrigida + "\n---\n")
check("cita a ração que estava faltando",
      "ração" in corrigida or "racao" in corrigida.lower(), corrigida)
check("mantém o texto original", "Anotado a troca" in corrigida, corrigida)
check("NÃO repete o óleo (a resposta já dizia 'troca')",
      "óleo" not in corrigida.lower(), corrigida)
check("lista só o que faltava (1 linha)",
      corrigida.count("\n• ") == 1, corrigida)

print("\nB2. Não mexe quando a resposta já citou tudo")
REPLY_BOM = ("Anotei os dois: a *ração da Nina* na sexta e a *troca de óleo* "
             "em fevereiro.")
check("resposta completa passa intacta",
      motor_v8._confirmar_todos_os_itens(REPLY_BOM, ITENS) == REPLY_BOM)

print("\nB3. Item único TAMBÉM é confirmado (mudou na v16.8)")
# Antes só agia com 2+ itens. Mas "Anotado." sozinho não diz o quê nem quando
# — e é exatamente o que sobra depois que o corte de pergunta redundante
# limpa "Anotado. Qual a data do pediatra?".
uni = motor_v8._confirmar_todos_os_itens("Anotado.", ITENS[:1])
check("1 item não citado é listado", "ração" in uni or "racao" in uni.lower(),
      uni)
check("usa 'Guardei:' (nada foi citado antes)", "Guardei:" in uni, uni)
check("1 item JÁ citado não vira lista",
      motor_v8._confirmar_todos_os_itens(
          "Anotei a *ração da Nina* pra sexta.", ITENS[:1])
      == "Anotei a *ração da Nina* pra sexta.")
check("0 item não quebra",
      motor_v8._confirmar_todos_os_itens("Oi!", []) == "Oi!")

print("\nB4. Três itens, dois esquecidos")
TRES = ITENS + [{"descricao": "pagar o IPTU", "valor_reais": 320.0,
                 "data_vencimento": "2026-08-10", "hora_alvo": None,
                 "tipo": "despesa", "categoria": "Contas",
                 "status": "pendente"}]
c3 = motor_v8._confirmar_todos_os_itens("Anotei a troca de óleo.", TRES)
check("cita os dois esquecidos",
      ("ração" in c3 or "racao" in c3.lower()) and "IPTU" in c3, c3)
check("mostra o valor do que tem valor", "320" in c3, c3)

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
