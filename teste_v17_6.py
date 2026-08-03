"""
teste_v17_6.py — A rodada de testes do Kevin (03/08, 15:11–15:26).

Evidência do banco depois dos testes dele:

  #76 [lembrete/Saúde] "marcar médico"  venc=NULL  hora=NULL
      Ele mandou: "Preciso marcar médico, me lembra daqui 10min"
      O bot respondeu: "Anotado. Vou te lembrar em 10 minutos."
      -> PROMETEU E NÃO IA TOCAR NUNCA. Pecado capital do produto.

  #77 "assistir ao curso na Escom"  venc=NULL  hora=15:25
      -> hora órfã: o disparo depende de data_vencimento, então nunca toca.

Dois furos, mesma raiz: eu tinha posto o cálculo de DATA em Python e esquecido
a HORA. O extract_due_time já resolvia "daqui 10min" — ninguém o chamava.

Rodar:  python teste_v17_6.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t176.db")

import tempo        # noqa: E402
import ai_engine    # noqa: E402
import motor_v8     # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


HOJE = tempo.hoje().isoformat()

# ---------------------------------------------------------------------------
print("1. 'me lembra daqui 10min' TEM que virar hora no banco")
it = motor_v8._preparar_item(
    {"descricao": "marcar médico", "tipo": "lembrete"},
    ai_engine, texto_origem="Preciso marcar médico, me lembra daqui 10min")
print(f"    -> hora={it.get('hora_alvo')!r} data={it.get('data_vencimento')!r}")
check("hora preenchida", bool(it.get("hora_alvo")), f"{it.get('hora_alvo')!r}")
check("data ancorada (senão não toca)", bool(it.get("data_vencimento")),
      f"{it.get('data_vencimento')!r}")
check("categoria Saúde", it["categoria"] == "Saúde", it["categoria"])

print("\n1b. Outras formas de dizer a mesma coisa")
for frase, campo in [
    ("me lembra daqui 30 minutos de ligar", "hora_alvo"),
    ("me lembra em 2 horas", "hora_alvo"),
    ("me lembra às 18h", "hora_alvo"),
    ("me lembra hoje as 14 30", "hora_alvo"),
    ("me lembra amanhã às 9", "hora_alvo"),
]:
    x = motor_v8._preparar_item({"descricao": frase, "tipo": "lembrete"},
                                ai_engine, texto_origem=frase)
    check(f"{frase!r} -> hora", bool(x.get(campo)), f"{x.get(campo)!r}")

print("\n1c. Hora sem data ganha data (o caso do #77)")
x = motor_v8._preparar_item(
    {"descricao": "assistir ao curso na Escom", "tipo": "lembrete",
     "hora_alvo": "15:25"}, ai_engine, texto_origem="")
check("ancorou a data", bool(x.get("data_vencimento")),
      f"{x.get('data_vencimento')!r}")
check("manteve a hora", x.get("hora_alvo") == "15:25", f"{x.get('hora_alvo')!r}")

print("\n1d. Item legitimamente sem quando continua sem quando")
y = motor_v8._preparar_item(
    {"descricao": "pesquisar sobre plano de saude", "tipo": "lembrete"},
    ai_engine, texto_origem="quero pesquisar sobre plano de saude")
check("não inventa hora", y.get("hora_alvo") is None, f"{y.get('hora_alvo')!r}")

# ---------------------------------------------------------------------------
print("\n2. NUNCA prometer aviso que o banco não sustenta")
SEM_QUANDO = [{"descricao": "marcar médico", "tipo": "lembrete",
               "categoria": "Saúde", "valor_reais": None,
               "data_vencimento": None, "hora_alvo": None}]
REAL = "Anotado. Vou te lembrar em 10 minutos."
r = motor_v8._polir_resposta(REAL, SEM_QUANDO)
print(f'\n--- o que voce recebeu (15:16) ---\n{REAL}\n\n--- agora ---\n{r}\n---\n')
check("não promete mais", "vou te lembrar em 10 minutos" not in r.lower(), r)
check("assume que não vai avisar", "não" in r.lower() and "avisar" in r.lower(), r)
check("confirma o que guardou", "marcar médico" in r, r)
check("pede o quando", "hoje às" in r or "daqui" in r, r)

print("\n2b. Com o item COMPLETO, a promessa fica")
COM_QUANDO = [{"descricao": "marcar médico", "tipo": "lembrete",
               "categoria": "Saúde", "valor_reais": None,
               "data_vencimento": HOJE, "hora_alvo": "15:26"}]
ok = motor_v8._polir_resposta("Anotado. Vou te lembrar às 15:26.", COM_QUANDO)
check("promessa com lastro passa", "15:26" in ok, ok)
check("não vira pedido de horário", "me diz o quando" not in ok.lower(), ok)

print("\n2c. Basta UM item com quando pra promessa ter lastro")
MIX = SEM_QUANDO + COM_QUANDO
m = motor_v8._polir_resposta("Anotado. Vou te lembrar às 15:26.", MIX)
check("mistura mantém a promessa", "15:26" in m, m)

print("\n2d. Resposta sem promessa de hora não é tocada")
sem = motor_v8._polir_resposta("Anotado ✅", SEM_QUANDO)
check("sem promessa, sem interferência", sem.startswith("Anotado"), sem)

# ---------------------------------------------------------------------------
print("\n3. A guarda anti-contaminação é por Nº DE ITENS, não por vírgula")
# A guarda antiga olhava pontuação: qualquer vírgula ou " e " bloqueava o
# fallback. Mas "Preciso marcar médico, me lembra daqui 10min" é UM item com
# vírgula — e era exatamente o caso do Kevin que falhava.
FRASE_DUPLA = "comprar ração da Nina sexta e trocar o óleo em 6 meses"
a = motor_v8._preparar_item({"descricao": "comprar ração da Nina",
                             "tipo": "lembrete"}, ai_engine,
                            texto_origem=FRASE_DUPLA, n_itens=2)
b = motor_v8._preparar_item({"descricao": "trocar o óleo", "tipo": "lembrete"},
                            ai_engine, texto_origem=FRASE_DUPLA, n_itens=2)
check("2 itens: ração -> Pet", a["categoria"] == "Pet", a["categoria"])
check("2 itens: óleo -> Veículo", b["categoria"] == "Veículo", b["categoria"])
check("2 itens: óleo não herda a data da ração",
      b.get("data_vencimento") is None or
      b["data_vencimento"] != ai_engine.extract_due_date("sexta"),
      f"{b.get('data_vencimento')!r}")

c = motor_v8._preparar_item({"descricao": "marcar médico", "tipo": "lembrete"},
                            ai_engine,
                            texto_origem="Preciso marcar médico, me lembra daqui 10min",
                            n_itens=1)
check("1 item com vírgula: usa a frase toda", bool(c.get("hora_alvo")),
      f"{c.get('hora_alvo')!r}")

print("\n4. Como as pessoas REALMENTE dizem 'feito'")
CONCLUSOES = ["feito", "resolvi", "já resolvi", "ja fiz", "fiz", "paguei",
              "já paguei", "tá pago", "ta feito", "pronto", "comprei",
              "peguei", "liguei", "marquei", "já foi", "finalizei",
              "terminei", "dei baixa", "resolvido", "quitei"]
for t in CONCLUSOES:
    check(f"{t!r} = FEITO", motor_v8._e_conclusao_explicita(t))

NAO_CONCLUSAO = [
    "me lembra de pagar a luz",
    "paguei a luz mas preciso lembrar do gás semana que vem",
    "resolvi que vou trocar de plano, me lembra de ligar amanhã",
    "comprar leite",
    "quanto eu gastei?",
]
for t in NAO_CONCLUSAO:
    check(f"NÃO confunde {t!r}", not motor_v8._e_conclusao_explicita(t), t)

print("\n5. Como as pessoas dizem 'adiar'")
ADIAR = ["deixa pra amanhã", "deixa pra depois", "fica pra amanhã",
         "empurra pra semana que vem", "passa pra amanhã", "mais tarde",
         "outro dia", "semana que vem", "hoje não vai dar",
         "joga pra depois"]
for t in ADIAR:
    check(f"{t!r} = ADIAR", motor_v8._e_adiamento_explicito(t))

for t in ["me lembra amanhã", "paguei hoje", "comprar pão"]:
    check(f"NÃO confunde {t!r}", not motor_v8._e_adiamento_explicito(t), t)

print("\n6. Falta a hora -> oferece manhã/noite (não escolhe sozinho)")
COM_DIA_SEM_HORA = [{"descricao": "levar o carro na revisão",
                     "tipo": "lembrete", "categoria": "Veículo",
                     "valor_reais": None,
                     "data_vencimento": "2026-08-07", "hora_alvo": None}]
r6 = motor_v8._polir_resposta("Anotado ✅ — *levar o carro na revisão* · *07/08*",
                              COM_DIA_SEM_HORA)
print(f"\n--- oferta de horário ---\n{r6}\n---\n")
check("oferece manhã", "manhã" in r6.lower(), r6)
check("oferece noite", "noite" in r6.lower(), r6)
check("mostra os horários padrão", "08:00" in r6 and "20:00" in r6, r6)
check("mantém a confirmação", "revisão" in r6, r6)

print("\n6b. Com hora, não pergunta nada")
COM_HORA = [dict(COM_DIA_SEM_HORA[0], hora_alvo="09:00")]
r6b = motor_v8._polir_resposta("Anotado ✅ — *levar o carro* · *07/08 às 09:00*",
                               COM_HORA)
check("não oferece horário", "que horas te aviso" not in r6b.lower(), r6b)

print("\n6c. Se a resposta já pergunta algo, não empilha")
r6c = motor_v8._polir_resposta("Anotado. Qual o endereço da oficina?",
                               COM_DIA_SEM_HORA)
check("não empilha pergunta", r6c.lower().count("?") == 1, r6c)

print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
