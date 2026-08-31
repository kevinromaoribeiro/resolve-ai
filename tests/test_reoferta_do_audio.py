# -*- coding: utf-8 -*-
"""A reoferta do áudio pra quem pulou o formulário (M7.7).

O Kevin: "TEMOS SIM QUE REOFERTAR pra quem não quis o áudio, e entregar
botões de decisão... e assim que clicar no botão, mandamos que se quiser
reativar, basta dizer QUERO OS ÁUDIOS".

Antes disto, quem marcava "Depois eu escolho" sumia do recurso pra sempre:
a fila do convite exige `podcast_nicho IS NOT NULL`.
"""
import datetime as _dt

import pytest

import db
import scheduler
import tempo
import voz
import wa_bot
from conftest import TELEFONE, responder


@pytest.fixture
def com_voz_e_ligado(monkeypatch):
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)


def _sem_assunto(usuario, horas=30):
    db.update_user_fields(usuario["id"], podcast_nicho=None,
                          podcast_convite_em=None, podcast_recusado_em=None)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=horas)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


def _ids(ds):
    return [d["user_id"] for d in ds]


# ---------------------------------------------------------------------------
# 1. quem pulou volta pro jogo
# ---------------------------------------------------------------------------

def test_quem_pulou_o_formulario_e_reofertado(usuario, com_voz_e_ligado):
    _sem_assunto(usuario)
    assert usuario["id"] in _ids(scheduler.check_podcast_oferta())


def test_a_oferta_leva_os_tres_botoes_ate_o_envio(usuario, com_voz_e_ligado):
    """Sem botão a pessoa teria que digitar — o oposto do pedido do dono."""
    _sem_assunto(usuario)
    d = scheduler.check_podcast_oferta()[0]
    assert wa_bot._botoes_do_disparo(d) == ["Quero ouvir", "Agora não",
                                            "Nunca mais"]


def test_quem_ja_escolheu_nao_recebe_a_oferta(usuario, com_voz_e_ligado):
    _sem_assunto(usuario)
    db.update_user_fields(usuario["id"], podcast_nicho="games")
    assert usuario["id"] not in _ids(scheduler.check_podcast_oferta())


def test_e_uma_vez_so_na_vida(usuario, com_voz_e_ligado):
    _sem_assunto(usuario)
    assert scheduler.check_podcast_oferta()
    db.podcast_marcar_convite(usuario["id"])
    assert usuario["id"] not in _ids(scheduler.check_podcast_oferta())


def test_quem_recusou_nao_e_reofertado(usuario, com_voz_e_ligado):
    """Re-oferta depois de "não" é o que a régua da Meta pune."""
    _sem_assunto(usuario)
    db.update_user_fields(
        usuario["id"], podcast_recusado_em=tempo.agora().isoformat())
    assert usuario["id"] not in _ids(scheduler.check_podcast_oferta())


def test_espera_o_onboarding_acontecer(usuario, com_voz_e_ligado):
    """Oferecer um extra no minuto do cadastro é falar de sobremesa antes
    do prato."""
    _sem_assunto(usuario, horas=1)
    assert usuario["id"] not in _ids(scheduler.check_podcast_oferta())


def test_sem_voz_configurada_nao_promete(usuario, monkeypatch):
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
    monkeypatch.setattr(voz, "disponivel", lambda: False)
    _sem_assunto(usuario)
    assert scheduler.check_podcast_oferta() == []   # cala TODA a fila


def test_a_chave_de_emergencia_cala_a_oferta(usuario, monkeypatch):
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", False)
    _sem_assunto(usuario)
    assert scheduler.check_podcast_oferta() == []


def test_a_oferta_so_sai_dentro_da_janela_de_24h():
    """Não existe template aprovado pra "escolha um assunto". Inventar
    envio fora da janela é o caminho pra terceira restrição."""
    import templates as _cat
    assert "podcast-convite" in _cat.KINDS_SEM_TEMPLATE


# ---------------------------------------------------------------------------
# 2. quem diz não sabe como voltar — e a frase funciona
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frase", [
    "quero os áudios", "quero os audios", "quero as notícias",
    "quero o resumo semanal", "quero o mini podcast", "quero o áudio"])
def test_a_frase_de_volta_e_reconhecida(frase):
    """Prometer uma palavra que o Python não entende é a regra que já
    custou um P0 neste projeto."""
    assert wa_bot._PODCAST_QUERO_RE.match(frase), frase


def test_cancelar_ensina_como_voltar(usuario, com_voz_e_ligado):
    db.update_user_fields(usuario["id"], podcast_nicho="games")
    r = responder("não quero mais o podcast")
    assert "quero os áudios" in r.lower(), r


def test_agora_nao_ensina_como_voltar(usuario, com_voz_e_ligado):
    db.update_user_fields(usuario["id"], podcast_nicho="games")
    r = responder("agora não")
    assert "quero os áudios" in r.lower(), r


def test_a_frase_de_volta_reativa_de_verdade(usuario, com_voz_e_ligado):
    """A promessa fecha o ciclo: quem cancelou consegue voltar sozinho."""
    db.update_user_fields(usuario["id"], podcast_nicho="games")
    responder("não quero mais o podcast")
    assert not db.get_user(usuario["id"])["podcast_nicho"]

    r = responder("quero os áudios")
    assert "futebol" in r.lower(), r
    responder("moda")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "moda"


# ---------------------------------------------------------------------------
# 3. o motor não pode quebrar
# ---------------------------------------------------------------------------

def test_a_madrugada_nao_derruba_o_motor(usuario, monkeypatch):
    madrugada = _dt.datetime(2026, 9, 1, 3, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda a=madrugada: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=madrugada: a.date())
    out = scheduler.run_proactive_engine()
    assert out["podcast_oferta_dispatches"] == []
    assert isinstance(out["total"], int)


def test_a_oferta_entra_no_total_do_motor(usuario, com_voz_e_ligado,
                                          monkeypatch):
    meio_dia = _dt.datetime(2026, 9, 1, 12, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda a=meio_dia: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=meio_dia: a.date())
    _sem_assunto(usuario)
    out = scheduler.run_proactive_engine()
    assert out["podcast_oferta_dispatches"], out
