# -*- coding: utf-8 -*-
"""A amostra não deixa a mensagem vazar pro motor de IA (M15).

O que o dono viu na tela dele, 02/09/2026: pediu o tema **13 (Geopolítica)**,
recebeu o áudio certo — e logo abaixo um texto INVENTADO listando notícias de
"celebridades e TV". Áudio de um assunto, texto de outro, com manchetes que
ninguém apurou.

A causa é uma linha minha. Fiz a amostra devolver `""` pra não mandar o
relatório que ele pediu pra tirar; os três chamadores testam `if resposta:`,
e string vazia é falsa. O texto não saiu — e a mensagem seguiu pro motor
como se ninguém tivesse tratado, com o "13" solto.

"Tratei, e não há o que dizer" precisa ser **dizível**. Vazio não diz isso:
é indistinguível de "não tratei".
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
    monkeypatch.setattr(voz, "sintetizar", lambda *a, **k: b"OggS" + b"x" * 900)
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
    monkeypatch.setattr(noticias, "buscar", lambda *a, **k: [
        {"titulo": "N", "resumo": "r", "fonte": "F", "link": "http://x",
         "data": None}])
    monkeypatch.setattr(podcast, "locucao", lambda *a, **k: "BIA: oi.\nLEO: oi.")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot, "send_whatsapp", lambda *a, **k: True,
                        raising=False)
    return True


@pytest.fixture
def dono(usuario, monkeypatch):
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", TELEFONE)
    return usuario


def test_o_sentinela_nao_e_texto_vazio():
    """Se voltar a ser "", os chamadores param de distinguir "tratei" de
    "não tratei" — que é exatamente o defeito."""
    assert wa_bot.SEM_RESPOSTA
    assert wa_bot.SEM_RESPOSTA.strip() != ""


def test_a_amostra_completa_devolve_o_sentinela(dono, com_voz):
    r = wa_bot._handle_commands(dono, TELEFONE, "quero áudio")
    assert "De qual tema" in r
    r2 = wa_bot._handle_commands(dono, TELEFONE, "13")
    assert r2 == wa_bot.SEM_RESPOSTA, r2


def test_o_numero_do_tema_nao_chega_no_motor(dono, com_voz, monkeypatch):
    """O cenário exato: "13" seguiu pro motor e ele respondeu com uma lista
    de notícias inventada, de outro assunto."""
    chamou = []
    monkeypatch.setattr(wa_bot, "_handle_onboarding",
                        lambda *a, **k: chamou.append("onb") or None)

    responder("quero áudio")
    r = responder("13")
    assert not chamou, "a mensagem vazou pra depois do comando"
    assert r is None or wa_bot.SEM_RESPOSTA not in str(r), r


def test_o_sentinela_nunca_chega_no_cliente(dono, com_voz):
    """Mandar "\x00sem-resposta" no WhatsApp de alguém seria trocar um
    defeito por outro pior."""
    responder("quero áudio")
    saida = responder("13")
    assert wa_bot.SEM_RESPOSTA not in str(saida or "")


def test_quando_algo_falha_o_texto_volta(dono, com_voz, monkeypatch):
    """O relatório sai do caminho feliz, não do caminho de erro: quando um
    tema não sai, o texto é a única forma de o dono saber."""
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": False,
                                             "motivo": "canal fora"})
    responder("quero áudio")
    r = responder("13")
    assert r and r != wa_bot.SEM_RESPOSTA, r
    assert "não saiu" in r.lower() or "nem tudo" in r.lower(), r


# ---------------------------------------------------------------------------
# O acelerador: o import que faltava
# ---------------------------------------------------------------------------

def test_o_voz_importa_io():
    """`_colar_opus` usa `io.open`, e o `voz.py` nunca importou `io`. Toda
    colagem em Opus morria em NameError e o episódio saía em MP3 — que toca
    e NÃO tem o botão de 1x/1,5x/2x. O acelerador nunca funcionou."""
    import io as _io
    assert voz.io is _io


def test_colar_opus_nao_estoura_por_nome_indefinido(monkeypatch):
    """Sem ffmpeg ele devolve None de propósito. O que não pode é morrer em
    NameError — que era o que acontecia, silenciosamente, dentro do except."""
    monkeypatch.setattr(voz, "tem_ffmpeg", lambda: False)
    assert voz._colar_opus([b"x", b"y"]) is None
    assert "NameError" not in (getattr(voz, "_ULTIMA_FALHA_OPUS", "") or "")


# ---------------------------------------------------------------------------
# O QUARTO CHAMADOR (auditoria M16, P0)
# ---------------------------------------------------------------------------
# Eu contei três e são quatro. O endpoint do painel chama
# `_amostra_de_podcast` DIRETO e manda o retorno por `send_whatsapp` — e ele
# existe justamente pra mandar a amostra pra OUTRA pessoa avaliar junto.
# Reproduzido pelo auditor: o telefone recebeu "\x00sem-resposta". Antes do
# M15 ia "" ali; eu troquei um lixo por um pior.

def test_o_painel_nao_manda_o_sentinela(dono, com_voz, monkeypatch):
    from fastapi.testclient import TestClient
    enviadas = []
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "send_whatsapp",
                        lambda tel, txt, **k: enviadas.append(txt) or True)

    r = TestClient(wa_bot.app).post(
        "/painel/acao",
        headers={"X-Painel-Token": "tok"},
        json={"acao": "amostra_podcast", "telefone": TELEFONE,
              "nicho": "futebol"})
    assert r.status_code == 200, r.text
    assert wa_bot.SEM_RESPOSTA not in str(enviadas), enviadas
    assert wa_bot.SEM_RESPOSTA not in r.text, r.text
    assert not any(t.strip() == "" for t in enviadas), enviadas


def test_o_painel_ainda_manda_a_legenda(dono, com_voz, monkeypatch):
    """A guarda não pode calar o que tem serventia."""
    from fastapi.testclient import TestClient
    enviadas = []
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "send_whatsapp",
                        lambda tel, txt, **k: enviadas.append(txt) or True)
    TestClient(wa_bot.app).post(
        "/painel/acao", headers={"X-Painel-Token": "tok"},
        json={"acao": "amostra_podcast", "telefone": TELEFONE,
              "nicho": "futebol"})
    assert any("amostra" in t.lower() for t in enviadas), enviadas


def test_a_amostra_nao_saúda_pelo_nome(dono, com_voz, monkeypatch):
    """O roteiro do cliente perdeu o nome no M12. Se a amostra mantivesse,
    ela deixaria de ser "o que o cliente ouviria" — a única razão dela."""
    vistos = []
    monkeypatch.setattr(podcast, "locucao",
                        lambda *a, **k: vistos.append(k.get("nome")) or "BIA: oi.")
    responder("quero áudio")
    responder("1")
    assert vistos and not any(vistos), vistos
