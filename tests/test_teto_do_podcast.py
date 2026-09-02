# -*- coding: utf-8 -*-
"""O teto entre episodios — e ele segue a frequencia da pessoa (M9.12).

Dois furos que a verificacao reversa expos:

1. O teto do caminho MANUAL ("quero ouvir") nao tinha teste nenhum. Desfazer
   a guarda deixava a suite verde — e sem ela dez toques viram dez episodios,
   dez chamadas pagas de TTS e, com tres assuntos, TRINTA notas de voz. E
   exatamente o padrao de rajada que a Meta pune, num numero ja restringido
   duas vezes.

2. O intervalo era 7 dias FIXOS nos dois caminhos. Com a escolha de
   regularidade (5/7/15/30), quem pedisse "a cada 5 dias" continuaria
   recebendo de 7 em 7, calado: a escolha aparecia na confirmacao e nao
   acontecia no produto.
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


@pytest.fixture
def conta_tts(monkeypatch):
    """Cada chamada aqui e dinheiro: TTS e cobrado por caractere."""
    chamadas = []
    monkeypatch.setattr(voz, "sintetizar",
                        lambda *a, **k: chamadas.append(1) or b"OggS" + b"x" * 900)
    return chamadas


def _assinante(usuario, nichos="futebol", freq=None, ultimo_ha_dias=None):
    campos = {"podcast_nicho": nichos}
    if freq is not None:
        campos["podcast_frequencia"] = str(freq)
    if ultimo_ha_dias is not None:
        campos["podcast_ultimo"] = (
            tempo.agora() - _dt.timedelta(days=ultimo_ha_dias)).isoformat()
    db.update_user_fields(usuario["id"], **campos)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=7)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


# ---------------------------------------------------------------------------
# 1. o teto do "quero ouvir"
# ---------------------------------------------------------------------------

def test_quem_acabou_de_ouvir_nao_ouve_de_novo(usuario, com_voz, conta_tts):
    _assinante(usuario, ultimo_ha_dias=1)
    r = responder("quero ouvir")
    assert not conta_tts, "queimou TTS pago dentro do intervalo"
    assert "já ouviu" in r, r


def test_o_teto_vale_pra_quem_tem_tres_assuntos(usuario, com_voz, conta_tts):
    """O furo que a reversao expos: o portao lia chave unica, entao quem
    tinha tres assuntos passava batido — e cada toque virava TRES audios."""
    _assinante(usuario, "futebol,economia,moda", ultimo_ha_dias=1)
    responder("quero ouvir")
    assert not conta_tts, "tres assuntos furaram o teto do episodio"


def test_dez_toques_nao_viram_dez_episodios(usuario, com_voz, conta_tts):
    """Dez notas de voz em segundos e o padrao de rajada que a Meta pune."""
    _assinante(usuario)
    responder("quero ouvir")
    primeira = len(conta_tts)
    assert primeira, "o primeiro episodio nem saiu — o teste nao mede nada"
    for _ in range(9):
        responder("quero ouvir")
    assert len(conta_tts) == primeira, conta_tts


def test_passado_o_intervalo_ela_ouve_de_novo(usuario, com_voz, conta_tts):
    """Teto nao pode virar jaula: no oitavo dia o episodio tem que sair."""
    _assinante(usuario, ultimo_ha_dias=8)
    responder("quero ouvir")
    assert conta_tts, "o teto prendeu quem ja tinha cumprido o intervalo"


def test_a_recusa_diz_o_ritmo_e_a_saida(usuario, com_voz):
    """"desta semana" pra quem escolheu 30 dias e simplesmente errado."""
    _assinante(usuario, freq=30, ultimo_ha_dias=2)
    r = responder("quero ouvir")
    assert "uma vez por mês" in r, r
    assert "muda a frequência" in r, r


# ---------------------------------------------------------------------------
# 2. o intervalo E a frequencia escolhida
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("freq,dias,pode", [
    (5, 4, False), (5, 6, True),
    (7, 6, False), (7, 8, True),
    (15, 10, False), (15, 16, True),
    (30, 20, False), (30, 31, True),
])
def test_o_intervalo_segue_a_escolha(usuario, com_voz, conta_tts,
                                     freq, dias, pode):
    _assinante(usuario, freq=freq, ultimo_ha_dias=dias)
    responder("quero ouvir")
    assert bool(conta_tts) is pode, (freq, dias, conta_tts)


def test_sem_escolha_o_intervalo_e_semanal(usuario, com_voz, conta_tts):
    _assinante(usuario, ultimo_ha_dias=6)
    responder("quero ouvir")
    assert not conta_tts


def test_frequencia_invalida_nao_abre_a_torneira(usuario, com_voz, conta_tts):
    """A coluna e TEXT e passa por update administrativo. "0" virando
    intervalo zero seria episodio a cada toque, pra sempre."""
    _assinante(usuario, freq="0", ultimo_ha_dias=1)
    responder("quero ouvir")
    assert not conta_tts


@pytest.mark.parametrize("dias", [0, -3, 1, 4])
def test_intervalo_menor_que_o_piso_cai_no_padrao(dias):
    """"no maximo a cada 5 dias" (Kevin, 31/08/2026) — e um valor abaixo do
    piso nao pode virar liberdade, tem que virar o padrao."""
    ultimo = (tempo.agora() - _dt.timedelta(days=2)).isoformat()
    assert not podcast.pode_enviar(ultimo, dias=dias)


def test_o_primeiro_episodio_nunca_esbarra_no_teto():
    assert podcast.pode_enviar(None, dias=30)


# ---------------------------------------------------------------------------
# 3. o caminho proativo usa o mesmo relogio
# ---------------------------------------------------------------------------

def test_o_convite_proativo_respeita_a_frequencia(usuario, com_voz,
                                                  horario_util):
    """Se so o caminho manual respeitasse a escolha, quem pediu 30 dias
    receberia convite toda semana assim mesmo — e convite e proativa."""
    _assinante(usuario, freq=30, ultimo_ha_dias=10)
    assert not [d for d in scheduler.check_podcast()
                if d["user_id"] == usuario["id"]]


def test_o_convite_sai_quando_a_frequencia_dela_vence(usuario, com_voz,
                                                      horario_util):
    _assinante(usuario, freq=5, ultimo_ha_dias=6)
    db.update_user_fields(usuario["id"], podcast_convite_em=None)
    assert [d for d in scheduler.check_podcast()
            if d["user_id"] == usuario["id"]]
