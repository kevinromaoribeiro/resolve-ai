"""
teste_v16_3.py — Bateria dos bugs achados no WhatsApp de produção (03/08/2026).

Cobre:
  1. nome "Feito"      — validador de nome do onboarding
  2. renomear          — "meu nome é X" / "me chama de X"
  3. categoria "Outros"— classify_category + fallback do motor_v8
  4. saída da busca web— markdown/link cru virando texto limpo

Rodar:  python teste_v16_3.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t163.db")
os.environ.setdefault("PAINEL_TOKEN", "teste")

import db            # noqa: E402
import ai_engine     # noqa: E402
import motor_v8      # noqa: E402
import wa_bot        # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


db.init_db()

# ---------------------------------------------------------------------------
print("1. Validador de nome — o bug do 'Feito'")
NAO_SAO_NOME = ["feito", "Feito", "FEITO", "pago", "paguei", "pronto", "ok",
                "concluido", "concluído", "resolvido", "adiar", "cancelar",
                "apagar", "listar", "sim", "não", "nao", "valeu", "obrigado",
                "oi", "bom dia", "teste", "quero", "menu", "reset"]
for t in NAO_SAO_NOME:
    check(f"rejeita {t!r}", wa_bot._is_not_a_name(t), "aceitou como nome")

SAO_NOME = ["Kevin", "Kevin Ribeiro", "Ana", "Maria Clara", "João Pedro",
            "Carol", "Rafinha", "Bia"]
for t in SAO_NOME:
    check(f"aceita {t!r}", not wa_bot._is_not_a_name(t), "rejeitou nome bom")

# ---------------------------------------------------------------------------
print("\n2. Comando de renomear")
uid = db.create_user(nome="Feito", telefone="5511900000001")
db.update_user_fields(uid, onboarding_step="done", status="ativo")
u = db.get_user(uid)

for frase, esperado in [
    ("meu nome é Kevin", "Kevin"),
    ("Meu nome e Kevin Ribeiro", "Kevin Ribeiro"),
    ("me chama de Kevin", "Kevin"),
    ("pode me chamar de Kevin", "Kevin"),
    ("me chamo Kevin", "Kevin"),
    ("na verdade me chamo Kevin", "Kevin"),
    ("prefiro ser chamado de Kevin", "Kevin"),
]:
    m = wa_bot._RENOMEAR_RE.match(frase)
    got = m.group("nome").strip() if m else None
    check(f"{frase!r} -> {esperado!r}", got == esperado, f"veio {got!r}")

# não pode confundir com pedido normal
for frase in ["me lembra de pagar a conta", "meu cartão vence dia 10",
              "qual é o meu nome?", "anota aí o nome do médico"]:
    check(f"NÃO renomeia com {frase!r}",
          wa_bot._RENOMEAR_RE.match(frase) is None)

# fluxo completo
r = wa_bot._handle_commands(db.get_user(uid), "5511900000001",
                            "meu nome é Kevin")
check("renomeia de verdade no banco", db.get_user(uid)["nome"] == "Kevin",
      f"ficou {db.get_user(uid)['nome']!r}")
check("confirma e pede desculpa pelo nome antigo",
      r and "Kevin" in r and "Feito" in r, f"{r!r}")

r2 = wa_bot._handle_commands(db.get_user(uid), "5511900000001",
                             "me chama de feito")
check("recusa renomear para um comando",
      db.get_user(uid)["nome"] == "Kevin" and r2 and "não parece" in r2,
      f"{r2!r} / nome={db.get_user(uid)['nome']!r}")

# ---------------------------------------------------------------------------
print("\n3. Categorizador — os itens reais que estavam todos em 'Outros'")
CASOS = [
    ("Cartão de débito",            "Contas"),
    ("cartão de crédito do nubank", "Contas"),
    ("boleto da faculdade",         "Contas"),
    ("conta de luz",                "Contas"),
    ("parcela do financiamento",    "Contas"),
    ("IPTU",                        "Contas"),
    ("comprar frutas",              "Alimentação"),
    ("fruta",                       "Alimentação"),
    ("esquentar o almoço",          "Alimentação"),
    ("mercado",                     "Alimentação"),
    ("ração da Nina",               "Pet"),
    ("veterinário do gato",         "Pet"),
    ("troca de óleo",               "Veículo"),
    ("IPVA do carro",               "Veículo"),
    ("consulta no dentista",        "Saúde"),
    ("remédio da pressão",          "Saúde"),
    ("botijão de gás",              "Casa"),
    ("assinatura da netflix",       "Lazer"),
]
for texto, esperado in CASOS:
    got = ai_engine.classify_category(texto)
    check(f"{texto!r} -> {esperado}", got == esperado, f"veio {got!r}")

print("\n3b. motor_v8 não carimba mais 'Outros' quando o LLM omite")
item = motor_v8._preparar_item(
    {"descricao": "Cartão de débito", "tipo": "lembrete"},
    ai_engine, texto_origem="me lembra do cartão de débito dia 20")
check("categoria preenchida pela função", item["categoria"] == "Contas",
      f"veio {item['categoria']!r}")

item2 = motor_v8._preparar_item(
    {"descricao": "fruta", "tipo": "lembrete", "categoria": "Outros"},
    ai_engine, texto_origem="me lembra de comprar fruta")
check("sobrescreve 'Outros' do LLM", item2["categoria"] == "Alimentação",
      f"veio {item2['categoria']!r}")

item3 = motor_v8._preparar_item(
    {"descricao": "x", "tipo": "lembrete", "categoria": "Pet"},
    ai_engine, texto_origem="")
check("respeita categoria válida do LLM", item3["categoria"] == "Pet",
      f"veio {item3['categoria']!r}")

item4 = motor_v8._preparar_item(
    {"descricao": "ligar pro Fernando", "tipo": "lembrete"},
    ai_engine, texto_origem="me lembra de ligar pro Fernando")
check("sem sinal continua 'Outros'", item4["categoria"] == "Outros",
      f"veio {item4['categoria']!r}")

# ---------------------------------------------------------------------------
print("\n4. Limpeza da saída de busca (o '## Highlights' que chegou no zap)")
SUJO = """## Highlights
- [¡¡España, campeona del mundo!! "Ha ganado el fútbol"](https://los40.com/2026/07/19/espana-campeona-del-mundo-ha-ganado-el-futbol/?utm_source=openai), Publicado en Sunday, July 19

A **Espanha** conquistou a Copa do Mundo de 2026 ao vencer a Argentina por 1 a 0.
Veja mais em https://exemplo.com/noticia?utm_source=openai


Quer que eu guarde algum lembrete?"""
limpo = motor_v8._limpar_saida_busca(SUJO)
print("\n--- antes ---\n" + SUJO + "\n--- depois ---\n" + limpo + "\n---\n")
check("sem '##'", "##" not in limpo, limpo)
check("sem link markdown", "](" not in limpo, limpo)
check("sem http", "http" not in limpo, limpo)
check("sem utm", "utm_" not in limpo, limpo)
check("sem negrito markdown '**'", "**" not in limpo, limpo)
check("mantém negrito do zap", "*Espanha*" in limpo, limpo)
check("mantém o fato", "Copa do Mundo de 2026" in limpo, limpo)
check("mantém o gancho", "lembrete" in limpo, limpo)
check("sem 3+ quebras seguidas", "\n\n\n" not in limpo, repr(limpo))
check("some com o cabeçalho 'Highlights'", "Highlights" not in limpo, limpo)
check("some com o 'Veja mais em' órfão", "Veja mais em" not in limpo, limpo)
check("some com 'Fonte:' órfã",
      "Fonte" not in motor_v8._limpar_saida_busca(
          "Resposta boa.\nFonte: https://x.com/y"), "sobrou Fonte")
check("não come frase legítima com 'link'",
      "mandar o link do boleto" in motor_v8._limpar_saida_busca(
          "Pode mandar o link do boleto que eu guardo."))
check("texto vazio não quebra", motor_v8._limpar_saida_busca("") == "")
check("texto limpo passa intacto (+ gancho do produto)",
      motor_v8._limpar_saida_busca("Tudo certo por aqui.")
      .startswith("Tudo certo por aqui."))

print("\n4b. Resposta REAL de produção (10:42) — textão + citação pendurada")
REAL = """A Espanha venceu a Copa do Mundo de 2026, derrotando a Argentina por 1 a 0 na prorrogação. Este é o segundo título mundial da Espanha, que já havia conquistado a taça em 2010. O gol decisivo foi marcado por Ferran Torres aos 106 minutos.

Rodri foi eleito o melhor jogador do torneio, recebendo a Bola de Ouro. Lionel Messi ficou com a Bola de Prata, e Kylian Mbappé levou a Bola de Bronze e a Chuteira de Ouro como artilheiro. Unai Simón, goleiro da Espanha, conquistou a Luva de Ouro, e Pau Cubarsí foi eleito o melhor jogador jovem.

A Espanha recebeu US$ 50 milhões (aproximadamente R$ 255 milhões) pela conquista. A Argentina, vice-campeã, ficou com US$ 33 milhões (cerca de R$ 169 milhões).
- [¡¡España, campeona del mundo!! "Ha ganado el fútbol"](https://los40.com/2026/07/19/x?utm_source=openai), Publicado en Sunday, July 19"""
lim = motor_v8._limpar_saida_busca(REAL)
print("\n--- o que chegou no zap (" + str(len(REAL)) + " chars) ---\n"
      + REAL + "\n\n--- o que vai chegar agora (" + str(len(lim))
      + " chars) ---\n" + lim + "\n---\n")
check("mata a citação em espanhol", "campeona" not in lim, lim)
check("mata 'Publicado en'", "Publicado" not in lim, lim)
check("cabe no celular (<= 520 chars)", len(lim) <= 520, f"{len(lim)} chars")
check("mantém a RESPOSTA (Espanha venceu)", "Espanha venceu" in lim, lim)
check("não corta no meio da palavra",
      lim.rstrip().endswith((".", "!", "?", "…")) or "?" in lim[-90:], lim)
check("devolve o gancho do produto",
      any(p in lim.lower() for p in ("lembr", "anotar", "conta", "consulta")),
      lim)
check("resposta curta não ganha corte",
      motor_v8._limpar_saida_busca("O dólar está em R$ 5,42 hoje.")
      .startswith("O dólar está em R$ 5,42 hoje."))

print("\n4c. Falha de busca não mente mais sobre limitação permanente")
r = motor_v8._resposta_nao_sei_do_mundo("Kevin")
check("não afirma que não acessa notícia",
      "não acesso" not in r["reply"], r["reply"])
check("diz que foi falha e pede pra tentar de novo",
      "falhou" in r["reply"] and "de novo" in r["reply"], r["reply"])

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
