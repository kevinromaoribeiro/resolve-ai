"""Achado da RODADA 4: a trava tem que perguntar "o Python achou o item?",
nao "isso parece frase?".

Com o predicado velho, cauda de ate 4 palavras passava batido e o caminho
degradado voltava a fechar o item errado — incluindo "feito o pagamento da
luz", que fecha o lembrete errado E deixa o pagamento sem registro.
"""
import pytest

import db
import motor_v8
import wa_bot
from conftest import TELEFONE, responder


def _status(item_id):
    with db.get_conn() as conn:
        r = conn.execute("SELECT status FROM items WHERE id=?",
                         (item_id,)).fetchone()
    return r["status"] if r else None


def _com_alarme(uid, descricao, tipo="lembrete"):
    item_id = db.add_item(user_id=uid, tipo=tipo, categoria="Outros",
                          descricao=descricao, status="pendente")
    db.log_dispatch(uid, "hora", item_id)
    return item_id


@pytest.fixture(autouse=True)
def _v8_fora_do_ar(monkeypatch):
    """Todo teste deste arquivo roda no caminho DEGRADADO — que e onde a
    protecao faltava."""
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: None)


@pytest.mark.parametrize("frase", [
    "fiz o cadastro no site",
    "terminei o relatorio do trimestre",
    "feito o pagamento da luz",
    "feito, me avisa",
    "feito, liga pra ana",
    "paguei a conta de luz 187",
    "feito isso, me avisa",
])
def test_nao_fecha_item_que_a_pessoa_nao_nomeou(usuario, frase):
    item = _com_alarme(usuario["id"], "estudar PM")
    responder(frase)
    assert _status(item) == "pendente", (
        f"'{frase}' fechou 'estudar PM', que nao tem nada a ver")


@pytest.mark.parametrize("frase", [
    "feito", "paguei", "já paguei", "resolvi", "pronto", "feito 👍",
])
def test_baixa_legitima_continua_passando(usuario, frase):
    item = _com_alarme(usuario["id"], "conta de luz", "despesa")
    responder(frase)
    assert _status(item) == "concluido", f"'{frase}' deixou de dar baixa"


def test_baixa_com_nome_certo_continua_passando(usuario):
    """A trava so pode barrar quando o Python NAO acha o item."""
    luz = _com_alarme(usuario["id"], "conta de luz", "despesa")
    _com_alarme(usuario["id"], "estudar PM")
    responder("feito o pagamento da luz")
    assert _status(luz) == "concluido", (
        "a trava barrou uma baixa que apontava pro item certo")


def test_predicado_direto(usuario):
    """Unitario do predicado, pra ele nao virar 'parece frase' de novo."""
    _com_alarme(usuario["id"], "conta de luz", "despesa")
    assert wa_bot._baixa_sem_alvo(usuario, "feito o pagamento da luz") is False
    assert wa_bot._baixa_sem_alvo(usuario, "fiz o cadastro no site") is True
    assert wa_bot._baixa_sem_alvo(usuario, "feito, me avisa") is True
    assert wa_bot._baixa_sem_alvo(usuario, "feito") is False
    assert wa_bot._baixa_sem_alvo(usuario, "bom dia") is False
