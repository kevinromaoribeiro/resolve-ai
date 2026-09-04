# -*- coding: utf-8 -*-
"""Uma analise por conselheiro por semana, e o limite e do servidor.

Decisao do dono depois da primeira rodada: o modelo bom custa centavos por
analise em vez de fracoes, e cinco por semana e gasto previsivel — cinco
por dia, nao.

A trava fica no servidor pela mesma razao da trava do disparo em lote: quem
clica de novo e justamente quem nao viu o aviso. O `forcar` do botao NAO
passa por cima, senao o limite viraria sugestao.
"""
import datetime as _dt
import json

import conselho
import db
import tempo
import wa_bot


def _cli(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    return TestClient(wa_bot.app)


def _guardar(tipo, texto, dias_atras=0.0):
    quando = (tempo.agora() - _dt.timedelta(days=dias_atras)
              ).isoformat(timespec="seconds")
    db.set_setting("conselho_" + tipo,
                   json.dumps({"texto": texto, "quando": quando}))
    return quando


def _pedir(c, tipo="crescimento", **extra):
    corpo = {"tipo": tipo}
    corpo.update(extra)
    return c.post("/painel/conselho?k=tok", json=corpo).json()


# --- a trava ----------------------------------------------------------

def test_dentro_da_semana_devolve_a_guardada_sem_gastar(usuario, monkeypatch):
    chamou = []
    monkeypatch.setattr(conselho, "pedir",
                        lambda *a, **k: chamou.append(1) or (True, "novo"))
    _guardar("crescimento", "a análise da semana passada", dias_atras=2)
    j = _pedir(_cli(monkeypatch))
    assert j["ok"] is True
    assert j["texto"] == "a análise da semana passada"
    assert j["reaproveitado"] is True
    assert not chamou, "gastou uma análise dentro da semana"


def test_o_forcar_do_botao_nao_passa_por_cima(usuario, monkeypatch):
    """Se passasse, o limite viraria sugestão."""
    chamou = []
    monkeypatch.setattr(conselho, "pedir",
                        lambda *a, **k: chamou.append(1) or (True, "novo"))
    _guardar("crescimento", "guardada", dias_atras=1)
    j = _pedir(_cli(monkeypatch), forcar=True)
    assert j["texto"] == "guardada"
    assert not chamou


def test_depois_de_uma_semana_libera(usuario, monkeypatch):
    chamou = []
    monkeypatch.setattr(
        conselho, "pedir",
        lambda *a, **k: chamou.append(1) or (True, "análise nova"))
    _guardar("crescimento", "velha", dias_atras=conselho.LIMITE_DIAS + 0.1)
    j = _pedir(_cli(monkeypatch))
    assert j["ok"] is True
    assert j["texto"] == "análise nova"
    assert chamou


def test_a_primeira_analise_nao_espera(usuario, monkeypatch):
    db.set_setting("conselho_marketing", "")
    monkeypatch.setattr(conselho, "pedir", lambda *a, **k: (True, "primeira"))
    j = _pedir(_cli(monkeypatch), tipo="marketing")
    assert j["texto"] == "primeira"


def test_a_trava_e_por_conselheiro(usuario, monkeypatch):
    """Analisar crescimento nao pode travar o de preco."""
    monkeypatch.setattr(conselho, "pedir", lambda *a, **k: (True, "do preço"))
    _guardar("crescimento", "recente", dias_atras=1)
    db.set_setting("conselho_preco", "")
    j = _pedir(_cli(monkeypatch), tipo="preco")
    assert j["texto"] == "do preço"


def test_a_resposta_diz_quantos_dias_faltam(usuario, monkeypatch):
    """Sem isso o dono clica todo dia sem entender por que nao muda."""
    _guardar("crescimento", "guardada", dias_atras=2)
    j = _pedir(_cli(monkeypatch))
    assert 0 < j["faltam_dias"] <= conselho.LIMITE_DIAS - 1.9


# --- a conta dos dias -------------------------------------------------

def test_sem_analise_nenhuma_nao_falta_nada():
    assert conselho.falta_para_liberar("", tempo.agora()) == 0.0


def test_carimbo_ilegivel_nao_prende_pra_sempre():
    """Data quebrada nao pode deixar o dono sem conselheiro."""
    assert conselho.falta_para_liberar("nao é data", tempo.agora()) == 0.0


def test_a_conta_bate_com_o_limite():
    agora = tempo.agora()
    ontem = (agora - _dt.timedelta(days=1)).isoformat(timespec="seconds")
    falta = conselho.falta_para_liberar(ontem, agora)
    assert abs(falta - (conselho.LIMITE_DIAS - 1)) < 0.01


# --- o modelo ---------------------------------------------------------

def test_usa_o_modelo_bom_e_nao_o_do_bot(monkeypatch):
    usados = []

    def _falso(model=None, **kw):
        usados.append(model)
        raise RuntimeError("sem rede no teste")

    import sys
    import types
    mod = types.ModuleType("litellm")
    mod.completion = _falso
    monkeypatch.setitem(sys.modules, "litellm", mod)
    conselho.pedir("crescimento", {}, reserva="gpt-4o-mini")
    assert usados[0] == conselho.MODELO
    assert conselho.MODELO != "gpt-4o-mini"


def test_se_o_modelo_bom_falhar_cai_na_reserva(monkeypatch):
    """Botao sem resposta depois de gastar a análise seria o pior desfecho."""
    usados = []

    def _falso(model=None, **kw):
        usados.append(model)
        if model == conselho.MODELO:
            raise RuntimeError("indisponível")

        class R:
            class _C:
                class _M:
                    content = "veio da reserva"
                message = _M()
            choices = [_C()]
        return R()

    import sys
    import types
    mod = types.ModuleType("litellm")
    mod.completion = _falso
    monkeypatch.setitem(sys.modules, "litellm", mod)
    ok, texto = conselho.pedir("crescimento", {}, reserva="gpt-4o-mini")
    assert ok is True and texto == "veio da reserva"
    assert usados == [conselho.MODELO, "gpt-4o-mini"]


def test_resposta_vazia_nao_vira_analise(monkeypatch):
    """A tela diria "analisado hoje" com nada dentro."""
    import sys
    import types

    class R:
        class _C:
            class _M:
                content = "   "
            message = _M()
        choices = [_C()]

    mod = types.ModuleType("litellm")
    mod.completion = lambda **kw: R()
    monkeypatch.setitem(sys.modules, "litellm", mod)
    ok, _t = conselho.pedir("crescimento", {})
    assert ok is False
