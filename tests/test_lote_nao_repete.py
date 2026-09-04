# -*- coding: utf-8 -*-
"""Clicar duas vezes no lote nao pode mandar a mensagem duas vezes.

04/09: o lote leva de 2 a 4 minutos (o espacamento entre envios e de 60 a
120s) e a tela nao dava sinal nenhum enquanto rodava. O dono clicou OK tres
vezes achando que nao tinha funcionado — e nada impedia tres lotes de
sairem, tres mensagens iguais pra cada pessoa, num numero ja restringido
DUAS vezes pela Meta.

Avisar nao basta: quem clica de novo e justamente quem nao viu o aviso. Por
isso a trava e do servidor e pula por padrao; repetir exige `repetir` no
corpo, explicito.
"""
import datetime as _dt

import db
import tempo
import wa_bot


def _cliente(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    return TestClient(wa_bot.app)


def _extras():
    return {"nome_da_novidade": "mini podcast",
            "o_que_ela_faz": "um áudio curto com as notícias"}


def _post(c, **extra):
    corpo = {"segmento": "todos", "template": "resolveai_novidade",
             "confirmo": True, "extras": _extras()}
    corpo.update(extra)
    return c.post("/painel/lote?k=tok", json=corpo).json()


# --- a trava ----------------------------------------------------------

def test_segundo_lote_nao_manda_de_novo(usuario, monkeypatch):
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(tel) or
        {"enviado": True, "via": "template", "motivo": ""})
    c = _cliente(monkeypatch)

    primeiro = _post(c)
    assert primeiro.get("ok"), primeiro
    assert primeiro["enviados"] >= 1
    quantos = len(enviados)

    segundo = _post(c)
    assert segundo.get("ok"), segundo
    assert len(enviados) == quantos, (
        "o segundo clique mandou a mesma mensagem de novo")
    assert segundo.get("pulados") or segundo.get("aviso")


def test_o_terceiro_clique_tambem_nao_passa(usuario, monkeypatch):
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(tel) or {"enviado": True})
    c = _cliente(monkeypatch)
    _post(c)
    quantos = len(enviados)
    _post(c)
    _post(c)
    assert len(enviados) == quantos


def test_repetir_explicito_manda_de_novo(usuario, monkeypatch):
    """A trava protege do acidente, nao da decisao consciente."""
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(tel) or {"enviado": True})
    c = _cliente(monkeypatch)
    _post(c)
    quantos = len(enviados)
    de_novo = _post(c, repetir=True)
    assert de_novo.get("ok"), de_novo
    assert len(enviados) > quantos


# --- conferir nao manda nada ------------------------------------------

def test_conferir_nao_dispara_mensagem(usuario, monkeypatch):
    chamou = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: chamou.append(1) or {"enviado": True})
    c = _cliente(monkeypatch)
    r = _post(c, conferir=True)
    assert r.get("conferindo") is True
    assert not chamou, "o modo conferir mandou mensagem"


def test_conferir_conta_quem_ja_recebeu(usuario, monkeypatch):
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True})
    c = _cliente(monkeypatch)
    antes = _post(c, conferir=True)
    assert antes["repetidos"] == 0
    _post(c)
    depois = _post(c, conferir=True)
    assert depois["repetidos"] >= 1
    assert depois["nomes"]


def test_conferir_exige_os_textos(usuario, monkeypatch):
    """Conferir roda DEPOIS da validacao: nao adianta conferir uma lista
    pra descobrir no OK que faltava escrever o texto.
    """
    c = _cliente(monkeypatch)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "todos", "template": "resolveai_novidade",
                     "confirmo": True, "conferir": True}).json()
    assert not r.get("ok")


# --- o registro por pessoa --------------------------------------------

def test_envio_manual_deixa_rastro_por_pessoa(usuario, monkeypatch):
    """Sem carimbo por pessoa nao da pra perguntar quem ja recebeu."""
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True})
    assert wa_bot._enviar_template_manual(
        usuario["id"], "resolveai_novidade", _extras())[0] is True
    assert usuario["id"] in db.recebeu_nos_ultimos_dias(
        "resolveai_novidade", 2)


def test_envio_recusado_nao_deixa_rastro(usuario, monkeypatch):
    """Carimbar o que nao saiu bloquearia um reenvio legitimo."""
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": False,
                                         "motivo": "fora da janela"})
    wa_bot._enviar_template_manual(usuario["id"], "resolveai_novidade",
                                   _extras())
    assert usuario["id"] not in db.recebeu_nos_ultimos_dias(
        "resolveai_novidade", 2)


def test_a_janela_de_dias_e_respeitada(usuario, monkeypatch):
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True})
    wa_bot._enviar_template_manual(usuario["id"], "resolveai_novidade",
                                   _extras())
    velho = (tempo.agora() - _dt.timedelta(days=9)).isoformat(
        timespec="seconds")
    with db.get_conn() as conn:
        conn.execute("UPDATE dispatches SET sent_at=? WHERE kind=?",
                     (velho, "resolveai_novidade"))
        conn.execute("UPDATE admin_acoes SET quando=? WHERE acao=?",
                     (velho, "enviar_template"))
    assert usuario["id"] not in db.recebeu_nos_ultimos_dias(
        "resolveai_novidade", 2)
    assert usuario["id"] in db.recebeu_nos_ultimos_dias(
        "resolveai_novidade", 30)


def test_outro_template_nao_bloqueia(usuario, monkeypatch):
    """A trava e por template. Um aviso nao pode calar outro."""
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True})
    wa_bot._enviar_template_manual(usuario["id"], "resolveai_novidade",
                                   _extras())
    assert usuario["id"] not in db.recebeu_nos_ultimos_dias(
        "resolveai_podcast_pronto", 2)


def test_o_carimbo_nao_come_cota_de_cortesia():
    """O nome do template nao pode virar kind de cortesia por acidente.

    Se virasse, um aviso manual passaria a silenciar o motor por 7 dias.
    """
    import templates as _cat
    for nome in _cat.CATALOGO:
        assert nome not in wa_bot.KINDS_DE_CORTESIA, nome
