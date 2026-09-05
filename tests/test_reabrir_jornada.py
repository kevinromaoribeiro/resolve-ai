# -*- coding: utf-8 -*-
"""Destravar as licoes que ainda vao acontecer, e SO elas.

05/09: cinco pessoas tinham as sete etapas marcadas do trial ORIGINAL,
semanas atras. Com a regua sendo dia -> licao, elas nunca mais receberiam
nada — e a decisao do dono foi que toda a base receba a jornada.

O risco desta operacao e o oposto: destravar tudo faria a pessoa receber
hoje uma licao de um dia que ja passou. Repeticao imediata e o que ela
nao quis.
"""
import datetime as _dt

import db
import tempo
import trial_guiado
import wa_bot


def _no_dia(uid, dia, marcados):
    base = tempo.agora() - _dt.timedelta(days=dia)
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=?, trial_nudges_sent=?, "
                  "status='trial', onboarding_step='done' WHERE id=?",
                  (base.strftime("%Y-%m-%d %H:%M:%S"), marcados, uid))


def _marcas(uid):
    return {x for x in (db.get_user(uid).get("trial_nudges_sent") or ""
                        ).split(",") if x}


def test_so_limpa_licao_de_dia_que_ainda_vem(usuario):
    _no_dia(usuario["id"], 6, "d1,d3,d5,d6,d7,d9,d11")
    db.reabrir_jornada_futura(seco=False)
    assert _marcas(usuario["id"]) == {"d1", "d3", "d5", "d6"}


def test_a_passada_fica_marcada(usuario):
    """Destravar o que ja passou = repeticao imediata."""
    _no_dia(usuario["id"], 6, "d1,d3,d5")
    db.reabrir_jornada_futura(seco=False)
    assert {"d1", "d3", "d5"} <= _marcas(usuario["id"])


def test_seco_nao_escreve_nada(usuario):
    _no_dia(usuario["id"], 6, "d1,d3,d5,d7,d9,d11")
    antes = _marcas(usuario["id"])
    r = db.reabrir_jornada_futura(seco=True)
    assert r["seco"] is True and r["quantas"] == 1
    assert _marcas(usuario["id"]) == antes, "modo seco escreveu"


def test_quem_nao_tem_o_que_destravar_fica_de_fora(usuario):
    _no_dia(usuario["id"], 6, "d1,d3,d5")
    r = db.reabrir_jornada_futura(seco=True)
    assert not [p for p in r["pessoas"] if p["user_id"] == usuario["id"]]


def test_o_plano_diz_o_que_muda(usuario):
    _no_dia(usuario["id"], 6, "d1,d7,d11")
    p = [x for x in db.reabrir_jornada_futura(seco=True)["pessoas"]
         if x["user_id"] == usuario["id"]][0]
    assert p["limpou"] == ["d11", "d7"]
    assert p["mantidos"] == ["d1"]
    assert p["dia"] == 6


def test_depois_de_destravar_a_licao_volta_a_ser_gerada(usuario):
    """A prova de que a operacao serve pra alguma coisa."""
    _no_dia(usuario["id"], 7, "d1,d3,d5,d6,d7,d9,d11")
    with db.get_conn() as c:
        c.execute("UPDATE users SET ultima_interacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=40)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    assert not [d for d in trial_guiado.run_trial_nudges()
                if d["user_id"] == usuario["id"]]
    db.reabrir_jornada_futura(seco=False)
    saiu = [d for d in trial_guiado.run_trial_nudges()
            if d["user_id"] == usuario["id"]]
    assert saiu and saiu[0]["kind"] == "trial_d7", saiu


def test_a_rota_e_seca_sem_confirmo(usuario, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    _no_dia(usuario["id"], 6, "d1,d7,d9,d11")
    antes = _marcas(usuario["id"])
    j = TestClient(wa_bot.app).post("/painel/jornada/reabrir?k=tok",
                                    json={}).json()
    assert j["ok"] and j["seco"] is True
    assert _marcas(usuario["id"]) == antes


def test_a_rota_exige_token(usuario, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    r = TestClient(wa_bot.app).post("/painel/jornada/reabrir?k=errado",
                                    json={"confirmo": True})
    assert r.status_code != 200 or not r.json().get("ok")


def test_o_fechamento_tambem_e_destravado(usuario):
    """`d6_fim` e a UNICA mensagem do trial que pede assinatura.

    Nas pessoas do trial original ela ja estava marcada: elas chegariam ao
    dia 13 sem receber justamente a mensagem que leva a decisao. A chave
    nao segue o padrao `d<numero>`, entao nao caia na conta das licoes —
    foi o modo seco, no plano de producao, que mostrou isso.
    """
    _no_dia(usuario["id"], 6, "d1,d3,d5,d6_fim,d7")
    db.reabrir_jornada_futura(seco=False)
    assert "d6_fim" not in _marcas(usuario["id"])
    assert {"d1", "d3", "d5"} <= _marcas(usuario["id"])


def test_quem_ja_passou_do_fechamento_nao_reabre(usuario):
    """Depois do dia 13 destravar o fechamento seria mandar de novo."""
    _no_dia(usuario["id"], trial_guiado.DIA_FECHAMENTO + 2, "d6_fim")
    db.reabrir_jornada_futura(seco=False)
    assert "d6_fim" in _marcas(usuario["id"])
