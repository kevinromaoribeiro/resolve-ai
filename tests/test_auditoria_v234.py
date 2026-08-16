"""Achados da auditoria da v23.4. Um teste por achado, todos por execucao.

O auditor REPROVOU a primeira rodada com 3 P0 e 4 P1. Estes testes falham
contra aquele codigo e passam contra o conserto — e ficam como trava pra
nenhum deles voltar.
"""
import datetime as _dt

import pytest

import db
import scheduler
import tempo
import wa_bot
from conftest import TELEFONE, responder

BOLETO = {"tipo": "despesa", "descricao": "boleto enel", "valor_reais": 210.5,
          "categoria": "Contas", "data_vencimento": "2026-08-20",
          "status": "pendente"}


def _status(item_id):
    with db.get_conn() as conn:
        r = conn.execute("SELECT status, tipo FROM items WHERE id=?",
                         (item_id,)).fetchone()
    return (r["status"], r["tipo"]) if r else (None, None)


def _com_alarme(uid, descricao="estudar PM", tipo="lembrete", quando=None):
    item_id = db.add_item(user_id=uid, tipo=tipo, categoria="Outros",
                          descricao=descricao, status="pendente")
    db.log_dispatch(uid, "hora", item_id)
    if quando:
        with db.get_conn() as conn:
            conn.execute("UPDATE dispatches SET sent_at=? WHERE item_id=?",
                         (quando.strftime("%Y-%m-%d %H:%M:%S"), item_id))
    return item_id


# --- P0-1: PENDING velho sequestra a mensagem e come o pedido novo -------

def test_pedido_novo_nao_vira_despesa_paga(usuario):
    """O menu testava `"pag" in c`: "me lembra de PAGar o condominio dia 25"
    era lido como "1 = despesa paga". O boleto velho virava despesa
    concluida e o pedido do usuario sumia sem aviso."""
    wa_bot._armar_pending(TELEFONE, dict(BOLETO))
    reply = responder("me lembra de pagar o condominio dia 25")

    assert "Despesa Paga" not in reply, reply
    despesas = [i for i in db.list_items(usuario["id"])
                if i["tipo"] == "despesa" and i["status"] == "concluido"]
    assert not despesas, f"criou despesa paga fantasma: {despesas}"
    descricoes = " | ".join(i["descricao"].lower()
                            for i in db.list_items(usuario["id"]))
    assert "condominio" in descricoes or "condomínio" in descricoes, (
        f"o pedido do usuario foi descartado em silencio. banco={descricoes!r}")


def test_pendencia_velha_nao_decide_mais_nada(usuario):
    """PENDING sem prazo virava jaula: um payload de dias antes continuava
    valendo. Agora expira."""
    wa_bot._armar_pending(TELEFONE, dict(BOLETO))
    wa_bot.PENDING_EM[TELEFONE] = tempo.agora() - _dt.timedelta(days=2)

    responder("bom dia")

    assert TELEFONE not in wa_bot.PENDING, "decisao de 2 dias atras sobreviveu"
    resgatado = [i for i in db.list_items(usuario["id"])
                 if i["descricao"] == "boleto enel"]
    assert resgatado and resgatado[0]["tipo"] == "lembrete", (
        "a pendencia vencida sumiu em vez de virar lembrete")


# --- P0-2: "feito + nome do item" (a forma que o proprio bot pede) -------

def test_feito_com_nome_do_item(usuario):
    """scheduler.py:278 manda "Responde *feito* + o nome do que ja resolveu"
    — e o regex exigia a palavra sozinha."""
    luz = _com_alarme(usuario["id"], "conta de luz", "despesa")
    _com_alarme(usuario["id"], "falar com o dentista")

    reply = responder("feito conta de luz")

    assert _status(luz)[0] == "concluido", (
        f"'feito conta de luz' nao deu baixa. resposta={reply!r}")
    assert "conta de luz" in reply


def test_feito_com_nome_ambiguo_nao_chuta(usuario):
    """Duas contas casando com a mesma palavra: concluir a errada e pior
    que nao concluir. Nesse caso ninguem e concluido."""
    a = _com_alarme(usuario["id"], "conta de luz", "despesa")
    b = _com_alarme(usuario["id"], "conta de agua", "despesa")
    responder("feito conta")
    assert _status(a)[0] == "pendente" and _status(b)[0] == "pendente"


def test_feito_isso_me_avisa_nao_e_baixa(usuario):
    """Frase combinando proxima etapa nao e comando de baixa.

    Aqui so se verifica o caminho DETERMINISTICO (que e o que esta rodada
    controla): ele recusa e sai da frente. A trava do caminho do motor tem
    teste proprio em test_auditoria_r2.py.
    """
    _com_alarme(usuario["id"], "avisar o contador")
    assert wa_bot._baixa_deterministica(usuario, TELEFONE,
                                        "feito isso, me avisa") is None


@pytest.mark.parametrize("palavra", [
    "feito ✅", "feito 👍", "já paguei", "ja paguei", "tá feito", "fiz",
])
def test_variantes_do_whatsapp(usuario, palavra):
    """Emoji DEPOIS da palavra e o jeito mais natural de responder."""
    item = _com_alarme(usuario["id"])
    responder(palavra)
    assert _status(item)[0] == "concluido", f"'{palavra}' nao deu baixa"


# --- P0-3: base antiga com onboarding_step="done" ------------------------

def test_baixa_funciona_para_base_antiga(usuario):
    """"done" tambem e cadastro fechado. Com a guarda velha a correcao
    inteira ficava desligada, em silencio, pros usuarios mais antigos."""
    db.update_user_fields(usuario["id"], onboarding_step="done")
    item = _com_alarme(usuario["id"])
    reply = responder("feito")
    assert _status(item)[0] == "concluido", (
        f"usuario com onboarding_step='done' nao consegue dar baixa: {reply!r}")


# --- P1-4: menu mais novo que o alarme ganha a palavra "pago" ------------

def test_menu_da_foto_ganha_de_alarme_velho(usuario):
    """Foto agora, alarme de 3h atras: "paguei" responde ao MENU."""
    antigo = _com_alarme(usuario["id"], "dentista",
                         quando=tempo.agora() - _dt.timedelta(hours=3))
    wa_bot._armar_pending(TELEFONE, dict(BOLETO))

    responder("paguei")

    assert _status(antigo)[0] == "pendente", (
        "concluiu o dentista, que a pessoa nem citou")
    pagas = [i for i in db.list_items(usuario["id"])
             if i["tipo"] == "despesa" and i["status"] == "concluido"]
    assert pagas and pagas[0]["descricao"] == "boleto enel", (
        f"a despesa da foto nao foi salva como paga: {pagas}")


def test_alarme_novo_ganha_do_menu_velho(usuario):
    """O contrario: o alarme tocou DEPOIS do menu. Ai "feito" e baixa."""
    wa_bot._armar_pending(TELEFONE, dict(BOLETO))
    wa_bot.PENDING_EM[TELEFONE] = tempo.agora() - _dt.timedelta(minutes=5)
    item = _com_alarme(usuario["id"])
    responder("feito")
    assert _status(item)[0] == "concluido"


# --- P1-5: payload malformado nao pode entrar no banco -------------------

@pytest.mark.parametrize("payload,campo,proibido", [
    ({"descricao": "x", "valor_reais": "210,50"}, "valor_reais", str),
    ({"descricao": "x", "data_vencimento": "20/08/2026"},
     "data_vencimento", str),
    ({"descricao": "x", "data_vencimento": "amanha"}, "data_vencimento", str),
])
def test_resgate_nao_grava_lixo(usuario, payload, campo, proibido):
    wa_bot._armar_pending(TELEFONE, payload)
    wa_bot._resgatar_pendencia(usuario, TELEFONE)
    item = [i for i in db.list_items(usuario["id"])
            if i["descricao"] == "x"][0]
    valor = item[campo]
    if campo == "data_vencimento":
        assert valor is None or __import__("re").match(r"^\d{4}-\d{2}-\d{2}$",
                                                       str(valor)), valor
    else:
        assert valor is None or isinstance(valor, float), repr(valor)


def test_cron_de_vencidos_sobrevive_a_data_invalida(usuario):
    """Uma linha ruim derrubava check_overdue inteiro: NINGUEM recebia
    aviso de vencimento naquele ciclo."""
    uid = usuario["id"]
    ontem = (tempo.hoje() - _dt.timedelta(days=1)).isoformat()
    bom = db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                      descricao="condominio", valor_reais=450.0,
                      data_vencimento=ontem, status="pendente")
    ruim = db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                       descricao="item torto", data_vencimento=ontem,
                       status="pendente")
    with db.get_conn() as conn:      # grava a data torta direto no banco
        conn.execute("UPDATE items SET data_vencimento='20/08/2026' WHERE id=?",
                     (ruim,))

    saida = scheduler.check_overdue()

    assert [d for d in saida if d["item_id"] == bom], (
        "a data invalida de um item calou o aviso de todos os outros")


# --- P1-7: silenciamento avisado -----------------------------------------

def test_silenciar_avisa_a_pessoa(usuario):
    """Parar de tocar e legitimo. Nao avisar e falha silenciosa: a pessoa
    segue achando que vai ser lembrada."""
    item = _com_alarme(usuario["id"], "pagar o IPTU")
    for _ in range(db.SNOOZE_LIMITE + 1):
        reply = responder("adiar")
    assert db.item_silenciado(item), "nao silenciou depois do limite"
    assert "IPTU" in reply and "paro de te cobrar" in reply.lower(), (
        f"silenciou sem avisar: {reply!r}")
    assert db.list_items(usuario["id"], status="pendente"), (
        "silenciar apagou o item"
    )


# --- P2-8 e P2-9: numeros que nao mentem ---------------------------------

def test_emergencia_escolhe_o_que_tem_data(usuario, monkeypatch):
    import motor_v8
    uid = usuario["id"]
    db.add_item(user_id=uid, tipo="lembrete", categoria="Outros",
                descricao="comprar pao", status="pendente")
    amanha = (tempo.hoje() + _dt.timedelta(days=1)).isoformat()
    db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                descricao="conta de luz", data_vencimento=amanha,
                status="pendente")
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: {
        "intent": "conversa", "reply": "", "items": [],
        "needs_decision": False, "mode": "v8"})

    reply = responder("entao ta")
    assert "conta de luz" in reply, (
        f"chamou de 'proximo pendente' um item sem data: {reply!r}")


def test_denominador_da_mesma_populacao(usuario):
    """"0 de 11 (sem contar voce)" com o voce dentro dos 11."""
    eng = db.engajamento(excluir_telefones=[TELEFONE])
    assert "base_comparavel" in eng
    total = len(db.list_users())
    assert eng["base_comparavel"] == total - 1, (
        f"base nao desconta quem foi excluido do numerador: "
        f"{eng['base_comparavel']} de {total}")
    linha = wa_bot._linha_engajamento(eng)
    assert f"de {total - 1} pessoa(s)" in linha, linha
