"""
teste_v16_7.py — "em N meses": o buraco que o teste ao vivo achou.

Mensagem real (03/08/2026, 10:59):
    "anota o boleto do IPTU dia 15 de 320 reais e me lembra de levar a
     Rafinha no pediatra em 8 meses"

Banco depois:
    #66 [despesa/Contas] "IPTU" val=320 venc=2026-08-15    <- perfeito
    (pediatra não existe)                                   <- sumiu

Resposta ao usuário:
    "Anotado. Você me disse que o IPTU vence 15/08 e é R$ 320,00.
     Qual a data do pediatra?"                              <- ele já disse

Causa: extract_due_date conhecia "em N dias" e "em N semanas", nunca
"em N meses". Consulta, revisão e garantia são exatamente o que se marca
em meses — é o caso de uso central do produto.

Rodar:  python teste_v16_7.py
"""
import os
import sys
import tempfile
from datetime import date

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t167.db")

import tempo        # noqa: E402
import ai_engine    # noqa: E402
import motor_v8     # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


REF = date(2026, 8, 3)   # a segunda-feira do teste real

# ---------------------------------------------------------------------------
print("1. 'em N meses' / 'em N anos' agora viram data")
CASOS = [
    ("me lembra do pediatra em 8 meses",        "2027-04-03"),
    ("revisão do carro em 6 meses",             "2027-02-03"),
    ("daqui a 3 meses",                         "2026-11-03"),
    ("daqui 1 mes",                             "2026-09-03"),
    ("dentro de 12 meses",                      "2027-08-03"),
    ("garantia vence em 2 anos",                "2028-08-03"),
    ("em 1 ano",                                "2027-08-03"),
    ("ano que vem",                             "2027-08-03"),
]
for texto, esperado in CASOS:
    got = ai_engine.extract_due_date(texto, ref=REF)
    check(f"{texto!r} -> {esperado}", got == esperado, f"veio {got}")

print("\n1b. Não quebra o que já funcionava")
JA_FUNCIONAVA = [
    ("em 3 dias",        "2026-08-06"),
    ("em 2 semanas",     "2026-08-17"),
    ("amanhã",           "2026-08-04"),
    ("depois de amanhã", "2026-08-05"),
    ("dia 15",           "2026-08-15"),
    ("semana que vem",   "2026-08-10"),
    ("sexta",            "2026-08-07"),
]
for texto, esperado in JA_FUNCIONAVA:
    got = ai_engine.extract_due_date(texto, ref=REF)
    check(f"{texto!r} -> {esperado}", got == esperado, f"veio {got}")

print("\n1c. Fim de mês não estoura (31/01 + 1 mês = 28/02)")
check("31/01/2026 + 1 mês -> 28/02/2026",
      ai_engine._somar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28),
      str(ai_engine._somar_meses(date(2026, 1, 31), 1)))
check("31/03 + 1 mês -> 30/04",
      ai_engine._somar_meses(date(2026, 3, 31), 1) == date(2026, 4, 30))
check("bissexto: 31/01/2028 + 1 mês -> 29/02/2028",
      ai_engine._somar_meses(date(2028, 1, 31), 1) == date(2028, 2, 29))
check("vira o ano: 15/11 + 3 meses -> 15/02 do ano seguinte",
      ai_engine._somar_meses(date(2026, 11, 15), 3) == date(2027, 2, 15))

print("\n1d. Fuso: usa tempo.hoje(), não date.today() (servidor em UTC)")
check("sem ref explícito parte de hoje-BR",
      ai_engine.extract_due_date("amanhã")
      == (tempo.hoje().replace(day=tempo.hoje().day)).replace(
          day=tempo.hoje().day).isoformat().replace(
          tempo.hoje().isoformat(),
          (tempo.hoje().toordinal() + 1 and
           date.fromordinal(tempo.hoje().toordinal() + 1).isoformat())),
      ai_engine.extract_due_date("amanhã"))

# ---------------------------------------------------------------------------
print("\n2. O item do pediatra nasce COM data, mesmo se o LLM omitir")
item = motor_v8._preparar_item(
    {"descricao": "levar a Rafinha no pediatra", "tipo": "lembrete"},
    ai_engine, texto_origem="me lembra de levar a Rafinha no pediatra em 8 meses")
check("data preenchida em Python", bool(item.get("data_vencimento")),
      f"veio {item.get('data_vencimento')!r}")
check("categoria Saúde", item["categoria"] == "Saúde",
      f"veio {item['categoria']!r}")

print("\n2b. Data do LLM tem prioridade — não sobrescrevo")
item2 = motor_v8._preparar_item(
    {"descricao": "pediatra", "tipo": "lembrete",
     "data_vencimento": "2027-04-10"},
    ai_engine, texto_origem="pediatra em 8 meses")
check("respeita a data que veio do LLM",
      item2["data_vencimento"] == "2027-04-10",
      f"veio {item2['data_vencimento']!r}")

print("\n2c. Frase COMPOSTA não vaza a data do vizinho")
FRASE = ("anota o boleto do IPTU dia 15 de 320 reais e me lembra de levar a "
         "Rafinha no pediatra em 8 meses")
iptu = motor_v8._preparar_item(
    {"descricao": "IPTU", "tipo": "despesa", "valor_reais": 320.0,
     "data_vencimento": "2026-08-15"}, ai_engine, texto_origem=FRASE)
check("IPTU mantém 15/08", iptu["data_vencimento"] == "2026-08-15",
      f"veio {iptu['data_vencimento']!r}")
check("IPTU em Contas", iptu["categoria"] == "Contas",
      f"veio {iptu['categoria']!r}")

pedi = motor_v8._preparar_item(
    {"descricao": "levar a Rafinha no pediatra em 8 meses", "tipo": "lembrete"},
    ai_engine, texto_origem=FRASE)
check("pediatra pega os 8 meses da PRÓPRIA descrição",
      bool(pedi.get("data_vencimento")), f"veio {pedi.get('data_vencimento')!r}")

print("\n2d. Item sem nenhuma data continua sem data (não invento)")
vazio = motor_v8._preparar_item(
    {"descricao": "ligar pro Fernando", "tipo": "lembrete"},
    ai_engine, texto_origem="me lembra de ligar pro Fernando")
check("sem sinal de data -> None", vazio.get("data_vencimento") is None,
      f"veio {vazio.get('data_vencimento')!r}")

# ---------------------------------------------------------------------------
print("\n3. _polir_resposta: a faxina roda nos DOIS caminhos (v16.8)")
PED = [{"descricao": "levar a Rafinha no pediatra",
        "data_vencimento": "2027-04-03", "valor_reais": None,
        "hora_alvo": None, "tipo": "lembrete", "categoria": "Saúde"}]
REAL = "Anotado. Qual a data do pediatra?"   # o que saiu em prod às 11:04
pol = motor_v8._polir_resposta(REAL, PED)
print(f'\n--- prod 11:04 ---\n{REAL}\n\n--- agora ---\n{pol}\n---\n')
check("some com a pergunta que ele já respondeu",
      "Qual a data" not in pol, pol)
check("passa a DIZER a data", "03/04/2027" in pol, pol)
check("diz o que guardou", "pediatra" in pol, pol)
check("usa 'Guardei:' e não 'Guardei também:'",
      "Guardei:" in pol and "também" not in pol, pol)

print("\n3b. Resposta que já diz tudo não ganha bloco")
BOA = "Anotado! Te aviso do *pediatra* em 03/04/2027. 👍"
check("resposta completa passa intacta",
      motor_v8._polir_resposta(BOA, PED) == BOA,
      motor_v8._polir_resposta(BOA, PED))

print("\n3c. Sem itens, não inventa bloco")
check("conversa pura passa intacta",
      motor_v8._polir_resposta("Tô aqui, Kevin. 🤝", []) == "Tô aqui, Kevin. 🤝")
check("reply vazio não quebra", motor_v8._polir_resposta("", PED) == "")

print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
