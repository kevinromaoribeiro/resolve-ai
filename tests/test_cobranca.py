# -*- coding: utf-8 -*-
"""A cobrança de quem pediu o link e não pagou (M7.6).

O Kevin: "a gente TEM QUE MANDAR a cobrança, não simplesmente esperar ou
parar de servir".

Antes disto: nada emitia `cobranca-link` (template aprovado desde 28/08 e
nunca usado), e `user_can_receive` devolve False pra trial expirado — a
pessoa sumia do radar exatamente no dia de decidir a compra.
"""
import datetime as _dt

import pytest

import db
import scheduler
import templates as _cat
import tempo
import wa_bot
from conftest import TELEFONE, responder


def _pediu_ha(usuario, dias):
    """A pessoa digitou 'assinar' e recebeu o link ha N dias."""
    with db.get_conn() as c:
        c.execute("DELETE FROM dispatches WHERE user_id=?", (usuario["id"],))
        c.execute(
            "INSERT INTO dispatches (user_id, item_id, kind, sent_at) "
            "VALUES (?,?,?,?)",
            (usuario["id"], None, "link-pagamento",
             (tempo.agora() - _dt.timedelta(days=dias)
              ).strftime("%Y-%m-%d %H:%M:%S")))


def _ids(ds):
    return [d["user_id"] for d in ds]


# ---------------------------------------------------------------------------
# 1. a cobrança sai — e é isso que não existia
# ---------------------------------------------------------------------------

def test_quem_pediu_o_link_e_nao_pagou_e_cobrado(usuario):
    _pediu_ha(usuario, 2)
    assert usuario["id"] in _ids(scheduler.check_cobranca())


def test_cobra_mesmo_com_o_trial_JA_VENCIDO(usuario):
    """O ponto inteiro. `user_can_receive` devolve False aqui, e era isso
    que fazia a gente parar de servir E de cobrar no mesmo instante."""
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=40)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    assert db.user_can_receive(db.get_user(usuario["id"])) is False
    _pediu_ha(usuario, 3)
    assert usuario["id"] in _ids(scheduler.check_cobranca())


def test_a_mensagem_diz_ha_quantos_dias(usuario):
    _pediu_ha(usuario, 4)
    d = scheduler.check_cobranca()[0]
    assert d["dias_desde_o_pedido"] == 4, d
    assert "*4* dia" in d["message"], d["message"]


def test_o_template_aprovado_monta_as_variaveis(usuario):
    _pediu_ha(usuario, 3)
    d = scheduler.check_cobranca()[0]
    tpl, variaveis = _cat.para_disparo(d)
    assert tpl == "resolveai_cobranca_link"
    assert variaveis == ["Kevin", "3"], variaveis


def test_sem_saber_os_dias_a_cobranca_nao_sai(usuario):
    """"há *0* dias e ainda não vi o pagamento" contradiz a si mesma."""
    d = {"kind": "cobranca-link", "user_id": usuario["id"],
         "user_nome": "Kevin", "item_id": None, "dias_desde_o_pedido": 0}
    assert _cat.para_disparo(d) == (None, [])


# ---------------------------------------------------------------------------
# 2. insiste duas vezes e PARA
# ---------------------------------------------------------------------------

def test_nao_cobra_antes_da_hora(usuario):
    _pediu_ha(usuario, 1)
    assert scheduler.check_cobranca() == []


def test_a_segunda_cobranca_espera_o_intervalo(usuario):
    _pediu_ha(usuario, 2)
    assert scheduler.check_cobranca()
    db.log_dispatch(usuario["id"], "cobranca-link")
    assert scheduler.check_cobranca() == [], "cobrou dois dias seguidos"


def test_para_depois_de_duas(usuario, monkeypatch):
    """A terceira e o caminho pro bloqueio, e quem nao respondeu duas ja
    respondeu."""
    _pediu_ha(usuario, 9)
    with db.get_conn() as c:
        for i in range(scheduler.COBRANCA_MAX):
            c.execute(
                "INSERT INTO dispatches (user_id, item_id, kind, sent_at) "
                "VALUES (?,?,?,?)",
                (usuario["id"], None, "cobranca-link",
                 (tempo.agora() - _dt.timedelta(days=6 - i)
                  ).strftime("%Y-%m-%d %H:%M:%S")))
    assert scheduler.check_cobranca() == []


# ---------------------------------------------------------------------------
# 3. quem pagou, quem saiu e quem nunca pediu ficam de fora
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["ativo", "cancelado", "bloqueado"])
def test_quem_pagou_ou_saiu_nao_e_cobrado(usuario, status):
    _pediu_ha(usuario, 5)
    db.update_user_fields(usuario["id"], status=status)
    assert scheduler.check_cobranca() == []


def test_quem_nunca_pediu_o_link_nao_e_cobrado(usuario):
    with db.get_conn() as c:
        c.execute("DELETE FROM dispatches WHERE user_id=?", (usuario["id"],))
    assert usuario["id"] not in _ids(scheduler.check_cobranca())


def test_aprovar_no_painel_tira_da_fila_na_hora(usuario):
    _pediu_ha(usuario, 5)
    assert scheduler.check_cobranca()
    db.update_user_fields(usuario["id"], status="ativo")
    assert scheduler.check_cobranca() == []


# ---------------------------------------------------------------------------
# 4. o motor não pode quebrar por causa disto
# ---------------------------------------------------------------------------

def test_o_kind_esta_declarado():
    assert "cobranca-link" in scheduler.KINDS_PROATIVOS


def test_a_madrugada_nao_derruba_o_motor(usuario, monkeypatch):
    madrugada = _dt.datetime(2026, 9, 1, 3, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda a=madrugada: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=madrugada: a.date())
    out = scheduler.run_proactive_engine()
    assert out["cobranca_dispatches"] == []
    assert isinstance(out["total"], int)


def test_a_cobranca_entra_no_total_do_motor(usuario, monkeypatch):
    meio_dia = _dt.datetime(2026, 9, 1, 12, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda a=meio_dia: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=meio_dia: a.date())
    _pediu_ha(usuario, 3)
    out = scheduler.run_proactive_engine()
    assert out["cobranca_dispatches"], out
    assert out["total"] >= 1


def test_banco_fora_nao_derruba_a_checagem(usuario, monkeypatch):
    """Fila vazia e o resultado seguro: nao cobrar hoje e recuperavel,
    derrubar o motor proativo inteiro nao e."""
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "get_conn", explode)
    assert db.pediu_link_e_nao_pagou() == []
    assert db.dias_desde_o_pedido_do_link(1) == 0


def test_nao_cobra_dois_dias_seguidos_mesmo_no_prazo(usuario):
    """A trava de intervalo, sozinha.

    Se a primeira cobranca atrasar (sai so no D+6, por exemplo), a regra de
    dias ja esta satisfeita pra segunda — e sem `dispatched_within` ela sairia
    no dia seguinte. Duas cobrancas em 24h e o padrao que a Meta pune.
    """
    _pediu_ha(usuario, 9)
    db.log_dispatch(usuario["id"], "cobranca-link")   # a 1a saiu HOJE
    assert scheduler.check_cobranca() == [], "cobrou de novo no dia seguinte"

    # passados os 2 dias de intervalo, a segunda (e ultima) pode sair
    with db.get_conn() as c:
        c.execute("UPDATE dispatches SET sent_at=? "
                  "WHERE user_id=? AND kind='cobranca-link'",
                  ((tempo.agora() - _dt.timedelta(days=3)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    assert usuario["id"] in _ids(scheduler.check_cobranca())
