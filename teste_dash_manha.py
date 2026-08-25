"""
teste_dash_manha.py — O resumo diário das 8h que chega no WhatsApp do dono.

Painel que depende de você lembrar de abrir é painel que você não olha. Este
teste monta a mensagem com dados realistas e imprime exatamente o que vai
chegar no celular — porque copy de alerta só se avalia lendo.

Rodar:  python teste_dash_manha.py
"""
import datetime as dt
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "manha.db")
os.environ["PAINEL_TOKEN"] = "TOKEN123"
os.environ["ADMIN_PHONE"] = "5511999990000"
os.environ["DASH_URL_BASE"] = "http://177.153.58.163:8000"

import tempo    # noqa: E402
import db       # noqa: E402
import wa_bot   # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


db.init_db()
ONTEM = (tempo.hoje() - dt.timedelta(days=1)).isoformat()


def novo(nome, status, dias_atras):
    uid = db.create_user(
        nome=nome, telefone="5511" + str(abs(hash(nome)) % 10**9).zfill(9))
    db.update_user_fields(uid, onboarding_step="done", status=status)
    db.set_created_days_ago(uid, dias_atras)
    return uid


kevin = novo("Kevin Ribeiro", "ativo", 20)
novo("Carol Silva", "trial", 13)      # decide amanhã
novo("Ana Lima", "trial", 14)         # decide hoje
novo("Joao Souza", "trial", 3)
novo("Pedro Antigo", "trial", 30)     # saiu sem assinar

with db.get_conn() as c:
    for i in range(9):
        c.execute("INSERT INTO msg_log (user_id,telefone,direcao,tipo,"
                  "preview,ts) VALUES (?,?,?,?,?,?)",
                  (kevin, "x", "in", "texto", "oi",
                   f"{ONTEM} 10:0{i % 6}:00"))
    c.execute("INSERT INTO msg_log (user_id,telefone,direcao,tipo,preview,ts)"
              " VALUES (?,?,?,?,?,?)",
              (kevin, "x", "out_falhou", "texto", "x", f"{ONTEM} 11:00:00"))
    c.execute("INSERT INTO items (user_id,tipo,categoria,descricao,status,"
              "data_criacao) VALUES (?,?,?,?,?,?)",
              (kevin, "lembrete", "Contas", "luz", "pendente",
               f"{ONTEM} 10:00:00"))

# --- captura a mensagem sem enviar de verdade -------------------------------
enviadas = []
wa_bot.send_whatsapp = lambda num, msg: (enviadas.append(msg) or True)
wa_bot._instance_state = lambda: "open"
_agora_real = tempo.agora
tempo.agora = lambda: _agora_real().replace(hour=8, minute=5)

print("1. Dispara às 8h")
check("enviou", wa_bot.relatorio_matinal() is True)
check("mandou 1 mensagem", len(enviadas) == 1, str(len(enviadas)))
msg = enviadas[0] if enviadas else ""
print("\n" + "=" * 52)
print(msg)
print("=" * 52 + "\n")

# ATUALIZADO NO M2.5, quando o relatório foi redesenhado.
#
# O que mudou: saúde técnica, risco e "quem decide em 3 dias" deixaram de ter
# linha fixa e passaram a aparecer SÓ quando exigem ação, na seção do topo —
# a diferença entre um relatório que descreve e um que aponta. Então o que se
# checa aqui agora é o que sempre sai (hábito, movimento, base, dinheiro,
# link) mais a existência da seção de ação, que o cenário deste script
# dispara por ter falha de envio ontem.
print("2. Tem tudo que importa")
for rotulo, trecho in [
    ("seção de ação", "FAZER HOJE"),
    ("falhas de ontem", "falha(s) de envio"),
    ("hábito por pessoa/dia", "msg por pessoa/dia"),
    # O texto muda quando NAO ha semana anterior pra comparar (M2.5): o
    # relatorio diz "(primeira semana com base pra comparar)" em vez de
    # inventar um "▲ 2.00 vs. semana passada" que le como crescimento e e
    # so o primeiro dado existindo. Os dois sao tendencia; o que nao pode e
    # sumir o eixo temporal.
    ("tendência da semana", ("semana passada", "primeira semana")),
    ("movimento de ontem", "Ontem:"),
    ("base", "Base:"),
    ("líquido", "Líquido"),
    ("link do dash", "/dash?k="),
]:
    _opcoes = trecho if isinstance(trecho, tuple) else (trecho,)
    check(f"tem {rotulo}", any(o in msg for o in _opcoes), msg[:60])

check("tem veredito de engajamento",
      any(v in msg for v in ("virou hábito", "no limite",
                             "não virou hábito")), msg[:60])

print("\n3. Não repete no mesmo dia")
check("2ª chamada não manda", wa_bot.relatorio_matinal() is False)
check("continua 1 mensagem", len(enviadas) == 1, str(len(enviadas)))

print("\n4. Só na janela da manhã")
tempo.agora = lambda: _agora_real().replace(hour=6, minute=0)
check("6h não manda", wa_bot.relatorio_matinal() is False)
tempo.agora = lambda: _agora_real().replace(hour=15, minute=0)
check("15h não manda", wa_bot.relatorio_matinal() is False)

print("\n5. Não consome a cota anti-bloqueio do usuário")
antes = wa_bot._proativas_hoje(kevin)
db.log_dispatch(kevin, "dash-manha")
check("dash-manha fora da cota", wa_bot._proativas_hoje(kevin) == antes,
      f"{wa_bot._proativas_hoje(kevin)} vs {antes}")

print("\n" + "=" * 52)
tempo.agora = _agora_real
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
