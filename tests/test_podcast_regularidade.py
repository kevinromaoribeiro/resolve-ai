# -*- coding: utf-8 -*-
"""A pergunta da regularidade: depois do primeiro episodio, uma vez so.

Jornada que o Kevin desenhou em 31/08/2026: primeiro audio 6h depois do
cadastro -> escolher a regularidade -> quais temas. As opcoes sao 5, 7, 15 ou
30 dias, e a escolha e tambem a JANELA de noticia.

O risco desta feature nao e ela nao funcionar: e ela funcionar demais. Um slot
numerico vivo tempo demais foi o que, em 30/08, fez "2" respondendo "qual
deles eu dou baixa?" virar assinatura de podcast e a baixa sumir calada.
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
        {"titulo": "Noticia", "resumo": "resumo", "fonte": "Fonte",
         "link": "http://x", "data": None}])
    monkeypatch.setattr(podcast, "locucao", lambda *a, **k: "BIA: oi.\nLEO: oi.")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})
    return True


@pytest.fixture(autouse=True)
def slots_limpos():
    """`PODCAST_FREQ_PERGUNTA` e dicionario de modulo: vaza entre testes."""
    wa_bot.PODCAST_FREQ_PERGUNTA.clear()
    yield
    wa_bot.PODCAST_FREQ_PERGUNTA.clear()


def _pronto(usuario, nichos="futebol"):
    db.update_user_fields(usuario["id"], podcast_nicho=nichos)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=7)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


def _primeiro_episodio(usuario, nichos="futebol"):
    _pronto(usuario, nichos)
    return responder("quero ouvir")


# ---------------------------------------------------------------------------
# 1. a pergunta sai — e sai no lugar certo
# ---------------------------------------------------------------------------

def test_o_fecho_do_primeiro_episodio_pergunta_a_regularidade(usuario, com_voz):
    r = _primeiro_episodio(usuario)
    assert "a cada 5 dias" in r, r
    assert "1x por semana" in r, r
    assert "a cada 15 dias" in r, r
    assert "1x por mês" in r, r


def test_a_pergunta_vai_de_carona_no_fecho(usuario, com_voz, monkeypatch):
    """Mensagem separada custaria uma proativa — e razao de ritmo — pra
    perguntar uma preferencia. Este numero ja foi restringido duas vezes."""
    enviados = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: enviados.append(t) or {"enviado": True})
    r = _primeiro_episodio(usuario)
    assert "está aí em cima" in r, r          # o fecho e a pergunta sao UM
    assert not any("a cada 15 dias" in t for t in enviados), enviados


def test_no_segundo_episodio_nao_pergunta_de_novo(usuario, com_voz):
    """Perguntar duas vezes a mesma preferencia e o "encher o saco" vetado."""
    _primeiro_episodio(usuario)
    responder("2")
    db.update_user_fields(usuario["id"], podcast_ultimo=None)
    r = responder("quero ouvir")
    assert "1x por semana" not in r, r
    assert "uma vez por semana" in r, r


def test_quem_ja_escolheu_ouve_o_proprio_ritmo_no_fecho(usuario, com_voz):
    _pronto(usuario)
    db.update_user_fields(usuario["id"], podcast_frequencia="15")
    r = responder("quero ouvir")
    assert "a cada 15 dias" in r, r


# ---------------------------------------------------------------------------
# 2. a resposta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resposta,dias", [
    ("1", 5), ("2", 7), ("3", 15), ("4", 30),
    ("a cada 5 dias", 5), ("semanal", 7), ("quinzenal", 15), ("mensal", 30),
])
def test_a_escolha_e_guardada(usuario, com_voz, resposta, dias):
    _primeiro_episodio(usuario)
    responder(resposta)
    assert db.frequencia_do_podcast(db.get_user(usuario["id"])) == dias


def test_a_escolha_confirma_em_portugues(usuario, com_voz):
    _primeiro_episodio(usuario)
    r = responder("3")
    assert "a cada 15 dias" in r, r


def test_a_escolha_vira_a_janela_de_noticia(usuario, com_voz, monkeypatch):
    """E o ponto da feature: "se a pessoa pedir a cada 5 dias, precisa entao
    ler as mensagens de 5 dias pra tras"."""
    _primeiro_episodio(usuario)
    responder("4")
    visto = {}
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: visto.update(dias=k.get("dias")) or [])
    db.update_user_fields(usuario["id"], podcast_ultimo=None)
    responder("quero ouvir")
    assert visto.get("dias") == 30, visto


# ---------------------------------------------------------------------------
# 3. a guarda — a metade que nao pode cair
# ---------------------------------------------------------------------------

def test_numero_fora_da_pergunta_nao_muda_frequencia(usuario):
    """Sem pergunta viva, digito e resposta de menu de OUTRO."""
    for digito in ("1", "2", "3", "4"):
        wa_bot.PODCAST_FREQ_PERGUNTA.pop(TELEFONE, None)
        responder(digito)
        assert not (db.get_user(usuario["id"])["podcast_frequencia"] or ""), \
            digito


def test_a_pergunta_expira(usuario, com_voz, monkeypatch):
    """"2" digitado horas depois respondendo outra coisa nao pode virar
    "de 7 em 7 dias" calado."""
    _primeiro_episodio(usuario)
    velho = tempo.agora() - _dt.timedelta(seconds=wa_bot.AJUSTE_TTL_S + 60)
    wa_bot.PODCAST_FREQ_PERGUNTA[TELEFONE] = velho
    responder("2")
    assert not (db.get_user(usuario["id"])["podcast_frequencia"] or "")


def test_nao_insiste_quando_a_pessoa_fala_de_outra_coisa(usuario, com_voz):
    """O slot tem que morrer na primeira resposta que nao for a escolha —
    senao a proxima mensagem dela cai aqui de novo, que e a jaula do menu."""
    _primeiro_episodio(usuario)
    responder("luz 120 dia 10")
    assert TELEFONE not in wa_bot.PODCAST_FREQ_PERGUNTA
    responder("2")
    assert not (db.get_user(usuario["id"])["podcast_frequencia"] or "")


def test_uma_decisao_viva_manda_mais_que_a_preferencia(usuario, com_voz,
                                                       monkeypatch):
    """Preferencia e conveniencia; a baixa de conta e o produto. Foi
    exatamente essa inversao que sumiu com uma baixa em 30/08."""
    _primeiro_episodio(usuario)
    monkeypatch.setattr(wa_bot, "_decisao_de_conversa_viva", lambda p: True)
    responder("2")
    assert not (db.get_user(usuario["id"])["podcast_frequencia"] or "")


# ---------------------------------------------------------------------------
# 4. "muda a frequência" — o fecho promete, entao tem que existir
# ---------------------------------------------------------------------------

def test_muda_a_frequencia_reabre_a_escolha(usuario, com_voz):
    _primeiro_episodio(usuario)
    responder("2")
    r = responder("muda a frequência")
    assert "a cada 15 dias" in r, r
    r2 = responder("1")
    assert db.frequencia_do_podcast(db.get_user(usuario["id"])) == 5, r2


def test_quem_nao_assina_nao_cai_nessa_porta(usuario):
    """Sem assunto escolhido, "frequência" nao e comando de podcast — e a
    pessoa falando de outra coisa."""
    db.update_user_fields(usuario["id"], podcast_nicho=None)
    r = responder("muda a frequência")
    assert "1x por semana" not in (r or "")
