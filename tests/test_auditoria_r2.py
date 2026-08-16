"""Achados da RODADA 2 do auditor. Todos por execucao.

A rodada 1 consertou o sequestro do PENDING e criou tres buracos novos:
a cauda do regex comia registro de despesa, "feito conta de luz" virava
pergunta infinita, e o gate irmao deixava metade do M1.5 morto pra base
antiga. Estes testes travam os tres.
"""
import datetime as _dt

import pytest

import db
import tempo
import wa_bot
from conftest import TELEFONE, responder

BOLETO = {"tipo": "despesa", "descricao": "boleto enel", "valor_reais": 210.5,
          "categoria": "Contas", "data_vencimento": "2026-08-20",
          "status": "pendente"}


def _status(item_id):
    with db.get_conn() as conn:
        r = conn.execute("SELECT status FROM items WHERE id=?",
                         (item_id,)).fetchone()
    return r["status"] if r else None


def _com_alarme(uid, descricao="estudar PM", tipo="lembrete"):
    item_id = db.add_item(user_id=uid, tipo=tipo, categoria="Outros",
                          descricao=descricao, status="pendente")
    db.log_dispatch(uid, "hora", item_id)
    return item_id


# --- P0-1: a cauda nao pode comer registro de despesa --------------------

@pytest.mark.parametrize("frase,marca", [
    ("paguei 250 no mercado", "mercado"),
    ("paguei 89 na farmacia", "farmacia"),
])
def test_despesa_nova_nao_vira_pergunta(usuario, frase, marca):
    """Com um alarme tocado nas ultimas 12h, a cauda do _BAIXA_RE
    sequestrava a mensagem: o bot perguntava "qual deles?" e o gasto NAO
    era registrado. Perda de dado direta."""
    _com_alarme(usuario["id"])
    antes = len(db.list_items(usuario["id"]))

    reply = responder(frase)

    assert "Qual deles" not in reply, (
        f"'{frase}' virou pergunta em vez de registro: {reply!r}")
    depois = db.list_items(usuario["id"])
    assert len(depois) > antes, (
        f"'{frase}' nao registrou nada. banco={[i['descricao'] for i in depois]}")


def test_terminei_algo_que_nao_esta_na_lista(usuario):
    """Cauda que nao aponta pra nada da lista: sai da frente, nao pergunta."""
    _com_alarme(usuario["id"])
    reply = responder("terminei o relatorio do trimestre")
    assert "Qual deles" not in reply, reply


# --- P0-2: "feito + nome" resolve, e a pergunta tem saida ----------------

def test_feito_conta_de_luz_com_nomes_parecidos(usuario):
    """O caso numero um do produto: duas contas, nomes parecidos.
    "conta" casa com as duas — "luz" e que decide."""
    luz = _com_alarme(usuario["id"], "conta de luz", "despesa")
    agua = _com_alarme(usuario["id"], "conta de agua", "despesa")

    reply = responder("feito conta de luz")

    assert _status(luz) == "concluido", (
        f"nao deu baixa na conta de luz. resposta={reply!r}")
    assert _status(agua) == "pendente", "deu baixa na conta errada"


def test_palavra_de_tres_letras_conta(usuario):
    """"luz", "gas", "pix": o vocabulario de conta e curto. O corte em 4
    letras apagava justamente o que distingue."""
    luz = _com_alarme(usuario["id"], "conta de luz", "despesa")
    _com_alarme(usuario["id"], "conta de agua", "despesa")
    responder("feito luz")
    assert _status(luz) == "concluido"


def test_pergunta_ambigua_tem_saida_por_numero(usuario):
    """Sem saida, a pessoa respondia o proprio rotulo que o bot listou e
    recebia a mesma pergunta pra sempre."""
    a = _com_alarme(usuario["id"], "conta de luz", "despesa")
    b = _com_alarme(usuario["id"], "conta de agua", "despesa")

    pergunta = responder("feito conta")
    assert "Qual deles" in pergunta, pergunta
    assert "*1*" in pergunta and "*2*" in pergunta, (
        f"a pergunta nao e numerada, entao nao tem resposta curta: {pergunta}")

    reply = responder("1")
    assert "baixa" in reply.lower(), reply
    concluidos = [i for i in (a, b) if _status(i) == "concluido"]
    assert len(concluidos) == 1, "o numero nao concluiu exatamente um item"


def test_numero_solto_fora_da_janela_nao_conclui(usuario):
    """Um "2" solto no meio de outra conversa nao pode dar baixa."""
    item = _com_alarme(usuario["id"], "conta de luz", "despesa")
    _com_alarme(usuario["id"], "conta de agua", "despesa")
    responder("feito conta")
    wa_bot.BAIXA_ESCOLHA[TELEFONE]["quando"] = (
        tempo.agora() - _dt.timedelta(hours=2))
    responder("1")
    assert _status(item) == "pendente", "numero velho concluiu item"


# --- P0-3: o gate irmao (M1.5 inteiro) para a base antiga ----------------

def test_contador_de_adiamento_vale_para_base_antiga(usuario):
    """Com step='done' o contador nunca registrava: o escalonamento, o
    silenciamento e o aviso novo eram codigo inalcancavel."""
    db.update_user_fields(usuario["id"], onboarding_step="done")
    item = _com_alarme(usuario["id"], "pagar o IPTU")

    for _ in range(db.SNOOZE_LIMITE + 1):
        reply = responder("adiar")

    assert db.dispatch_count_item("adiado", item) == db.SNOOZE_LIMITE + 1, (
        "o contador M1.5 nao registrou pra quem tem onboarding_step='done'")
    assert db.item_silenciado(item), "nunca silenciou"
    assert "paro de te cobrar" in reply.lower(), reply


def test_ack_de_botao_vale_para_base_antiga(usuario):
    db.update_user_fields(usuario["id"], onboarding_step="done")
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="olhar os pontos", status="pendente")
    reply = responder("Isso mesmo")
    assert "guardado" in reply.lower(), reply


# --- P1-4: correcao de valor sem a palavra "valor" -----------------------

@pytest.mark.parametrize("correcao", ["é 250 reais", "250 reais",
                                      "valor 210,50 vence 25/07", "210,50"])
def test_correcao_de_valor_continua_no_menu(usuario, correcao):
    """Bloquear a correcao rebaixava a despesa da foto para lembrete e
    ainda fazia o motor criar um item chamado "é" na lista."""
    wa_bot._armar_pending(TELEFONE, dict(BOLETO))
    responder(correcao)
    lixo = [i["descricao"] for i in db.list_items(usuario["id"])
            if len(i["descricao"].strip()) <= 2]
    assert not lixo, f"criou item com descricao lixo: {lixo}"
    assert TELEFONE in wa_bot.PENDING, (
        f"'{correcao}' soltou a decisao em vez de corrigir o dado")


# --- regressao: os negativos continuam negativos -------------------------

@pytest.mark.parametrize("frase", [
    "o bolo ta feito de chocolate",
    "não fiz",
])
def test_negativos_nao_dao_baixa(usuario, frase):
    item = _com_alarme(usuario["id"], "avisar o contador")
    responder(frase)
    assert _status(item) == "pendente", f"'{frase}' concluiu item"


def test_frase_com_palavra_de_baixa_nao_conclui_pelo_motor(usuario,
                                                           monkeypatch):
    """"feito isso, me avisa" e combinacao de proxima etapa, nao baixa.

    Este e o caminho de PRODUCAO: o motor_v8 (LLM) entende como conclusao e
    a trava de Python veta. O caminho deterministico ja recusa antes.
    """
    import motor_v8
    item = _com_alarme(usuario["id"], "avisar o contador")
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: {
        "intent": "conclusao", "reply": "Feito, dei baixa.", "items": [],
        "concluir": item, "needs_decision": False, "mode": "v8"})

    responder("feito isso, me avisa")

    assert _status(item) == "pendente", (
        "o motor concluiu um item que a pessoa nao nomeou")


def test_motor_classico_nao_fecha_item_pelo_nome_errado(usuario):
    """Era limite conhecido (falhava igual no HEAD) e foi consertado na
    rodada 3: a trava de conclusao passou a valer tambem no caminho
    degradado, nao so no do motor_v8."""
    item = _com_alarme(usuario["id"], "estudar PM")
    reply = responder("paguei a conta de luz 187")
    assert "Qual deles" not in reply, reply
    assert _status(item) == "pendente", (
        "fechou 'estudar PM' por causa de uma frase sobre conta de luz")
