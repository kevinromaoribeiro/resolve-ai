# -*- coding: utf-8 -*-
"""O disjuntor do número (M7.3).

Em 31/08/2026 o número chegou a 5.5x proativas por resposta, com o vermelho
começando em 3.0 — quase o dobro. É a assinatura de "broadcaster", e é
exatamente o que restringiu este número duas vezes.

A única defesa que existia era uma SUGESTÃO no relatório do dono. O motor
continuava mandando. Aviso que depende de alguém ler não é proteção.
"""
import pytest

import db
import scheduler
import wa_bot
from conftest import TELEFONE, responder


@pytest.fixture
def sem_espera(monkeypatch):
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(wa_bot, "DISPATCH_MAX_PER_CYCLE", 10)


def _vermelho(monkeypatch, sim=True):
    monkeypatch.setattr(db, "pulso_envio",
                        lambda *a, **k: {"risco": "🔴 alto" if sim
                                         else "🟢 ok", "motivo": "x"})


def _disparo(usuario, kind, msg="oi"):
    return {"user_id": usuario["id"], "user_nome": "Kevin",
            "telefone": TELEFONE, "item_id": None, "kind": kind,
            "message": msg, "quando": "31/08"}


def _rodar(monkeypatch, disparos):
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(kw.get("template") or "livre")
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(scheduler, "run_proactive_engine",
                        lambda **k: {"executed_at": "x",
                                     "total": len(disparos),
                                     "due_dispatches": disparos})
    db.log_message(None, TELEFONE, "in", "texto", "oi")
    wa_bot.dispatch_proactive()
    return enviados


# ---------------------------------------------------------------------------
# 1. no vermelho, a cortesia para
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(wa_bot.KINDS_DE_CORTESIA))
def test_cortesia_nao_sai_com_o_numero_no_vermelho(usuario, sem_espera,
                                                   monkeypatch, kind):
    _vermelho(monkeypatch)
    assert _rodar(monkeypatch, [_disparo(usuario, kind)]) == []


# ---------------------------------------------------------------------------
# 2. mas o PRODUTO continua — e essa é a metade que importa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["vencimento", "hora", "vencido", "resumo",
                                  "retorno", "gastos", "trial-ending"])
def test_o_que_a_pessoa_pediu_continua_saindo_no_vermelho(usuario, sem_espera,
                                                          monkeypatch, kind):
    """Cortar lembrete pra proteger o número é proteger o canal matando o
    produto que o canal existe pra entregar. A pessoa PAGA por isso."""
    _vermelho(monkeypatch)
    assert _rodar(monkeypatch, [_disparo(usuario, kind, "sua conta vence")])


def test_no_vermelho_o_lembrete_passa_e_o_convite_fica(usuario, sem_espera,
                                                       monkeypatch):
    _vermelho(monkeypatch)
    enviados = _rodar(monkeypatch, [
        _disparo(usuario, "vencimento", "sua conta vence amanhã"),
        _disparo(usuario, "reativacao", "oi, volta")])
    assert len(enviados) == 1, enviados
    assert wa_bot.ULTIMO_CICLO.get("cortesia_cortada") == 1


# ---------------------------------------------------------------------------
# 3. no verde, tudo volta — sem deploy e sem ninguém religar
# ---------------------------------------------------------------------------

def test_no_verde_a_cortesia_volta_sozinha(usuario, sem_espera, monkeypatch):
    _vermelho(monkeypatch, sim=False)
    assert _rodar(monkeypatch, [_disparo(usuario, "reativacao", "oi, volta")])


# ---------------------------------------------------------------------------
# 4. o disjuntor não pode virar o próprio defeito
# ---------------------------------------------------------------------------

def test_erro_ao_ler_o_risco_nao_cala_o_produto(usuario, sem_espera,
                                                monkeypatch):
    """Na dúvida o disjuntor NÃO corta: um erro aqui calaria lembrete de
    vencimento, e aí a proteção custa mais que o risco."""
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "pulso_envio", explode)
    assert wa_bot._numero_no_vermelho() is False
    assert _rodar(monkeypatch, [_disparo(usuario, "vencimento", "vence")])


def test_o_health_diz_se_a_cortesia_esta_pausada(usuario, monkeypatch):
    from fastapi.testclient import TestClient
    c = TestClient(wa_bot.app)
    _vermelho(monkeypatch)
    assert c.get("/health").json()["cortesia_pausada"] is True
    _vermelho(monkeypatch, sim=False)
    assert c.get("/health").json()["cortesia_pausada"] is False


def test_a_lista_de_cortesia_nao_inclui_o_produto():
    """Contrato: o critério é "quem pediu?", não "é importante?"."""
    produto = {"vencimento", "hora", "vencido", "resumo", "retorno",
               "gastos", "trial-ending", "1-click-buy", "arquivado"}
    assert not (wa_bot.KINDS_DE_CORTESIA & produto)
    # e todo kind de cortesia é um kind que o motor realmente emite
    assert wa_bot.KINDS_DE_CORTESIA <= (scheduler.KINDS_PROATIVOS |
                                        {"reativacao"})
