"""
teste_resumo.py — Bateria do resumo semanal (v16). Banco descartável.

Rodar:  python teste_resumo.py
Sai com código 1 se qualquer caso falhar (dá pra plugar em CI depois).

Verifica contra o BANCO, não contra a resposta bonita: cada caso monta o
estado no SQLite e pergunta ao motor o que ele dispararia.
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

DB = os.path.join(tempfile.mkdtemp(), "teste_resumo.db")
os.environ["DB_PATH"] = DB          # antes do import: db.py lê no import

import db          # noqa: E402
import scheduler   # noqa: E402

FALHAS = []


def check(nome, cond, detalhe=""):
    print(("  OK   " if cond else "  FALHA") + f" {nome}"
          + (f"  -> {detalhe}" if (detalhe and not cond) else ""))
    if not cond:
        FALHAS.append(nome)


def novo_usuario(nome, telefone, dia="Segunda-feira"):
    uid = db.create_user(nome=nome, telefone=telefone, dia_resumo=dia)
    db.update_user_fields(uid, onboarding_step="done", status="ativo")
    return uid


def limpar_disparos():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM dispatches")


db.init_db()
print(f"banco de teste: {DB}\n")

# Segunda-feira de referência (2026-08-03 é uma segunda).
SEG = date(2026, 8, 3)
assert SEG.weekday() == 0, "data base do teste não é segunda"
SEG_8H = datetime(2026, 8, 3, 8, 5)
SEG_22H = datetime(2026, 8, 3, 22, 5)
TER_8H = datetime(2026, 8, 4, 8, 5)

# ---------------------------------------------------------------------------
print("1. Parser de dia_resumo (o gatilho)")
casos = {"Segunda-feira": 0, "segunda": 0, "SEGUNDA FEIRA": 0, "Terça-feira": 1,
         "terca": 1, "Sábado": 5, "sabado": 5, "Domingo": 6,
         "": None, None: None, "banana": None}
for entrada, esperado in casos.items():
    got = scheduler.dia_resumo_weekday(entrada)
    check(f"dia_resumo_weekday({entrada!r}) == {esperado}", got == esperado,
          f"veio {got}")

# ---------------------------------------------------------------------------
print("\n2. Usuário com semana cheia recebe resumo")
uid = novo_usuario("Kevin Ribeiro", "5511999990001")
db.add_item(uid, "lembrete", "Contas", "Conta de luz", valor_reais=187.4,
            data_vencimento=SEG.isoformat())
db.add_item(uid, "lembrete", "Pet", "Ração da Nina",
            data_vencimento=(SEG + timedelta(days=3)).isoformat())
db.add_item(uid, "lembrete", "Saúde", "Dentista",
            data_vencimento=(SEG - timedelta(days=6)).isoformat())  # atrasado
db.add_item(uid, "despesa", "Alimentação", "Mercado", valor_reais=210.0)
db.add_item(uid, "despesa", "Pet", "Petshop", valor_reais=132.9)

d = scheduler.check_weekly_summary(ref=SEG_8H)
check("dispara 1 resumo", len(d) == 1, f"veio {len(d)}")
msg = d[0]["message"] if d else ""
check("kind == 'resumo'", bool(d) and d[0]["kind"] == "resumo")
check("item_id é None (não é item)", bool(d) and d[0]["item_id"] is None)
check("cita Conta de luz", "Conta de luz" in msg)
check("cita o atrasado", "Dentista" in msg and "Em atraso" in msg)
check("soma gastos = R$ 342,90", "R$ 342,90" in msg, msg)
check("valor formatado BR", "R$ 187,40" in msg, msg)
check("marca 'hoje' no item de hoje", "hoje —" in msg, msg)
print("\n--- mensagem gerada ---\n" + msg + "\n-----------------------\n")

# ---------------------------------------------------------------------------
print("3. Janela horária e dia da semana")
check("22h não dispara", scheduler.check_weekly_summary(ref=SEG_22H) == [])
check("terça não dispara", scheduler.check_weekly_summary(ref=TER_8H) == [])
check("7h não dispara",
      scheduler.check_weekly_summary(ref=datetime(2026, 8, 3, 7, 59)) == [])
check("11h59 ainda dispara",
      len(scheduler.check_weekly_summary(ref=datetime(2026, 8, 3, 11, 59))) == 1)

# ---------------------------------------------------------------------------
print("\n4. Dedup: não manda duas vezes no mesmo dia")
limpar_disparos()
d1 = scheduler.check_weekly_summary(ref=SEG_8H)
db.log_dispatch(uid, "resumo")
d2 = scheduler.check_weekly_summary(ref=SEG_8H)
check("1ª chamada dispara", len(d1) == 1)
check("2ª chamada não dispara", len(d2) == 0, f"veio {len(d2)}")
limpar_disparos()

# ---------------------------------------------------------------------------
print("\n5. Usuário sem nada NÃO recebe (silêncio > 'você não tem nada')")
uid_vazio = novo_usuario("Ana Vazia", "5511999990002")
d = [x for x in scheduler.check_weekly_summary(ref=SEG_8H)
     if x["user_id"] == uid_vazio]
check("usuário vazio não recebe", d == [], f"veio {d}")
check("montar_resumo_semanal devolve None",
      scheduler.montar_resumo_semanal(db.get_user(uid_vazio), ref=SEG) is None)

# ---------------------------------------------------------------------------
print("\n6. Quem está em onboarding não recebe")
uid_onb = novo_usuario("Bruno Novato", "5511999990003")
db.update_user_fields(uid_onb, onboarding_step="nome")
db.add_item(uid_onb, "lembrete", "Contas", "Água",
            data_vencimento=SEG.isoformat())
d = [x for x in scheduler.check_weekly_summary(ref=SEG_8H)
     if x["user_id"] == uid_onb]
check("onboarding incompleto não recebe", d == [], f"veio {d}")

# ---------------------------------------------------------------------------
print("\n7. dia_resumo diferente respeita o usuário")
uid_qui = novo_usuario("Carla Quinta", "5511999990004", dia="Quinta-feira")
db.add_item(uid_qui, "lembrete", "Casa", "Faxina",
            data_vencimento=(SEG + timedelta(days=3)).isoformat())
seg = [x for x in scheduler.check_weekly_summary(ref=SEG_8H)
       if x["user_id"] == uid_qui]
qui = [x for x in scheduler.check_weekly_summary(
    ref=datetime(2026, 8, 6, 8, 5)) if x["user_id"] == uid_qui]
check("não recebe na segunda", seg == [])
check("recebe na quinta", len(qui) == 1, f"veio {len(qui)}")

# ---------------------------------------------------------------------------
print("\n8. Teto de itens (+N outros)")
uid_muito = novo_usuario("Davi Cheio", "5511999990005")
for i in range(12):
    db.add_item(uid_muito, "lembrete", "Outros", f"Tarefa {i}",
                data_vencimento=(SEG + timedelta(days=i % 7)).isoformat())
msg = scheduler.montar_resumo_semanal(db.get_user(uid_muito), ref=SEG)
check("corta em 8 itens", msg.count("\n• ") <= scheduler.RESUMO_MAX_ITENS + 1,
      f"linhas: {msg.count(chr(10) + '• ')}")
check("avisa quantos sobraram", "outro(s)" in msg, msg)

# ---------------------------------------------------------------------------
print("\n8b. Rotina diária/horária NÃO entra (almoçar, remédio de 8/8h)")
uid_rot = novo_usuario("Elis Rotina", "5511999990006")
db.add_item(uid_rot, "lembrete", "Saúde", "Almoçar",
            data_vencimento=SEG.isoformat(), hora_alvo="12:00",
            recorrencia="diaria")
db.add_item(uid_rot, "lembrete", "Saúde", "Remédio",
            data_vencimento=SEG.isoformat(), hora_alvo="08:00",
            recorrencia="horas:8")
db.add_item(uid_rot, "lembrete", "Casa", "Alongar",
            data_vencimento=(SEG + timedelta(days=2)).isoformat(),
            recorrencia="diaria")
msg = scheduler.montar_resumo_semanal(db.get_user(uid_rot), ref=SEG)
check("só rotina => resumo vazio (None)", msg is None, f"veio: {msg!r}")

# agora com UMA conta de verdade no meio da rotina
db.add_item(uid_rot, "lembrete", "Contas", "Boleto do IPTU",
            valor_reais=340.0,
            data_vencimento=(SEG + timedelta(days=2)).isoformat())
msg = scheduler.montar_resumo_semanal(db.get_user(uid_rot), ref=SEG)
check("a conta entra", msg and "Boleto do IPTU" in msg, f"{msg!r}")
check("Almoçar NÃO entra", msg and "Almoçar" not in msg, f"{msg!r}")
check("Remédio (horas:8) NÃO entra", msg and "Remédio" not in msg, f"{msg!r}")
check("Alongar NÃO entra", msg and "Alongar" not in msg, f"{msg!r}")
check("lista tem exatamente 1 item", msg and msg.count("\n• ") == 1, f"{msg!r}")

# recorrente semanal/mensal CONTINUA entrando (não é rotina de rotina)
db.add_item(uid_rot, "lembrete", "Veículo", "Revisão do carro",
            data_vencimento=(SEG + timedelta(days=4)).isoformat(),
            recorrencia="mensal:7")
msg = scheduler.montar_resumo_semanal(db.get_user(uid_rot), ref=SEG)
check("recorrente mensal entra", msg and "Revisão do carro" in msg, f"{msg!r}")
print("\n--- resumo com rotina filtrada ---\n" + str(msg)
      + "\n----------------------------------\n")

check("_e_rotina('diaria')", scheduler._e_rotina({"recorrencia": "diaria"}))
check("_e_rotina('horas:6')", scheduler._e_rotina({"recorrencia": "horas:6"}))
check("_e_rotina('mensal:10') == False",
      not scheduler._e_rotina({"recorrencia": "mensal:10"}))
check("_e_rotina(sem recorrencia) == False",
      not scheduler._e_rotina({"recorrencia": None}))

# ---------------------------------------------------------------------------
print("\n8c. REGRESSÃO com os dados REAIS de produção (user 23, 03/08/2026)")
# Copiado do banco via /painel/acao. O resumo v16.0 mandou os 5; o certo é 1.
uid_prod = novo_usuario("Kevin Producao", "5511999990007")
PROD = [
    # (desc, valor, venc, hora, recorrencia, deve_entrar)
    ("Esquentar o almoço", None, "2026-08-02", "12:39", None,     False),
    ("fruta",              None, "2026-08-03", "10:00", "diaria", False),
    ("fruta",              None, "2026-08-03", "16:30", "diaria", False),
    ("Cartão de débito",   None, "2026-08-03", "20:00", None,     True),
    ("comprar frutas",     None, "2026-08-04", "12:30", None,     False),
]
for desc, val, venc, hora, rec, _ in PROD:
    db.add_item(uid_prod, "lembrete", "Outros", desc, valor_reais=val,
                data_vencimento=venc, hora_alvo=hora, recorrencia=rec)
for desc, val, venc, hora, rec, deve in PROD:
    got = scheduler._entra_no_resumo(
        {"tipo": "lembrete", "categoria": "Outros", "descricao": desc,
         "valor_reais": val, "data_vencimento": venc, "hora_alvo": hora,
         "recorrencia": rec})
    check(f"{'ENTRA' if deve else 'sai  '} · {desc!r}", got == deve,
          f"veio {got}")
msg = scheduler.montar_resumo_semanal(db.get_user(uid_prod), ref=SEG)
check("resumo real tem exatamente 1 linha de item",
      msg and msg.count("\n• ") == 1, f"{msg!r}")
check("mantém o Cartão de débito", msg and "Cartão de débito" in msg)
check("some com 'fruta'", msg and "fruta" not in msg.lower())
check("some com 'almoço'", msg and "almo" not in msg.lower())
print("\n--- resumo real (v16.2) ---\n" + str(msg)
      + "\n---------------------------\n")

print("\n8d. Léxico de dinheiro salva a conta mal categorizada")
for termo in ["Boleto da faculdade", "IPVA 2026", "conta de luz",
              "Fatura do cartao", "pagar o aluguel", "Netflix"]:
    check(f"dinheiro: {termo!r}", scheduler._cheira_a_dinheiro(termo))
for termo in ["comprar frutas", "ligar pra mãe", "Esquentar o almoço",
              "levar o cachorro pra passear"]:
    check(f"nao-dinheiro: {termo!r}",
          not scheduler._cheira_a_dinheiro(termo))

print("\n8e. Lembrete SEM hora continua entrando (é planejamento)")
check("sem hora entra", scheduler._entra_no_resumo(
    {"tipo": "lembrete", "categoria": "Outros", "descricao": "Levar o carro",
     "valor_reais": None, "hora_alvo": None, "recorrencia": None}))
check("despesa sempre entra", scheduler._entra_no_resumo(
    {"tipo": "despesa", "categoria": "Outros", "descricao": "x",
     "valor_reais": None, "hora_alvo": "10:00", "recorrencia": None}))
check("com valor sempre entra", scheduler._entra_no_resumo(
    {"tipo": "lembrete", "categoria": "Outros", "descricao": "x",
     "valor_reais": 50.0, "hora_alvo": "10:00", "recorrencia": None}))

# ---------------------------------------------------------------------------
print("\n9. Integração: run_proactive_engine expõe resumo_dispatches")
limpar_disparos()
r = scheduler.run_proactive_engine(ref_date=SEG, ref_datetime=SEG_8H)
check("chave resumo_dispatches existe", "resumo_dispatches" in r)
check("total soma o resumo", r["total"] >= len(r["resumo_dispatches"]))
check("gera pelo menos 1 resumo", len(r["resumo_dispatches"]) >= 1,
      f"veio {len(r.get('resumo_dispatches', []))}")
r_noite = scheduler.run_proactive_engine(ref_date=SEG, ref_datetime=SEG_22H)
check("quiet hours zera o resumo", r_noite["resumo_dispatches"] == [])

# ---------------------------------------------------------------------------
print("\n10. Helpers novos do db.py")
gastos = db.spend_by_category_period(uid, (SEG - timedelta(days=6)).isoformat(),
                                     SEG.isoformat())
check("spend_by_category_period soma certo",
      round(sum(gastos.values()), 2) == 342.90, f"veio {gastos}")
check("ordena do maior pro menor",
      list(gastos) == sorted(gastos, key=gastos.get, reverse=True))
atr = db.items_overdue_for_user(uid, ref=SEG)
check("items_overdue_for_user acha o atrasado",
      len(atr) == 1 and atr[0]["descricao"] == "Dentista", f"veio {atr}")
check("não conta item futuro como atrasado",
      all(i["data_vencimento"] < SEG.isoformat() for i in atr))

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if FALHAS:
    print(f"{len(FALHAS)} FALHA(S): " + ", ".join(FALHAS))
    sys.exit(1)
print("TUDO VERDE.")
