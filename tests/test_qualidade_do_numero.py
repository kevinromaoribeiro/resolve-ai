# -*- coding: utf-8 -*-
"""A nota que a Meta da pro numero, lida da fonte.

O painel tinha um medidor PROPRIO de risco (proativas por resposta), com um
corte de 3.0x que fomos nos que escolhemos. A Meta nao olha essa razao: ela
olha bloqueio e denuncia, e o resultado e o `quality_rating`. Mostrar so o
nosso e decidir com o termometro errado — e o medo aqui e concreto, o
numero ja foi restringido duas vezes.

Nenhum destes testes fala com a Meta de verdade. O que se testa e o que
acontece quando ela responde bem, quando responde mal, e quando nao
responde — que sao os tres casos em que o card precisa nao mentir.
"""
import pytest

import meta_cloud


class _Resp:
    def __init__(self, status=200, corpo=None):
        self.status_code = status
        self._corpo = corpo if corpo is not None else {}
        # O corpo de erro da Meta as vezes ecoa o que foi enviado. Poe algo
        # sensivel aqui de proposito, pra provar que nao vaza.
        self.text = "erro com TOKEN_SECRETO_XYZ dentro"

    def json(self):
        return self._corpo


@pytest.fixture(autouse=True)
def _cache_limpo(monkeypatch):
    """Cache de modulo vaza entre testes e faz um mascarar o outro."""
    meta_cloud._QUALIDADE_CACHE.update({"quando": None, "dados": None})
    monkeypatch.setattr(meta_cloud, "META_TOKEN", "tok-de-mentira")
    monkeypatch.setattr(meta_cloud, "PHONE_NUMBER_ID", "123456")
    yield
    meta_cloud._QUALIDADE_CACHE.update({"quando": None, "dados": None})


def _responde(monkeypatch, resp, contador=None):
    import httpx

    def _get(url, **kw):
        if contador is not None:
            contador.append(url)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(httpx, "get", _get)


# --- o caminho feliz, que nenhum teste exercitava --------------------

def test_le_a_nota_e_o_limite(monkeypatch):
    _responde(monkeypatch, _Resp(200, {
        "quality_rating": "GREEN", "messaging_limit_tier": "TIER_1K",
        "name_status": "APPROVED", "verified_name": "Resolve AI",
        "display_phone_number": "+55 11 99999-9999"}))
    d = meta_cloud.qualidade_do_numero()
    assert d["ok"] is True
    assert d["nota"] == "GREEN" and "alta" in d["luz"]
    assert d["limite"] == "1K", d["limite"]
    assert d["status_do_nome"] == "aprovado"


@pytest.mark.parametrize("nota,esperado", [
    ("GREEN", "alta"), ("YELLOW", "media"), ("RED", "baixa"),
    ("UNKNOWN", "sem nota"),
])
def test_cada_nota_tem_leitura_em_portugues(monkeypatch, nota, esperado):
    _responde(monkeypatch, _Resp(200, {"quality_rating": nota}))
    d = meta_cloud.qualidade_do_numero()
    assert esperado in d["luz"], d
    assert d["leitura"].strip()


def test_a_leitura_do_vermelho_nao_ameniza(monkeypatch):
    """Eufemismo aqui custa o numero."""
    _responde(monkeypatch, _Resp(200, {"quality_rating": "RED"}))
    d = meta_cloud.qualidade_do_numero()
    assert "bloquear" in d["leitura"].lower()


def test_nota_desconhecida_nao_quebra(monkeypatch):
    _responde(monkeypatch, _Resp(200, {"quality_rating": "ROXO"}))
    assert meta_cloud.qualidade_do_numero()["ok"] is True


def test_limite_ausente_nao_quebra(monkeypatch):
    _responde(monkeypatch, _Resp(200, {"quality_rating": "GREEN"}))
    assert meta_cloud.qualidade_do_numero()["limite"] == ""


# --- quando a Meta responde mal ---------------------------------------

def test_token_recusado_diz_o_que_e(monkeypatch):
    """401 e token vencido, e o dono precisa saber pra renovar."""
    _responde(monkeypatch, _Resp(401))
    d = meta_cloud.qualidade_do_numero()
    assert d["ok"] is False
    assert "token" in d["erro"].lower()


def test_o_corpo_do_erro_nao_vaza(monkeypatch):
    """A Meta as vezes ecoa o que foi enviado. O token vive no header."""
    _responde(monkeypatch, _Resp(500))
    d = meta_cloud.qualidade_do_numero()
    assert "TOKEN_SECRETO_XYZ" not in str(d)


def test_meta_fora_nao_levanta(monkeypatch):
    import httpx
    _responde(monkeypatch, httpx.ConnectError("sem rede"))
    d = meta_cloud.qualidade_do_numero()
    assert d["ok"] is False


def test_sem_credencial_nem_tenta_a_rede(monkeypatch):
    chamou = []
    _responde(monkeypatch, _Resp(200, {}), contador=chamou)
    monkeypatch.setattr(meta_cloud, "META_TOKEN", "")
    d = meta_cloud.qualidade_do_numero()
    assert d["ok"] is False and not chamou


# --- o campo que some -------------------------------------------------

def test_campo_ausente_nao_vira_sem_volume(monkeypatch):
    """"A Meta ainda nao tem volume" e uma frase tranquilizadora.

    Se o campo sumir por mudanca de contrato da Meta, dizer isso seria
    acalmar o dono com uma explicacao errada — justamente sobre a coisa
    que ele tem medo de nao ver a tempo.
    """
    _responde(monkeypatch, _Resp(200, {"messaging_limit_tier": "TIER_1K"}))
    d = meta_cloud.qualidade_do_numero()
    assert d["ok"] is False
    assert "campo" in d["erro"].lower()


def test_nota_vazia_e_sem_volume_de_verdade(monkeypatch):
    """Campo presente e vazio E "sem volume". A distincao importa."""
    _responde(monkeypatch, _Resp(200, {"quality_rating": None}))
    d = meta_cloud.qualidade_do_numero()
    assert d["ok"] is True and d["nota"] == "UNKNOWN"


# --- o cache ----------------------------------------------------------

def test_sucesso_e_lido_uma_vez_so(monkeypatch):
    chamou = []
    _responde(monkeypatch, _Resp(200, {"quality_rating": "GREEN"}), chamou)
    meta_cloud.qualidade_do_numero()
    meta_cloud.qualidade_do_numero()
    meta_cloud.qualidade_do_numero()
    assert len(chamou) == 1, chamou


def test_a_falha_tambem_e_cacheada(monkeypatch):
    """Sem isto, a Meta fora vira 12s de espera em TODO refresh do painel.

    E o painel se redesenha a cada 20 segundos, indefinidamente, prendendo
    uma thread do pool a cada vez.
    """
    chamou = []
    _responde(monkeypatch, _Resp(503), chamou)
    meta_cloud.qualidade_do_numero()
    meta_cloud.qualidade_do_numero()
    assert len(chamou) == 1, chamou


def test_a_falha_vale_por_menos_tempo_que_o_sucesso(monkeypatch):
    """Token renovado tem que voltar a aparecer em um minuto, nao em 15."""
    assert meta_cloud.QUALIDADE_ERRO_TTL_S < meta_cloud.QUALIDADE_TTL_S


def test_o_cache_de_falha_expira(monkeypatch):
    import time
    chamou = []
    _responde(monkeypatch, _Resp(503), chamou)
    meta_cloud.qualidade_do_numero()
    meta_cloud._QUALIDADE_CACHE["quando"] = (
        time.time() - meta_cloud.QUALIDADE_ERRO_TTL_S - 1)
    meta_cloud.qualidade_do_numero()
    assert len(chamou) == 2, chamou


def test_forcar_ignora_o_cache(monkeypatch):
    chamou = []
    _responde(monkeypatch, _Resp(200, {"quality_rating": "GREEN"}), chamou)
    meta_cloud.qualidade_do_numero()
    meta_cloud.qualidade_do_numero(forcar=True)
    assert len(chamou) == 2


# --- o que o painel consome -------------------------------------------

def test_as_chaves_que_a_tela_le_existem(monkeypatch):
    """O JS le `luz`, `leitura`, `limite`, `status_do_nome`, `nome_aprovado`.

    Trocar o nome de uma chave aqui sem mexer no JS deixa o card com
    campo vazio e ninguem percebe.
    """
    _responde(monkeypatch, _Resp(200, {
        "quality_rating": "GREEN", "messaging_limit_tier": "TIER_1K",
        "name_status": "APPROVED", "verified_name": "X",
        "display_phone_number": "+55"}))
    d = meta_cloud.qualidade_do_numero()
    for chave in ("ok", "luz", "leitura", "limite", "status_do_nome",
                  "nome_aprovado", "numero", "nota"):
        assert chave in d, chave


def test_o_painel_nao_cai_se_a_meta_cair(monkeypatch):
    import wa_bot
    monkeypatch.setattr(meta_cloud, "qualidade_do_numero",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    assert wa_bot._qualidade_segura()["ok"] is False
