"""
teste_v17_8.py — Os dois últimos B.O.s da varredura de 03/08.

A) DESCRIÇÃO SUJA
   "preciso ir no mercado e gastar até 250,00" virou a descrição do item.
   Isso aparece no lembrete, no resumo de segunda e na confirmação — e um
   resumo semanal com frases assim vira parágrafo, não lista.

B) DUPLICATA NA MESMA MENSAGEM
   A mesma frase gerou #73 [despesa] "mercado" e #74 [lembrete] "preciso ir
   no mercado e gastar…". Duas linhas pro mesmo assunto: o usuário dá baixa
   numa e a outra continua cobrando. O `_ja_existe` não pegava porque só
   compara com o que já está no banco — estes nasceram juntos.

Rodar:  python teste_v17_8.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t178.db")

import ai_engine   # noqa: E402
import motor_v8    # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


# ---------------------------------------------------------------------------
print("A. Descrição limpa (o caso real primeiro)")
CASOS = [
    ("preciso ir no mercado e gastar até 250,00", "ir no mercado"),
    ("me lembra de levar a Rafinha no pediatra", "levar a Rafinha no pediatra"),
    ("que preciso pegar minha encomendas na farmácia",
     "pegar minha encomendas na farmácia"),
    ("anota comprar ração da Nina", "comprar ração da Nina"),
    ("tenho que pagar o condominio", "pagar o condominio"),
    ("quero marcar o dentista", "marcar o dentista"),
    ("me avisa que preciso buscar o exame", "buscar o exame"),
    ("lembrar de ligar pro contador", "ligar pro contador"),
    ("mercado, R$ 250", "mercado"),
]
for entrada, esperado in CASOS:
    got = motor_v8.limpar_descricao(entrada)
    check(f"{entrada!r} -> {esperado!r}", got == esperado, f"veio {got!r}")

print("\nA2. Não estraga descrição que já está boa")
for d in ["Cartão de débito", "IPTU", "ração da Nina", "trocar o óleo do carro",
          "buscar exame no laboratorio", "condominio"]:
    check(f"preserva {d!r}", motor_v8.limpar_descricao(d) == d,
          motor_v8.limpar_descricao(d))

print("\nA3. Nunca devolve vazio nem quebra")
check("string curta volta original", motor_v8.limpar_descricao("oi") == "oi")
check("só verbo volta original",
      len(motor_v8.limpar_descricao("preciso")) >= 3,
      motor_v8.limpar_descricao("preciso"))
check("vazio não quebra", motor_v8.limpar_descricao("") == "")
check("None não quebra", motor_v8.limpar_descricao(None) is None)

print("\nA4. Entra no _preparar_item")
it = motor_v8._preparar_item(
    {"descricao": "preciso ir no mercado e gastar até 250,00",
     "tipo": "lembrete", "valor_reais": 250.0}, ai_engine, texto_origem="")
check("descrição chega limpa no banco", it["descricao"] == "ir no mercado",
      f"{it['descricao']!r}")
check("valor preservado", it["valor_reais"] == 250.0)

# ---------------------------------------------------------------------------
print("\nB. Duplicata na mesma mensagem vira 1 item")
DOIS = [
    {"descricao": "mercado", "tipo": "despesa", "categoria": "Alimentação",
     "valor_reais": 250.0, "data_vencimento": None, "hora_alvo": None},
    {"descricao": "ir no mercado", "tipo": "lembrete",
     "categoria": "Alimentação", "valor_reais": 250.0,
     "data_vencimento": "2026-08-04", "hora_alvo": "19:00"},
]
r = motor_v8.fundir_duplicatas([dict(x) for x in DOIS])
print(f"\n    antes: {len(DOIS)} itens -> depois: {len(r)}")
for x in r:
    print(f"    {x['tipo']}/{x['categoria']} \"{x['descricao']}\" "
          f"val={x['valor_reais']} venc={x['data_vencimento']} "
          f"hora={x['hora_alvo']}")
check("virou 1 item", len(r) == 1, f"{len(r)}")
check("herdou a data", r[0]["data_vencimento"] == "2026-08-04")
check("herdou a hora", r[0]["hora_alvo"] == "19:00")
check("manteve o valor", r[0]["valor_reais"] == 250.0)
check("virou despesa (quem tem valor)", r[0]["tipo"] == "despesa",
      r[0]["tipo"])
check("ficou a descrição mais curta", r[0]["descricao"] == "mercado",
      r[0]["descricao"])

print("\nB2. Assuntos DIFERENTES continuam separados")
DIF = [
    {"descricao": "comprar ração da Nina", "tipo": "lembrete",
     "categoria": "Pet", "valor_reais": None,
     "data_vencimento": "2026-08-07", "hora_alvo": None},
    {"descricao": "trocar o óleo do carro", "tipo": "lembrete",
     "categoria": "Veículo", "valor_reais": None,
     "data_vencimento": "2027-02-02", "hora_alvo": None},
]
r2 = motor_v8.fundir_duplicatas([dict(x) for x in DIF])
check("2 assuntos = 2 itens", len(r2) == 2, f"{len(r2)}")
check("ração mantém a data", r2[0]["data_vencimento"] == "2026-08-07")
check("óleo mantém a data", r2[1]["data_vencimento"] == "2027-02-02")

print("\nB3. Casos de borda")
check("1 item passa intacto",
      len(motor_v8.fundir_duplicatas([dict(DOIS[0])])) == 1)
check("lista vazia não quebra", motor_v8.fundir_duplicatas([]) == [])
check("3 iguais viram 1",
      len(motor_v8.fundir_duplicatas([dict(DOIS[0]) for _ in range(3)])) == 1)
check("descrição vazia não funde",
      len(motor_v8.fundir_duplicatas(
          [{"descricao": "", "tipo": "lembrete"},
           {"descricao": "", "tipo": "lembrete"}])) == 2)

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
