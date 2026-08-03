"""
teste_prompt_guarda.py — TRAVA CONTRA REGRESSÃO DE PROMPT.

Por que este arquivo existe (custou caro, 03/08/2026 às 14:17):
Eu reescrevi a persona do _V8_SYSTEM para tirar a palavra "mordomo" e escrevi
"Você NÃO faz as coisas pela pessoa. Você não paga, não compra, não resolve."

O modelo leu "não resolve" como "não aja" e PAROU DE CRIAR ITENS. A mensagem
"me lembra amanhã às 9 de levar o carro na revisão" — a coisa mais simples que
o produto faz — devolveu "Não consegui guardar isso direito aqui".

Nenhum teste pegou, porque todos os outros testam FUNÇÕES Python. O prompt não
tinha guarda nenhuma. Este arquivo é essa guarda: ele não chama o LLM, ele
audita o TEXTO do prompt procurando instruções que desligam o registro.

Rodar:  python teste_prompt_guarda.py
"""
import os
import re
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "tpg.db")

import motor_v8   # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


P = motor_v8._V8_SYSTEM
Pl = P.lower()

# A auditoria roda sobre o prompt SEM o parágrafo de ATENÇÃO — aquele bloco
# CITA a frase proibida de propósito, pra documentar o incidente. Auditar o
# próprio aviso é como o alarme de incêndio disparar com a foto do incêndio.
_MARCA = "atenção — erro já cometido aqui"
_i = Pl.find(_MARCA)
Pl_audit = Pl[:_i] if _i > 0 else Pl

# ---------------------------------------------------------------------------
print("1. O prompt MANDA registrar, e manda alto")
check("contém instrução de registrar",
      "registrar" in Pl or "registre" in Pl, "sem ordem de registro!")
check("cita o campo 'itens'", '"itens"' in P or "'itens'" in P)
check("diz que registrar é obrigatório/sempre",
      any(p in Pl for p in ("sempre", "obrigat")), "registro sem reforço")

# ---------------------------------------------------------------------------
print("\n2. Nenhuma negação genérica que desligue a ação")
# Estas frases já quebraram o produto. Não podem voltar.
# Só frases que DESLIGAM a ação de forma genérica. "não crie item novo" NÃO
# entra: é instrução legítima do caminho de atualização (evita duplicata).
# Um teste que acusa instrução boa vira ruído, e teste ruidoso é desligado.
PROIBIDAS = [
    r"n[ãa]o\s+resolve\b",
    r"n[ãa]o\s+faz\s+nada",
    r"n[ãa]o\s+faz\s+as\s+coisas",
    r"n[ãa]o\s+age\b",
    r"n[ãa]o\s+execute\b",
    r"n[ãa]o\s+registre\b",
    r"n[ãa]o\s+crie\s+nenhum\s+item",
    r"n[ãa]o\s+salve\b",
    r"n[ãa]o\s+guarde\b",
    r"nunca\s+registre\b",
    r"apenas\s+converse\b",
    r"s[óo]\s+converse\b",
]
for pat in PROIBIDAS:
    m = re.search(pat, Pl_audit)
    check(f"sem {pat!r}", m is None,
          f"ACHOU: ...{Pl_audit[max(0,m.start()-45):m.end()+45]}..." if m else "")

# ---------------------------------------------------------------------------
print("\n3. O guardrail do produto continua no lugar")
# É legítimo (e é a promessa inegociável) dizer que não paga/compra/transfere.
for termo in ["paga", "compra", "transfere"]:
    check(f"guardrail '{termo}' presente", termo in Pl,
          "o guardrail do produto sumiu do prompt")

print("\n3b. A ordem importa: 'sempre registrar' vem ANTES do 'não faz'")
i_reg = Pl.find("registrar")
i_nao = Pl.find("não paga")
check("instrução de registrar aparece primeiro",
      i_reg >= 0 and (i_nao < 0 or i_reg < i_nao),
      f"registrar em {i_reg}, 'não paga' em {i_nao}")

# ---------------------------------------------------------------------------
print("\n4. O aviso que documenta o incidente continua no arquivo")
check("comentário do incidente preservado",
      "REGISTRAR É OBRIGATÓRIO" in P,
      "alguem removeu o aviso que impede o erro de voltar")

# ---------------------------------------------------------------------------
print("\n5. Prompt da busca web também não desliga a ação")
src = open(motor_v8.__file__, encoding="utf-8").read()
i = src.find("_responder_com_busca")
trecho = src[i:i+3000].lower() if i > 0 else ""
check("busca não diz 'não faz nada'",
      "não faz nada" not in trecho and "nao faz nada" not in trecho, trecho[:80])

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
