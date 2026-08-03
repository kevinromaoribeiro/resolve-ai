"""
teste_v16_6.py — Os três últimos defeitos vistos no WhatsApp de produção.

  1. "Te aviso em 02/02"        -> data futura sem ano (era 2027)
  2. "Anotado a troca de hoje"  -> "hoje" que o usuário nunca disse
  3. "Precisando de ajuda com algo?" / "Posso ajudar com mais alguma coisa?"
     -> frase de atendimento que só serve pra vibrar o celular

Rodar:  python teste_v16_6.py
"""
import os
import sys
import tempfile
from datetime import date, timedelta

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t166.db")

import tempo        # noqa: E402
import motor_v8     # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


HOJE = tempo.hoje()
ANO = HOJE.year

# ---------------------------------------------------------------------------
print("1. Data futura mostra o ano; data do ano corrente não polui")
check(f"{ANO}-02-02 -> 02/02 (sem ano)",
      motor_v8._br(f"{ANO}-02-02") == "02/02",
      motor_v8._br(f"{ANO}-02-02"))
check(f"{ANO + 1}-02-02 -> 02/02/{ANO + 1}",
      motor_v8._br(f"{ANO + 1}-02-02") == f"02/02/{ANO + 1}",
      motor_v8._br(f"{ANO + 1}-02-02"))
check(f"{ANO - 1}-12-25 -> 25/12/{ANO - 1} (passado também mostra ano)",
      motor_v8._br(f"{ANO - 1}-12-25") == f"25/12/{ANO - 1}",
      motor_v8._br(f"{ANO - 1}-12-25"))
check("lixo não quebra", motor_v8._br("banana") == "banana")
check("None não quebra", motor_v8._br(None) == "None")

print("\n1b. A linha do item usa a data com ano")
linha = motor_v8._item_linha({"descricao": "trocar o óleo do carro",
                              "data_vencimento": f"{ANO + 1}-02-02",
                              "valor_reais": None, "hora_alvo": None})
check(f"linha cita 02/02/{ANO + 1}", f"02/02/{ANO + 1}" in linha, linha)

# ---------------------------------------------------------------------------
print("\n2. 'hoje' inventado some quando nenhum item é de hoje")
ITEM_FUTURO = [{"descricao": "trocar o óleo do carro",
                "data_vencimento": f"{ANO + 1}-02-02",
                "valor_reais": None, "hora_alvo": None}]
RUIM = "Anotado a troca de hoje."
corr = motor_v8._corrigir_hoje_falso(RUIM, ITEM_FUTURO)
print(f'    antes:  "{RUIM}"\n    depois: "{corr}"')
check("tirou o 'de hoje'", "hoje" not in corr.lower(), corr)
check("não deixou espaço duplo", "  " not in corr, repr(corr))
check("não deixou espaço antes do ponto", " ." not in corr, repr(corr))
check("manteve a confirmação", "Anotado" in corr, corr)

print("\n2b. 'hoje' legítimo é preservado")
ITEM_HOJE = [{"descricao": "pagar a luz", "data_vencimento": HOJE.isoformat(),
              "valor_reais": 180.0, "hora_alvo": None}]
BOM = "Anotado, a conta de luz vence hoje."
check("mantém 'hoje' quando o item É de hoje",
      motor_v8._corrigir_hoje_falso(BOM, ITEM_HOJE) == BOM)

print("\n2c. Um item de hoje entre vários basta pra manter")
MIX = ITEM_FUTURO + ITEM_HOJE
check("mistura mantém 'hoje'",
      motor_v8._corrigir_hoje_falso("Um vence hoje.", MIX) == "Um vence hoje.")
check("sem itens não mexe",
      motor_v8._corrigir_hoje_falso("Bom dia hoje.", []) == "Bom dia hoje.")

# ---------------------------------------------------------------------------
print("\n3. Enchimento de linguiça (as frases reais de 03/08 10:02)")
REAIS = [
    "Precisando de ajuda com algo?",
    "Posso ajudar com mais alguma coisa?",
    "Posso te ajudar com mais alguma coisa?",
    "Mais alguma coisa?",
    "Em que mais posso te ajudar?",
    "Fico à disposição!",
    "Estou à sua disposição.",
    "Qualquer coisa é só me chamar.",
    "Espero ter ajudado!",
]
for frase in REAIS:
    r = motor_v8.tirar_enchimento(f"Anotado. Conta de luz dia 20.\n\n{frase}")
    check(f"corta {frase!r}",
          "Anotado" in r and frase.rstrip("!.?").lower() not in r.lower(), r)

print("\n3b. Cortado no fim do parágrafo, não só em linha própria")
r = motor_v8.tirar_enchimento("Concluído. Posso ajudar com mais alguma coisa?")
print(f'    "Concluído. Posso ajudar com mais alguma coisa?" -> "{r}"')
check("sobra só o 'Concluído.'", r.strip() == "Concluído.", repr(r))

print("\n3c. NÃO come frase útil")
UTEIS = [
    "Quer que eu te avise um dia antes?",
    "Você já pagou essa conta?",
    "Responda *feito* que eu dou baixa.",
    "Qual o valor do boleto?",
    "Anotado. Te aviso dia 20 às 9h.",
    "Precisa de mais prazo? Responda *adiar*.",
]
for frase in UTEIS:
    check(f"preserva {frase!r}", motor_v8.tirar_enchimento(frase) == frase,
          motor_v8.tirar_enchimento(frase))

print("\n3d. Nunca some com a resposta inteira")
check("resposta só de enchimento volta original",
      motor_v8.tirar_enchimento("Posso ajudar com mais alguma coisa?")
      == "Posso ajudar com mais alguma coisa?")
check("vazio não quebra", motor_v8.tirar_enchimento("") == "")
check("None não quebra", motor_v8.tirar_enchimento(None) is None)

print("\n3e. O resumo semanal não é afetado")
RESUMO = ("☀️ Bom dia, Kevin. Sua semana no Resolve AI:\n\n"
          "📌 *Esta semana* (1)\n• hoje — Cartão de débito (R$ 340,50)\n\n"
          "Responda *feito* + o nome do item que eu dou baixa.")
check("resumo passa intacto", motor_v8.tirar_enchimento(RESUMO) == RESUMO)

# ---------------------------------------------------------------------------
print("\n4. Fluxo completo: a resposta ruim de 10:46 virando a boa")
ITENS = [
    {"descricao": "comprar ração da Nina", "valor_reais": None,
     "data_vencimento": (HOJE + timedelta(days=2)).isoformat(),
     "hora_alvo": None, "tipo": "lembrete", "categoria": "Pet"},
    {"descricao": "trocar o óleo do carro", "valor_reais": None,
     "data_vencimento": f"{ANO + 1}-02-02", "hora_alvo": None,
     "tipo": "lembrete", "categoria": "Veículo"},
]
ANTES = ("Anotado a troca de hoje.\n\n*Te aviso em 02/02* — 6 meses.\n\n"
         "Precisando de ajuda com algo?")
# mesma ordem exata do route() em produção
depois = motor_v8._corrigir_hoje_falso(ANTES, ITENS)
depois = motor_v8._completar_ano_nas_datas(depois, ITENS)
depois = motor_v8.tirar_enchimento(depois)
depois = motor_v8._confirmar_todos_os_itens(depois, ITENS)
print("\n--- o que o usuário recebeu (10:46) ---\n" + ANTES
      + "\n\n--- o que vai receber agora ---\n" + depois + "\n---\n")
check("sem 'hoje' falso", "hoje" not in depois.lower(), depois)
check("sem enchimento", "Precisando de ajuda" not in depois, depois)
check("cita a ração que faltava",
      "ração" in depois or "racao" in depois.lower(), depois)
check(f"data com ano ({ANO + 1})", f"02/02/{ANO + 1}" in depois, depois)
check("preservou as quebras de linha", "\n" in depois, repr(depois))

print("\n4b. _completar_ano_nas_datas é cirúrgico")
check("completa só a data que bate",
      motor_v8._completar_ano_nas_datas("Te aviso em 02/02.", ITENS)
      == f"Te aviso em 02/02/{ANO + 1}.")
check("não mexe em data de outro dia",
      motor_v8._completar_ano_nas_datas("Te aviso em 15/09.", ITENS)
      == "Te aviso em 15/09.")
check("não duplica ano já presente",
      motor_v8._completar_ano_nas_datas(f"Te aviso em 02/02/{ANO + 1}.", ITENS)
      == f"Te aviso em 02/02/{ANO + 1}.")
check("item do ano corrente não ganha ano",
      motor_v8._completar_ano_nas_datas(
          "Te aviso em 05/08.",
          [{"data_vencimento": (HOJE + timedelta(days=2)).isoformat()}])
      == "Te aviso em 05/08.")
check("sem itens não mexe",
      motor_v8._completar_ano_nas_datas("Te aviso em 02/02.", [])
      == "Te aviso em 02/02.")

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
