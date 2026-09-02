# -*- coding: utf-8 -*-
"""O passo a passo e a troca de assuntos (M9.11).

O Kevin pediu "o caminho pra pessoa escolher o que quer receber e regularidade
... e tambem um passo a passo simples".

A regularidade ja trocava desde o M9.9. Os ASSUNTOS nao: quem escolheu futebol
e cansou tinha duas saidas, aguentar ou cancelar o podcast inteiro — e
preferencia sem porta de troca vira motivo de cancelamento, nao de retencao.
"""
import datetime as _dt

import pytest

import db
import noticias
import podcast
import scheduler
import tempo
import voz
import wa_bot
from conftest import TELEFONE, responder


@pytest.fixture
def com_voz(monkeypatch):
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(voz, "sintetizar", lambda *a, **k: b"OggS" + b"x" * 8000)
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
    monkeypatch.setattr(noticias, "buscar", lambda *a, **k: [
        {"titulo": "N", "resumo": "r", "fonte": "F", "link": "http://x",
         "data": None}])
    monkeypatch.setattr(podcast, "locucao", lambda *a, **k: "BIA: oi.\nLEO: oi.")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})
    return True


@pytest.fixture(autouse=True)
def slots_limpos():
    wa_bot.PODCAST_PERGUNTA.clear()
    wa_bot.PODCAST_FREQ_PERGUNTA.clear()
    yield
    wa_bot.PODCAST_PERGUNTA.clear()
    wa_bot.PODCAST_FREQ_PERGUNTA.clear()


def _assinante(usuario, nichos="futebol", freq="7"):
    db.update_user_fields(usuario["id"], podcast_nicho=nichos,
                          podcast_frequencia=freq)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=7)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


# ---------------------------------------------------------------------------
# 1. o passo a passo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frase", [
    "como funciona o podcast",
    "como funciona o mini podcast",
    "passo a passo",
    "ajuda podcast",
])
def test_o_passo_a_passo_responde(usuario, frase):
    r = responder(frase)
    assert "Como funciona o mini podcast" in r, (frase, r)


def test_cada_linha_do_guia_e_uma_coisa_pra_digitar(usuario):
    """Guia que explica conceito nao ajuda no celular — ajuda o que a pessoa
    consegue copiar e mandar."""
    r = responder("como funciona o podcast")
    for comando in ("quero ouvir", "muda os assuntos", "muda a frequência",
                    "não quero mais o podcast"):
        assert comando in r, (comando, r)


def test_o_guia_diz_o_estado_atual_de_quem_assina(usuario):
    """Passo a passo generico faz a pessoa perguntar "mas eu estou em qual
    mesmo?"."""
    _assinante(usuario, "futebol,economia", freq="15")
    r = responder("como funciona o podcast")
    assert "futebol" in r and "economia" in r, r
    assert "a cada 15 dias" in r, r


def test_o_guia_atende_quem_ainda_nao_assinou(usuario):
    """E ele quem explica como assinar — recusar por nao ser assinante seria
    fechar a porta na cara de quem quer entrar."""
    db.update_user_fields(usuario["id"], podcast_nicho=None)
    r = responder("como funciona o podcast")
    assert "ainda não assinou" in r, r


def test_o_guia_nao_promete_comando_que_nao_existe(usuario):
    """Cada comando citado tem que ser atendido de verdade — foi por isso
    que "muda os assuntos" precisou existir."""
    _assinante(usuario)
    r = responder("como funciona o podcast")
    for frase, regex in (("muda os assuntos", wa_bot._PODCAST_ASSUNTOS_RE),
                         ("muda a frequência", wa_bot._PODCAST_FREQ_RE),
                         ("quero ouvir", wa_bot._PODCAST_QUERO_RE)):
        assert frase in r, frase
        assert regex.match(frase), frase


# ---------------------------------------------------------------------------
# 2. trocar os assuntos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frase", [
    "muda os assuntos", "mudar os temas", "troca os assuntos",
    "quero outros assuntos",
])
def test_a_troca_reabre_a_lista(usuario, frase):
    _assinante(usuario)
    r = responder(frase)
    assert "Futebol" in r and "Gastronomia" in r, (frase, r)


def test_a_troca_grava_a_escolha_nova(usuario):
    _assinante(usuario, "futebol")
    responder("muda os assuntos")
    responder("6, 12")
    assert podcast.nichos_da_pessoa(
        db.get_user(usuario["id"])) == ["economia", "horoscopo"]


def test_a_troca_nao_apaga_a_frequencia(usuario):
    """Trocar de assunto nao e recomecar: o ritmo que ela escolheu continua,
    e perguntar de novo seria a mesma pergunta duas vezes."""
    _assinante(usuario, "futebol", freq="30")
    responder("muda os assuntos")
    responder("6")
    assert db.frequencia_do_podcast(db.get_user(usuario["id"])) == 30


def test_quem_nao_assina_nao_cai_nessa_porta(usuario):
    db.update_user_fields(usuario["id"], podcast_nicho=None)
    r = responder("muda os assuntos")
    assert "Gastronomia" not in (r or ""), r


def test_uma_decisao_viva_vem_antes_da_troca(usuario, monkeypatch):
    """O extra nunca passa na frente da decisao — foi a inversao que sumiu
    com uma baixa de conta em 30/08."""
    _assinante(usuario)
    monkeypatch.setattr(wa_bot, "_decisao_de_conversa_viva", lambda p: True)
    r = responder("muda os assuntos")
    assert "me responde a de cima" in r, r
    assert TELEFONE not in wa_bot.PODCAST_PERGUNTA


def test_a_troca_respeita_o_teto_de_tres(usuario):
    _assinante(usuario)
    responder("muda os assuntos")
    responder("1, 2, 3, 4, 5")
    assert len(podcast.nichos_da_pessoa(db.get_user(usuario["id"]))) == 3
