"""FASE 1 — P0-3, P1-4, P1-5, P1-6, P2-7 e as regressoes do caminho da imagem.

Cada teste executa o fluxo. Nenhum le codigo e conclui que esta certo: foi
exatamente assim (regex que compilava e nunca casava) que um bug ficou dias
em producao fingindo funcionar.
"""
import pytest

import db
import jornada
import motor_v8
import scheduler
import tempo
import wa_bot
from conftest import TELEFONE, responder, texto


# --- P0-3: resposta vazia do LLM nao pode virar pergunta generica ---------

def test_reply_vazio_nao_chega_no_usuario(usuario, monkeypatch):
    """11/08 23:44 — o motor devolveu reply:"" e a pessoa levou um
    "nao ficou claro o que voce gostaria de registrar" logo depois de
    confirmar um item."""
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Contas",
                descricao="conta de luz", data_vencimento="2026-08-20",
                status="pendente")
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: {
        "intent": "conversa", "reply": "", "items": [],
        "needs_decision": False, "mode": "v8"})

    # mensagem qualquer que chega ate o motor (nao e ACK de botao nem comando)
    reply = responder("entao ta")

    assert reply.strip(), "o bot devolveu mensagem VAZIA para o usuario"
    assert "conta de luz" in reply, (
        f"fallback improvisou em vez de usar o estado que o Python ja tem: "
        f"{reply!r}")


def test_reply_vazio_sem_pendente_tambem_responde(usuario, monkeypatch):
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: {
        "intent": "conversa", "reply": "   ", "items": [],
        "needs_decision": False, "mode": "v8"})
    reply = responder("isso mesmo")
    assert reply.strip()


# --- P1-4: onboarding fora de ordem --------------------------------------

_PEDIDO = "me manda uma coisa que você não pode esquecer"


def test_onboarding_nao_pede_demanda_depois_de_ja_guardar(limpo):
    """11/08 23:43: o bot anotou "Estudar Product Manager" e SO ENTAO pediu
    a primeira demanda. Pedir o que ja recebeu e o bot admitindo que nao
    estava prestando atencao."""
    fone = "5511977776666"
    with db.get_conn() as conn:
        conn.execute("DELETE FROM users WHERE telefone LIKE ?", (f"%{fone}%",))

    def _itens():
        u = db.get_user_by_phone(fone)
        return db.list_items(u["id"]) if u else []

    def _checar(msgs):
        for m in msgs:
            if m and _PEDIDO in m:
                assert not _itens(), (
                    "o bot pediu a primeira demanda DEPOIS de ja ter "
                    "guardado item")

    for entrada in ("oi", "concordo", "Kevin",
                    "me lembra de estudar Product Manager amanha as 10h"):
        antes = len(limpo)
        reply = responder(entrada, telefone=fone)
        _checar([reply] + [t for _, t in limpo[antes:]])


def test_demanda_antes_do_aceite_nao_vira_item(limpo):
    """Sem aceite nao existe base de dado — mas tambem nao pode sumir."""
    fone = "5511966665555"
    with db.get_conn() as conn:
        conn.execute("DELETE FROM users WHERE telefone LIKE ?", (f"%{fone}%",))
    responder("oi", telefone=fone)
    reply = responder("luz 187 vence dia 20", telefone=fone)

    u = db.get_user_by_phone(fone)
    assert not db.list_items(u["id"]), "gravou item sem aceite de LGPD"
    assert "não registrei" in reply.lower() or "guardei" in reply.lower(), (
        f"descartou a demanda em silencio: {reply!r}")


# --- P1-5: regua de saude com leitura invertida ---------------------------

def test_regua_diz_o_motivo_da_cor():
    """13/08: "🔴 alto · pico 1/min" ao lado de "🟢 ok · pico 4/min".
    A conta estava certa; a linha e que nao dizia qual numero decidiu."""
    env = {"risco": "🔴 alto", "motivo": "3.0x mais proativas que respostas",
           "pico_por_minuto": 1, "proativas": 9, "entradas": 3}
    linha = wa_bot._linha_risco(env)
    assert "3.0x mais proativas" in linha
    assert "pico 1/min" in linha, "os numeros brutos sumiram da conferencia"


def test_pulso_classifica_pelo_ritmo_e_explica(usuario):
    """Executa contra o banco: 9 proativas para 3 recebidas = vermelho,
    mesmo com pico de 1/min."""
    uid = usuario["id"]
    for i in range(3):
        db.log_message(uid, TELEFONE, "in", "texto", f"msg {i}")
    for i in range(9):
        db.log_dispatch(uid, "hora")
    p = db.pulso_envio()
    assert "alto" in p["risco"], p
    assert p["pico_por_minuto"] < db.PICO_ALTO, (
        "o pico e que subiu — o teste nao esta medindo o ritmo")
    assert "proativas" in p["motivo"], p["motivo"]
    assert "pico" not in p["motivo"], (
        f"culpou o pico numa decisao que foi do ritmo: {p['motivo']!r}")


def test_pulso_culpa_o_pico_quando_e_o_pico(usuario):
    uid = usuario["id"]
    for i in range(12):
        db.log_message(uid, TELEFONE, "out", "texto", f"msg {i}")
    p = db.pulso_envio()
    assert "alto" in p["risco"], p
    assert "pico" in p["motivo"], p["motivo"]


def test_banco_limpo_e_verde():
    p = db.pulso_envio()
    assert p["motivo"], "motivo nunca pode vir vazio"


# --- P1-6: metrica que se contradiz na mesma mensagem ---------------------

def test_engajamento_explicita_o_denominador():
    """"0 pessoa(s)" e "Base: 11 pessoa(s)" na mesma tela."""
    eng = {"por_pessoa_dia": 0.0, "pessoas": 0, "dono_excluido": True}
    linha = wa_bot._linha_engajamento(eng, 11)
    assert "0 de 11" in linha, linha
    assert "sem contar você" in linha, linha


def test_engajamento_sem_exclusao_nao_mente():
    eng = {"por_pessoa_dia": 1.5, "pessoas": 4, "dono_excluido": False}
    linha = wa_bot._linha_engajamento(eng, 11)
    assert "4 de 11" in linha
    assert "sem contar" not in linha


# --- P2-7: cobranca diaria de item vencido -------------------------------

def test_vencido_cobra_uma_vez_so(usuario):
    """"venceu ontem e nao vi a baixa" chegou em dias seguidos."""
    uid = usuario["id"]
    ontem = (tempo.hoje() - __import__("datetime").timedelta(days=1))
    item_id = db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                          descricao="condominio", valor_reais=450.0,
                          data_vencimento=ontem.isoformat(),
                          status="pendente")
    primeira = scheduler.check_overdue()
    meus = [d for d in primeira if d["item_id"] == item_id]
    assert len(meus) == 1, f"cobrou {len(meus)}x no mesmo dia"

    for d in meus:                      # o cron grava o disparo
        db.log_dispatch(d["user_id"], d["kind"], d["item_id"])

    segunda = scheduler.check_overdue()
    assert not [d for d in segunda if d["item_id"] == item_id], (
        "cobrou o mesmo item vencido de novo no dia seguinte")


def test_vencido_silenciado_nao_cobra(usuario):
    """M1.5 — quem adiou 3 vezes para de ser cobrado, sem perder o item."""
    uid = usuario["id"]
    ontem = (tempo.hoje() - __import__("datetime").timedelta(days=1))
    item_id = db.add_item(user_id=uid, tipo="lembrete", categoria="Outros",
                          descricao="falar com o dentista",
                          data_vencimento=ontem.isoformat(),
                          status="pendente")
    db.silenciar_item(item_id, uid)
    assert not [d for d in scheduler.check_overdue()
                if d["item_id"] == item_id]
    assert db.list_items(uid, status="pendente"), (
        "silenciar apagou o item — silenciar e parar de tocar, nao sumir")


# --- REGRESSAO: o menu 1/2 da imagem silenciosa continua funcionando ------

def test_imagem_silenciosa_ainda_abre_o_menu(usuario, monkeypatch):
    """A Regra de Ouro vale pra imagem AMBIGUA.

    MUDOU NO M2.1: quando a visao devolve valor E vencimento, o Python
    registra direto em vez de perguntar — perguntar o que ele acabou de ler
    e atrito sem serviço. O menu continua vivo pro caso ambiguo, que e o
    caso pra que ele foi feito. Ver test_m21_boleto.py.
    """
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64fake")
    monkeypatch.setattr(
        wa_bot, "_read_image",
        lambda b64: "Print de uma conversa sobre o churrasco de sabado")
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "IMG1"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")
    assert "Despesa Paga" in reply and "Lembrete" in reply, reply
    assert TELEFONE in wa_bot.PENDING, "a Regra de Ouro parou de armar decisao"

    resposta = responder("1")
    assert "despesa" in resposta.lower()
    despesas = db.list_items(usuario["id"], tipo="despesa")
    assert len(despesas) == 1, f"o menu deixou de salvar o item: {despesas}"
    assert despesas[0]["status"] == "concluido"
    assert TELEFONE not in wa_bot.PENDING, "PENDING ficou preso apos escolha"


def test_pending_liberado_salva_como_lembrete(usuario, limpo):
    """Ao soltar a decisao presa, o item NAO pode evaporar."""
    wa_bot.PENDING[TELEFONE] = {"tipo": "despesa", "descricao": "boleto enel",
                                "valor_reais": 210.5, "categoria": "Contas",
                                "data_vencimento": "2026-08-20",
                                "status": "pendente"}
    responder("nada a ver")
    responder("outra coisa")
    itens = [i for i in db.list_items(usuario["id"])
             if i["descricao"] == "boleto enel"]
    assert itens, "a decisao presa foi descartada em silencio"
    assert itens[0]["tipo"] == "lembrete" and itens[0]["status"] == "pendente", (
        "salvou como despesa paga algo que ninguem disse ter pago")
