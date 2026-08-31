# -*- coding: utf-8 -*-
"""A regra anti-spam, num lugar só (M8.0).

O Kevin: "não é pra encher a pessoa de mensagens... pra não virar spam, de
tanto que enche o saco".

Cada feature tinha seu próprio teto (convite 1x, cobrança 2x, empurrão 1x,
anti-churn 3x). Somados, a mesma pessoa recebia várias coisas nossas na
mesma semana — cada uma "dentro do limite dela". Teto por feature não é
teto: é a SOMA que a pessoa sente, e é a soma que a Meta lê.
"""
import datetime as _dt

import pytest

import db
import scheduler
import tempo
import trial_guiado
import wa_bot
from conftest import TELEFONE, responder


@pytest.fixture
def sem_espera(monkeypatch):
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(wa_bot, "DISPATCH_MAX_PER_CYCLE", 10)


def _d(usuario, kind, msg="oi"):
    return {"user_id": usuario["id"], "user_nome": "Kevin",
            "telefone": TELEFONE, "item_id": None, "kind": kind,
            "message": msg, "quando": "31/08"}


def _rodar(monkeypatch, disparos):
    db.log_message(None, TELEFONE, "in", "texto", "oi")
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(txt)
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(scheduler, "run_proactive_engine",
                        lambda **k: {"executed_at": "x",
                                     "total": len(disparos),
                                     "due_dispatches": disparos})
    wa_bot.dispatch_proactive()
    return enviados


# ---------------------------------------------------------------------------
# 1. a soma, não o teto de cada uma
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("primeira,segunda", [
    ("podcast-convite", "anti-churn"),
    ("anti-churn", "winback"),
    ("reativacao", "podcast"),
    ("winback", "podcast-convite"),
])
def test_duas_cortesias_diferentes_na_mesma_semana_nao_saem(
        usuario, sem_espera, monkeypatch, primeira, segunda):
    """O buraco que a regra fecha: cada feature achava que era a primeira."""
    db.log_dispatch(usuario["id"], primeira)
    assert _rodar(monkeypatch, [_d(usuario, segunda)]) == []


def test_passada_a_semana_a_cortesia_volta(usuario, sem_espera, monkeypatch):
    db.log_dispatch(usuario["id"], "podcast-convite")
    with db.get_conn() as c:
        c.execute("UPDATE dispatches SET sent_at=? WHERE user_id=?",
                  ((tempo.agora() - _dt.timedelta(days=8)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    assert _rodar(monkeypatch, [_d(usuario, "anti-churn")])


# ---------------------------------------------------------------------------
# 2. o que ELA pediu não entra na conta — e é a metade que importa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["vencimento", "hora", "vencido", "resumo",
                                  "retorno", "gastos", "trial-ending",
                                  "cobranca-link"])
def test_o_produto_nao_tem_teto_de_cortesia(usuario, sem_espera, monkeypatch,
                                            kind):
    """Ela PAGA pelo lembrete. E a cobranca e do link que ela mesma pediu."""
    db.log_dispatch(usuario["id"], "podcast-convite")
    assert _rodar(monkeypatch, [_d(usuario, kind, "sua conta vence")])


def test_a_regua_do_trial_nao_entra_no_teto():
    """A jornada de ativação é o que faz a pessoa ver valor nos 14 dias —
    capá-la a 1x por semana seria matar a conversão pra evitar spam que ela
    não sente: é 1 toque por dia NO MÁXIMO, e só quando a pessoa esfria."""
    regua = {"trial_d%d" % n for n in range(1, 14)}
    assert not (regua & wa_bot.KINDS_DE_CORTESIA)


def test_a_regua_do_trial_sai_mesmo_com_cortesia_recente(usuario, sem_espera,
                                                         monkeypatch):
    db.log_dispatch(usuario["id"], "podcast-convite")
    assert _rodar(monkeypatch, [_d(usuario, "trial_d3", "dica rapida")])


# ---------------------------------------------------------------------------
# 3. "agora não" vale pra tudo
# ---------------------------------------------------------------------------

def test_agora_nao_da_espaco_de_toda_oferta(usuario, sem_espera, monkeypatch):
    """Pedir espaço uma vez e ser atendido só naquela feature é o oposto de
    respeitar o pedido."""
    db.update_user_fields(usuario["id"], podcast_nicho=None)
    responder("Agora não")
    assert _rodar(monkeypatch, [_d(usuario, "anti-churn")]) == []
    assert _rodar(monkeypatch, [_d(usuario, "podcast-convite")]) == []


def test_mas_o_lembrete_continua_depois_do_agora_nao(usuario, sem_espera,
                                                     monkeypatch):
    db.update_user_fields(usuario["id"], podcast_nicho=None)
    responder("Agora não")
    assert _rodar(monkeypatch, [_d(usuario, "vencimento", "sua conta vence")])


# ---------------------------------------------------------------------------
# 4. a regra não pode virar o próprio defeito
# ---------------------------------------------------------------------------

def test_banco_fora_adia_a_cortesia_e_nao_o_produto(usuario, sem_espera,
                                                    monkeypatch):
    """Na dúvida adia o convite (recuperável) e entrega o lembrete."""
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "dispatched_within", explode)
    assert wa_bot._cortesia_recente(usuario["id"]) is True
    monkeypatch.undo()
    assert _rodar(monkeypatch, [_d(usuario, "vencimento", "vence amanha")])


def test_disparo_sem_user_id_nao_quebra():
    assert wa_bot._cortesia_recente(None) is False
