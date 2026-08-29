# -*- coding: utf-8 -*-
"""LOTE NAO PODE DEIXAR O BOT SURDO.

INCIDENTE 28/08/2026, e a culpa foi minha. O `/painel/lote` e `async def` e
eu usei `time.sleep()` entre os envios pra espacar. Num endpoint async isso
NAO espaca: BLOQUEIA O EVENT LOOP INTEIRO do FastAPI.

Resultado: durante os ~15 minutos do disparo, o bot parou de responder a
TODO MUNDO. As mensagens chegavam no webhook (200 OK no log) e morriam sem
processamento. O Kevin mandou "quero comecar" e um lembrete de verdade, e
levou silencio.

A regra, e ela vale pra qualquer endpoint async daqui pra frente: NENHUMA
espera bloqueante dentro do event loop. `asyncio.sleep` cede o controle e o
servidor segue atendendo; `time.sleep` congela o processo.
"""
import ast
import inspect
import re

import pytest

import wa_bot


def _sem_comentario(codigo):
    """Tira linhas de comentario.

    O teste tem que olhar CODIGO. Sem isto ele acusava o proprio comentario
    que documenta o incidente ("NUNCA time.sleep") como se fosse a chamada —
    um teste que reprova a documentacao do bug que ele existe pra impedir.
    """
    linhas = codigo.splitlines()
    return "\n".join(l for l in linhas if not l.lstrip().startswith("#"))


def _fonte_da_rota(nome):
    """Extrai o codigo da funcao de rota pelo nome, do arquivo."""
    fonte = inspect.getsource(wa_bot)
    m = re.search(r"\n    async def %s\(.*?(?=\n    @app\.|\n    def |\Z)"
                  % re.escape(nome), fonte, re.S)
    assert m, "rota %s nao encontrada" % nome
    return _sem_comentario(m.group(0))


ROTAS_ASYNC = ["painel_lote", "painel_acao"]


@pytest.mark.parametrize("rota", ROTAS_ASYNC)
def test_rota_async_nao_usa_sleep_bloqueante(rota):
    """`time.sleep` num handler async congela o bot pra base inteira."""
    codigo = _fonte_da_rota(rota)
    # procura chamadas .sleep( que NAO sejam asyncio
    for m in re.finditer(r"(\w+)\.sleep\(", codigo):
        mod = m.group(1)
        assert mod in ("asyncio",), (
            "%s usa %s.sleep() — num endpoint async isso bloqueia o event "
            "loop e o bot para de responder a todo mundo. Use "
            "`await asyncio.sleep()`." % (rota, mod))


@pytest.mark.parametrize("rota", ROTAS_ASYNC)
def test_todo_sleep_da_rota_tem_await(rota):
    codigo = _fonte_da_rota(rota)
    for m in re.finditer(r"(await\s+)?asyncio\.sleep\(", codigo):
        assert m.group(1), (
            "%s chama asyncio.sleep() sem await — nao espera nada e ainda "
            "vira RuntimeWarning" % rota)


def test_o_lote_continua_espacando():
    """A correcao nao pode virar rajada: o espacamento e o que evitou a
    restricao de 04/08. Tirar o sleep resolveria o travamento e criaria um
    problema pior."""
    codigo = _fonte_da_rota("painel_lote")
    assert "asyncio.sleep" in codigo, "o lote perdeu o espacamento"
    assert "ENVIO_INTERVALO_MIN" in codigo and "ENVIO_INTERVALO_MAX" in codigo


def test_lote_roda_sem_travar(monkeypatch, base_limpa_lote):
    """De ponta a ponta: o lote responde e nao segura o processo."""
    import time
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "template",
                                         "motivo": ""})
    c = TestClient(wa_bot.app)
    t0 = time.time()
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "todos",
                     "template": "reativar_boas_vindas", "confirmo": True})
    assert r.json().get("ok"), r.text
    assert time.time() - t0 < 10, "lote demorou demais mesmo sem espacamento"


@pytest.fixture
def base_limpa_lote():
    import db
    def _zera():
        with db.get_conn() as c:
            c.execute("DELETE FROM dispatches")
            c.execute("DELETE FROM items")
            c.execute("DELETE FROM users")
    _zera()
    uid = db.create_user(nome="Teste", telefone="5511900009999")
    db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=10.0, status="pendente")
    yield
    _zera()
