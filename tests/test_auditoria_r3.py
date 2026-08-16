"""Achados da RODADA 3 do auditor.

Nenhum P0 desta vez, mas o P1-1 reabriu uma classe ja fechada duas vezes:
estado novo (BAIXA_ESCOLHA) roubando a resposta de um estado mais recente.
"""
import datetime as _dt

import pytest

import db
import motor_v8
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


def _com_alarme(uid, descricao, tipo="despesa"):
    item_id = db.add_item(user_id=uid, tipo=tipo, categoria="Contas",
                          descricao=descricao, status="pendente")
    db.log_dispatch(uid, "hora", item_id)
    return item_id


# --- P1-1: decisao nova mata pergunta velha ------------------------------

def test_menu_da_foto_nao_perde_o_um_para_pergunta_velha(usuario):
    """A pessoa manda a foto DEPOIS da pergunta, ve o menu 1/2 na tela e
    responde "1" pra ele. O "1" nao pode ser capturado pela pergunta
    antiga."""
    luz = _com_alarme(usuario["id"], "conta de luz")
    _com_alarme(usuario["id"], "conta de agua")
    responder("feito conta")                    # arma a pergunta numerada
    assert TELEFONE in wa_bot.BAIXA_ESCOLHA

    wa_bot._armar_pending(TELEFONE, dict(BOLETO))   # foto chega depois
    assert TELEFONE not in wa_bot.BAIXA_ESCOLHA, (
        "a pergunta velha sobreviveu a uma decisao mais nova")

    reply = responder("1")

    assert _status(luz) == "pendente", (
        f"concluiu item que a pessoa nao citou: {reply!r}")
    pagas = [i for i in db.list_items(usuario["id"])
             if i["tipo"] == "despesa" and i["status"] == "concluido"]
    assert any(i["descricao"] == "boleto enel" for i in pagas), (
        f"o '1' nao foi pro menu da foto: {pagas}")


# --- P1-2: acento -------------------------------------------------------

@pytest.mark.parametrize("escrito,item", [
    ("feito agua", "Conta de Água"),
    ("feito água", "Conta de Água"),
    ("feito pao", "comprar pão"),
    ("feito ACADEMIA", "academia"),
])
def test_placar_ignora_acento_e_caixa(usuario, escrito, item):
    alvo = _com_alarme(usuario["id"], item)
    _com_alarme(usuario["id"], "conta de luz")
    reply = responder(escrito)
    assert _status(alvo) == "concluido", (
        f"'{escrito}' nao casou com '{item}'. resposta={reply!r}")
    assert "Dei baixa" in reply, (
        f"a decisao voltou pro motor em vez de ficar no caminho "
        f"deterministico: {reply!r}")


# --- P2-4 (promovido junto): nome exato ganha do superset ----------------

def test_nome_exato_ganha_do_superset(usuario):
    exato = _com_alarme(usuario["id"], "conta de luz")
    _com_alarme(usuario["id"], "conta de luz do escritorio")
    reply = responder("feito conta de luz")
    assert _status(exato) == "concluido", reply


# --- P1-3: a trava vale nos DOIS caminhos --------------------------------

def test_motor_classico_nao_conclui_com_frase(usuario, monkeypatch):
    """Caminho degradado (v8 fora do ar). A trava vivia so no caminho
    saudavel — ou seja, faltava exatamente onde mais importa."""
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: None)
    item = _com_alarme(usuario["id"], "estudar PM", "lembrete")

    responder("feito isso, me avisa")

    assert _status(item) == "pendente", (
        "o motor classico fechou item que a pessoa nao nomeou")


def test_despesa_nao_some_no_caminho_degradado(usuario, monkeypatch):
    """"paguei a conta de luz 187" fechava o item errado E o gasto sumia.

    LIMITE CONHECIDO que fica de fora: o motor classico nao le "187" solto
    como dinheiro (sem R$ e sem centavos), entao o item nasce com
    valor_reais=None e a resposta diz "valor nao informado". Quem le valor
    solto e o motor_v8; aqui o que importa e que nada se perde e que o item
    errado nao e fechado.
    """
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: None)
    item = _com_alarme(usuario["id"], "estudar PM", "lembrete")

    responder("paguei a conta de luz 187")

    assert _status(item) == "pendente", "fechou o item errado"
    novos = [i for i in db.list_items(usuario["id"]) if i["id"] != item]
    assert novos, "o gasto sumiu sem virar registro nenhum"
    assert "luz" in novos[0]["descricao"].lower(), novos[0]["descricao"]


def test_baixa_limpa_continua_funcionando(usuario, monkeypatch):
    """A trava nao pode barrar a baixa legitima."""
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: None)
    item = _com_alarme(usuario["id"], "conta de luz")
    responder("feito")
    assert _status(item) == "concluido"
