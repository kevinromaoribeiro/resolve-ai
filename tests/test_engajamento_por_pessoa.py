# -*- coding: utf-8 -*-
"""Silêncio por PESSOA (M7.4) — e não freio de operação.

O Kevin: "não é pra frear nossa operação... não quero entregar um produto
meia boca, ou limitado, tudo o que construímos tem que estar disponível para
o usuário". Ele está certo, e o disjuntor global da M7.3 fazia exatamente o
contrário: desligava features pra base inteira por causa de uma média.

O que a Meta pune não é volume, é falar com quem não ouve. Cem clientes que
respondem podem receber tudo, todo dia, sem risco — cada resposta deles
entra do outro lado da conta.
"""
import datetime as _dt

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


def _disparo(usuario, kind, msg="oi"):
    return {"user_id": usuario["id"], "user_nome": "Kevin",
            "telefone": TELEFONE, "item_id": None, "kind": kind,
            "message": msg, "quando": "31/08"}


def _rodar(monkeypatch, disparos):
    # janela de 24h aberta: sem isto o `_tem_como_sair` poda os kinds que
    # nao tem template e o teste mediria a poda, nao a regra.
    db.log_message(None, TELEFONE, "in", "texto", "oi")
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(kw.get("kind") or "x")
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(scheduler, "run_proactive_engine",
                        lambda **k: {"executed_at": "x",
                                     "total": len(disparos),
                                     "due_dispatches": disparos})
    wa_bot.dispatch_proactive()
    return enviados


def _calou(usuario, quantas):
    """A pessoa recebeu N proativas, em dias passados, e nao respondeu.

    ONTEM PRA TRAS de proposito: com tudo hoje, o teto diario de proativas
    por usuario cortaria o envio e o teste mediria o teto, nao o silencio.
    E limpa os disparos antes, senao uma chamada soma na anterior.
    """
    with db.get_conn() as c:
        c.execute("DELETE FROM msg_log WHERE user_id=?", (usuario["id"],))
        c.execute("DELETE FROM dispatches WHERE user_id=?", (usuario["id"],))
        for i in range(quantas):
            c.execute(
                "INSERT INTO dispatches (user_id, item_id, kind, sent_at) "
                "VALUES (?,?,?,?)",
                (usuario["id"], None, "vencimento",
                 (_dt.datetime(2026, 8, 1) + _dt.timedelta(minutes=i)
                  ).isoformat(timespec="seconds")))


# ---------------------------------------------------------------------------
# 1. quem conversa recebe TUDO — sem teto, sem espera
# ---------------------------------------------------------------------------

def test_quem_responde_recebe_o_produto_inteiro(usuario, sem_espera,
                                                monkeypatch):
    """A regra nunca alcança quem está usando o bot. Escalar com cliente
    engajado não tem risco: a resposta dele entra do outro lado da conta."""
    _calou(usuario, 20)
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    # o template do podcast promete "seu resumo de *X*": sem assunto
    # escolhido ele nao sai, e com razao.
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")

    # UM DE CADA VEZ, de proposito: mandar os seis no mesmo ciclo esbarraria
    # no teto de 6 proativas por pessoa por dia — que e outra protecao, e
    # intencional. Nenhum cliente real recebe seis convites num dia.
    for kind in sorted(wa_bot.KINDS_DE_CORTESIA):
        with db.get_conn() as c:
            c.execute("DELETE FROM dispatches WHERE user_id=?",
                      (usuario["id"],))
        assert _rodar(monkeypatch, [_disparo(usuario, kind)]), kind
    assert wa_bot._parou_de_ouvir(usuario["id"]) is False


def test_uma_resposta_zera_a_contagem_na_hora(usuario):
    _calou(usuario, 9)
    assert wa_bot._parou_de_ouvir(usuario["id"]) is True
    db.log_message(usuario["id"], TELEFONE, "in", "texto", "oi")
    assert wa_bot._parou_de_ouvir(usuario["id"]) is False


def test_o_limite_e_generoso(usuario):
    """Quem lê e não responde ainda recebe cinco antes de a gente entender."""
    for n in range(wa_bot.SILENCIO_ATE_PARAR):
        _calou(usuario, n)
        assert wa_bot._parou_de_ouvir(usuario["id"]) is False, n


# ---------------------------------------------------------------------------
# 2. quem sumiu: para de ouvir convite, continua recebendo o que pediu
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(wa_bot.KINDS_DE_CORTESIA))
def test_convite_nao_persegue_quem_nunca_responde(usuario, sem_espera,
                                                  monkeypatch, kind):
    _calou(usuario, 9)
    assert _rodar(monkeypatch, [_disparo(usuario, kind)]) == []


@pytest.mark.parametrize("kind", ["vencimento", "hora", "vencido", "resumo",
                                  "retorno", "gastos", "trial-ending"])
def test_o_que_ela_pediu_sai_mesmo_calada(usuario, sem_espera, monkeypatch,
                                          kind):
    """Ela PAGA pelo lembrete. Cortar isso pra proteger o número seria
    proteger o canal matando o produto que o canal existe pra entregar."""
    _calou(usuario, 30)
    assert _rodar(monkeypatch, [_disparo(usuario, kind, "sua conta vence")])


def test_no_mesmo_ciclo_o_lembrete_passa_e_o_convite_fica(usuario, sem_espera,
                                                          monkeypatch):
    _calou(usuario, 9)
    enviados = _rodar(monkeypatch, [
        _disparo(usuario, "vencimento", "sua conta vence amanhã"),
        _disparo(usuario, "reativacao", "oi, volta")])
    assert len(enviados) == 1, enviados
    assert wa_bot.ULTIMO_CICLO.get("convite_adiado") == 1


# ---------------------------------------------------------------------------
# 3. é POR PESSOA — o silêncio de um não pune o outro
# ---------------------------------------------------------------------------

def test_quem_sumiu_nao_tira_o_podcast_de_quem_esta_ativo(usuario,
                                                          sem_espera,
                                                          monkeypatch):
    """O DEFEITO DA M7.3, que o Kevin recusou com razão: uma média da base
    desligava feature pra todo mundo."""
    _calou(usuario, 30)                       # este sumiu
    ativo = db.create_user(nome="Ativa", telefone="5511955550000")
    db.update_user_fields(ativo, status="trial")
    db.log_message(ativo, "5511955550000", "in", "texto", "oi")
    try:
        enviados = _rodar(monkeypatch, [
            _disparo(usuario, "podcast", "seu resumo"),
            {"user_id": ativo, "user_nome": "Ativa",
             "telefone": "5511955550000", "item_id": None,
             "kind": "podcast", "message": "seu resumo", "quando": "31/08"}])
        assert len(enviados) == 1, "o silêncio de um puniu o outro"
    finally:
        db.delete_user(ativo)


# ---------------------------------------------------------------------------
# 4. a regra não pode virar o próprio defeito
# ---------------------------------------------------------------------------

def test_banco_fora_nao_cala_ninguem(usuario, sem_espera, monkeypatch):
    """Na dúvida entrega. Erro de leitura não pode silenciar cliente."""
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "proativas_sem_resposta", explode)
    assert wa_bot._parou_de_ouvir(1) is False
    assert _rodar(monkeypatch, [_disparo(usuario, "podcast", "seu resumo")])


def test_disparo_sem_user_id_nao_quebra(usuario, sem_espera, monkeypatch):
    assert wa_bot._parou_de_ouvir(None) is False


def test_o_health_conta_quem_sumiu_sem_dado_pessoal(usuario, monkeypatch):
    from fastapi.testclient import TestClient
    _calou(usuario, 9)
    v = TestClient(wa_bot.app).get("/health").json()["sem_responder"]
    assert isinstance(v, int) and v >= 1


def test_a_regra_so_alcanca_o_que_a_gente_puxa():
    """Contrato: o critério é "quem pediu?", não "é importante?"."""
    produto = {"vencimento", "hora", "vencido", "resumo", "retorno",
               "gastos", "trial-ending", "1-click-buy", "arquivado"}
    assert not (wa_bot.KINDS_DE_CORTESIA & produto)
