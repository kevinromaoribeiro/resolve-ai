"""
teste_v17_2.py — A conversa real do Kevin em 03/08 às 14:08.

    14:08 Kevin: "Me lembra hoje as 14 30 que preciso pegar minha
                  encomendas na farmácia"
    14:08 bot:   "Anotado. Qual o valor da encomenda?"
    14:09 Kevin: "Nao tem valor, é um lembrete apenas"
    14:09 bot:   "Anotado. Qual o valor da encomenda?"   <-- idêntico
    14:09 Kevin: "0"

No banco o item estava PERFEITO:
    #68 [lembrete/Saúde] "pegar encomendas na farmácia" venc=hoje hora=14:30

Dois defeitos, nenhum deles no dado:
  A) pediu VALOR de um lembrete (lembrete não tem preço)
  B) REPETIU a mesma pergunta depois de o usuário responder

Rodar:  python teste_v17_2.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t172.db")

import tempo       # noqa: E402
import motor_v8    # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


HOJE = tempo.hoje().isoformat()
ENCOMENDA = [{"descricao": "pegar encomendas na farmácia", "tipo": "lembrete",
              "categoria": "Saúde", "valor_reais": None,
              "data_vencimento": HOJE, "hora_alvo": "14:30"}]

# ---------------------------------------------------------------------------
print("A. Lembrete NÃO leva pergunta de valor")
REAL = "Anotado. Qual o valor da encomenda?"
r = motor_v8._polir_resposta(REAL, ENCOMENDA)
print(f'\n--- o que voce recebeu ---\n{REAL}\n\n--- o que vai receber ---\n{r}\n---\n')
check("some com a pergunta de valor", "valor" not in r.lower(), r)
check("mantém a confirmação", "notado" in r.lower(), r)
check("não sobra conector solto",
      not r.rstrip().endswith(("e", "E", ",", ";", "-", ":")), repr(r))

print("\nA2. Variações da mesma pergunta")
for p in ["Anotado. Qual o valor?", "Anotado! Quanto custou?",
          "Anotado. Qual o preço da encomenda?",
          "Anotado. E o valor da encomenda?"]:
    out = motor_v8._polir_resposta(p, ENCOMENDA)
    check(f"corta {p!r}",
          "valor" not in out.lower() and "quanto" not in out.lower()
          and "preço" not in out.lower(), out)

print("\nA3. DESPESA sem valor AINDA pode ser perguntada (aí faz sentido)")
DESPESA = [{"descricao": "mercado", "tipo": "despesa", "categoria": "Alimentação",
            "valor_reais": None, "data_vencimento": HOJE, "hora_alvo": None}]
out = motor_v8._polir_resposta("Anotado. Qual o valor?", DESPESA)
check("despesa mantém a pergunta de valor", "valor" in out.lower(), out)

print("\nA4. Pergunta de DATA num lembrete sem data continua valendo")
SEM_DATA = [{"descricao": "ligar pro contador", "tipo": "lembrete",
             "categoria": "Outros", "valor_reais": None,
             "data_vencimento": None, "hora_alvo": None}]
out = motor_v8._polir_resposta("Anotado. Quando você quer que eu te lembre?",
                               SEM_DATA)
check("pergunta de data preservada", "quando" in out.lower(), out)

# ---------------------------------------------------------------------------
print("\nB. Nunca repetir a MESMA pergunta")
ULTIMA = "Anotado. Qual o valor da encomenda?"
r2 = motor_v8._polir_resposta(ULTIMA, ENCOMENDA, ULTIMA)
print(f'\n--- 2a vez (14:09) ---\n{r2}\n---\n')
check("não repete", not motor_v8._mesma_pergunta(r2, ULTIMA), r2)
check("confirma o que tem", "farmácia" in r2 or "farmacia" in r2.lower(), r2)
check("mostra a hora salva", "14:30" in r2, r2)

print("\nB2. _mesma_pergunta é preciso")
check("idêntico", motor_v8._mesma_pergunta("Qual o valor?", "Qual o valor?"))
check("ignora acento/pontuação",
      motor_v8._mesma_pergunta("Anotado. Qual o valor da encomenda?",
                               "anotado qual o valor da encomenda"))
check("frases diferentes NÃO batem",
      not motor_v8._mesma_pergunta("Qual o valor?", "Quando você quer?"))
check("curtinha não dá falso positivo",
      not motor_v8._mesma_pergunta("Ok", "Okay, e o resto?"))
check("vazio não quebra", not motor_v8._mesma_pergunta("", "Qual o valor?"))

print("\nB3. Resposta diferente passa intacta")
BOA = "Anotado ✅ Te aviso hoje às 14:30 pra pegar a encomenda."
check("não mexe em resposta boa",
      motor_v8._polir_resposta(BOA, ENCOMENDA, ULTIMA) == BOA,
      motor_v8._polir_resposta(BOA, ENCOMENDA, ULTIMA))

print("\nB4. Confirmação seca")
check("1 item", "farmácia" in motor_v8._confirmacao_seca(ENCOMENDA))
check("sem itens não quebra", "notado" in motor_v8._confirmacao_seca([]).lower())
tres = ENCOMENDA * 3
check("3 itens viram lista", motor_v8._confirmacao_seca(tres).count("\n• ") == 3,
      motor_v8._confirmacao_seca(tres))

# ---------------------------------------------------------------------------
print("\nC. INTERPRETAR 'não tem valor' como resposta (o pedido do Kevin)")
RECUSAS = [
    "Nao tem valor, é um lembrete apenas",
    "não tem valor",
    "sem valor",
    "é só um lembrete",
    "não é uma despesa",
    "deixa em branco",
    "não se aplica",
    "sem data",
    "tanto faz",
]
for t in RECUSAS:
    check(f"entende {t!r} como recusa", motor_v8.usuario_recusou_campo(t), t)

NAO_RECUSAS = [
    "187,50",
    "o valor é 90 reais",
    "vence dia 20",
    "pode ser amanhã às 9h",
    "quero anotar mais uma coisa",
]
for t in NAO_RECUSAS:
    check(f"NÃO confunde {t!r}", not motor_v8.usuario_recusou_campo(t), t)

print("\nC2. Lembrete: a pergunta já morre antes, nem chega a insistir")
r3 = motor_v8._polir_resposta("Anotado. Qual o valor da encomenda?", ENCOMENDA,
                              "", "Nao tem valor, é um lembrete apenas")
print(f'\n--- resposta ao "nao tem valor" ---\n{r3}\n---\n')
check("não pergunta de novo", "?" not in r3, r3)
check("confirma o item", "farmácia" in r3 or "farmacia" in r3.lower(), r3)
check("mostra data e hora", "14:30" in r3, r3)

print("\nC2b. DESPESA: aí a pergunta é legítima — mas 'não tem' encerra")
# despesa sem valor: perguntar é certo. Só que se a pessoa já disse que não
# tem, insistir é o loop que o Kevin viveu.
r3b = motor_v8._polir_resposta("Anotado. Qual o valor?", DESPESA, "",
                               "não tem valor, deixa em branco")
print(f'\n--- despesa + "nao tem valor" ---\n{r3b}\n---\n')
check("para de perguntar", "?" not in r3b, r3b)
check("confirma o que tem", "mercado" in r3b.lower(), r3b)
check("abre porta pra completar depois", "depois" in r3b.lower(), r3b)

print("\nC2c. DESPESA sem recusa: continua perguntando (correto)")
r3c = motor_v8._polir_resposta("Anotado. Qual o valor?", DESPESA, "",
                               "comprei no mercado")
check("mantém a pergunta legítima", "?" in r3c, r3c)

print("\nC3. Resposta COM valor segue o fluxo normal")
r4 = motor_v8._polir_resposta("Anotado. Qual o valor?", ENCOMENDA, "", "187,50")
check("não vira confirmação seca", "completar depois" not in r4.lower(), r4)

print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
