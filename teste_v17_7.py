"""
teste_v17_7.py — A alucinação de assunto (03/08, 15:48).

    Kevin: "Amanhã preciso ir no mercado me lembra"
    bot:   "Guardei: • ir no mercado · 04/08 — Que horas te aviso?"
    Kevin: "Manhã"
    bot:   "Te aviso às 08:00 pra COMPRAR FRUTAS."     <- item #62, antigo
    bot:   "Qual o tipo de fruta que você quer comprar?"
    Kevin: "Ué, mamão"

O LLM recebe a lista de itens abertos no prompt. Quando a resposta do usuário
é curta ("Manhã"), ele reancora no item mais parecido em vez do item da
conversa — e conduz um diálogo inteiro em cima do item errado.

Rodar:  python teste_v17_7.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t177.db")

import motor_v8   # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


ABERTOS = [
    {"id": 62, "descricao": "comprar frutas", "status": "pendente"},
    {"id": 79, "descricao": "ir no mercado", "status": "pendente"},
    {"id": 63, "descricao": "Cartão de débito", "status": "pendente"},
]

# ---------------------------------------------------------------------------
print("1. A resposta não pode falar de outro lembrete")
REAL = "Te aviso às *08:00* pra comprar frutas."
r = motor_v8._nao_trocar_de_assunto(REAL, "ir no mercado", ABERTOS)
print(f'\n--- o que voce recebeu ---\n{REAL}\n\n--- agora ---\n{r}\n---\n')
check("some com 'comprar frutas'", "frutas" not in r.lower(), r)
check("fala do mercado", "mercado" in r.lower(), r)

print("\n1b. Se cita o item CERTO, passa intacta")
BOA = "Te aviso às *08:00* pra ir no mercado."
check("resposta certa não é tocada",
      motor_v8._nao_trocar_de_assunto(BOA, "ir no mercado", ABERTOS) == BOA)

print("\n1c. Cita os DOIS: vale, porque citou o certo")
DOIS = "Te aviso às 08:00 pra ir no mercado (e as frutas continuam pendentes)."
check("não mexe quando o certo está lá",
      motor_v8._nao_trocar_de_assunto(DOIS, "ir no mercado", ABERTOS) == DOIS)

print("\n1d. Resposta genérica (não cita ninguém) passa")
GEN = "Anotado ✅ Te aviso às 08:00."
check("genérica passa",
      motor_v8._nao_trocar_de_assunto(GEN, "ir no mercado", ABERTOS) == GEN)

print("\n1e. Sem alvo definido, não age no escuro")
check("sem alvo não mexe",
      motor_v8._nao_trocar_de_assunto(REAL, "", ABERTOS) == REAL)
check("sem lista não mexe",
      motor_v8._nao_trocar_de_assunto(REAL, "ir no mercado", []) == REAL)

# ---------------------------------------------------------------------------
print("\n2. _assunto_da_vez identifica o item da conversa")
check("1 item novo -> é ele",
      motor_v8._assunto_da_vez({"items": [{"descricao": "ir no mercado"}]},
                               ABERTOS) == "ir no mercado")
check("2 itens novos -> não arrisca",
      motor_v8._assunto_da_vez(
          {"items": [{"descricao": "a"}, {"descricao": "b"}]}, ABERTOS) == "")
check("atualização -> pega pelo id",
      motor_v8._assunto_da_vez({"items": [], "atualizar": {"id": 79}},
                               ABERTOS) == "ir no mercado")
check("id desconhecido -> vazio",
      motor_v8._assunto_da_vez({"items": [], "atualizar": {"id": 999}},
                               ABERTOS) == "")
check("nada -> vazio", motor_v8._assunto_da_vez({}, ABERTOS) == "")

# ---------------------------------------------------------------------------
print("\n3. Fluxo completo pelo _polir_resposta")
ITEM = [{"descricao": "ir no mercado", "tipo": "lembrete",
         "categoria": "Alimentação", "valor_reais": None,
         "data_vencimento": "2026-08-04", "hora_alvo": "08:00"}]
out = motor_v8._polir_resposta(REAL, ITEM, "", "Manhã",
                               "ir no mercado", ABERTOS)
check("fluxo corrige o assunto", "frutas" not in out.lower(), out)
check("fluxo mantém o item certo", "mercado" in out.lower(), out)

print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
