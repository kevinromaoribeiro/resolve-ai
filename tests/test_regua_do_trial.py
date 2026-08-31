# -*- coding: utf-8 -*-
"""A régua de engajamento do trial (M7.8).

O Kevin: "todos os dias durante o trial como o cliente está sendo impactado?
eu não recebo, e não sei se os clientes estão".

Medido: ninguém. Nunca. Dois defeitos independentes, cada um bastava sozinho
— e os dois eram invisíveis, porque o sintoma é ausência de mensagem.
"""
import datetime as _dt

import pytest

import db
import scheduler
import tempo
import trial_guiado
import wa_bot
from conftest import TELEFONE


def _no_dia(usuario, dia, calado_ha=20, passo=None):
    """Cliente real: entrou há `dia` dias, calado há `calado_ha` horas."""
    db.update_user_fields(usuario["id"], onboarding_step=passo,
                          status="trial")
    # ZERA A REGUA. `trial_nudges_sent` acumula e a fixture `usuario` do
    # conftest nao limpa — sem isto, um teste consome o "d2" e o proximo
    # mede o consumo do anterior em vez de medir a regua.
    with db.get_conn() as _c:
        _c.execute("UPDATE users SET trial_nudges_sent=NULL WHERE id=?",
                   (usuario["id"],))
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=?, data_criacao=?, "
                  "ultima_interacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=dia)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   (tempo.agora() - _dt.timedelta(days=dia)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   (tempo.agora() - _dt.timedelta(hours=calado_ha)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   usuario["id"]))


def _meus(usuario):
    return [d for d in trial_guiado.run_trial_nudges()
            if d["user_id"] == usuario["id"]]


# ---------------------------------------------------------------------------
# 1. o defeito que esvaziava a fila
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("passo", [None, "done"])
def test_quem_terminou_o_onboarding_entra_na_fila(usuario, passo):
    """Quem termina fica com `None`, não com "done" (ver jornada.py). A
    comparação estrita excluía TODO cliente real — e os outros quatro
    lugares do código sempre usaram `(x or "done")`."""
    _no_dia(usuario, dia=2, passo=passo)
    assert usuario["id"] in [u["id"] for u in db.active_trial_users()], passo


def test_quem_ainda_esta_no_onboarding_fica_de_fora(usuario):
    """Nudge no meio do cadastro é atropelar quem ainda está entrando."""
    _no_dia(usuario, dia=2, passo="lgpd_landing")
    assert usuario["id"] not in [u["id"] for u in db.active_trial_users()]


# ---------------------------------------------------------------------------
# 2. o defeito que tornava a régua impossível
# ---------------------------------------------------------------------------

def test_o_nudge_cabe_dentro_da_janela_de_24h():
    """A INVARIANTE QUE ESTAVA QUEBRADA.

    O nudge sai como texto livre (nenhum `trial_d*` tem template, de
    propósito). Texto livre só passa pra quem falou nas últimas 24h. Se o
    gatilho exigir 24h+ de silêncio, as duas condições se excluem e a
    mensagem é gerada e descartada — sem erro e sem log.
    """
    assert trial_guiado.INACTIVE_HOURS < 24, (
        "nudge exige %sh de silencio, mas texto livre so passa dentro de "
        "24h — a regua fica impossivel" % trial_guiado.INACTIVE_HOURS)


def test_o_nudge_SAI_de_verdade_pra_quem_esfriou(usuario, monkeypatch):
    """Ponta a ponta: gerado E entregue. Antes ele morria na poda."""
    _no_dia(usuario, dia=2, calado_ha=20)
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    with db.get_conn() as c:      # a entrada foi ha 20h, nao agora
        c.execute("UPDATE msg_log SET ts=? WHERE user_id=?",
                  ((tempo.agora() - _dt.timedelta(hours=20)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    ds = _meus(usuario)
    assert ds, "nao gerou nudge nenhum"

    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(txt)
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(scheduler, "run_proactive_engine",
                        lambda **k: {"executed_at": "x", "total": len(ds),
                                     "guided_dispatches": ds})
    wa_bot.dispatch_proactive()
    assert enviados, "gerou o nudge e ele morreu na poda — de novo"


def test_quem_acabou_de_falar_nao_e_incomodado(usuario):
    """"Nudge é pra quem esfriou. Quem já está engajado precisa de espaço."
    — a filosofia escrita no topo do próprio arquivo."""
    _no_dia(usuario, dia=2, calado_ha=1)
    assert _meus(usuario) == []


# ---------------------------------------------------------------------------
# 3. a régua cobre os dias que promete
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dia", [1, 2, 3, 4, 5, 7, 9, 11, 12])
def test_cada_dia_da_regua_produz_seu_toque(usuario, dia):
    _no_dia(usuario, dia=dia, calado_ha=20)
    ds = _meus(usuario)
    assert ds, "dia %d nao gerou nada" % dia
    assert ds[0]["message"].strip(), ds


def test_o_fechamento_sai_no_fim_do_trial(usuario):
    """O D13 é o que fecha o funil: prova de valor + link de pagamento."""
    _no_dia(usuario, dia=trial_guiado.DIA_FECHAMENTO, calado_ha=20)
    ds = _meus(usuario)
    assert ds, "o fechamento do trial nao saiu"
    assert "19,90" in ds[0]["message"], ds[0]["message"]


def test_ninguem_recebe_o_mesmo_toque_duas_vezes(usuario):
    _no_dia(usuario, dia=2, calado_ha=20)
    assert _meus(usuario)
    db.mark_nudge_sent(usuario["id"], "d2")
    assert _meus(usuario) == []


def test_um_toque_por_dia_no_maximo(usuario):
    _no_dia(usuario, dia=2, calado_ha=20)
    assert len(_meus(usuario)) <= 1
