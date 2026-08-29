# -*- coding: utf-8 -*-
"""O MINI-PODCAST DE PONTA A PONTA: landing -> convite -> audio -> dia.

O que este arquivo cobra e o CAMINHO, nao as pecas — `test_podcast.py` ja
cobre roteiro, fontes e teto. Aqui a pergunta e outra: a escolha da landing
chega no banco? O convite sai com botao? O toque gera audio DE VERDADE pelo
`canal` (que e quem respeita a janela de 24h)? A recusa desliga?

NENHUM TESTE TOCA A REDE nem chama modelo pago: o download de RSS, o LLM e a
sintese de voz sao todos injetaveis, e e o caminho de producao que roda.
"""
import datetime as _dt

import pytest

import canal
import db
import noticias
import podcast
import scheduler
import tempo
import voz
import wa_bot
from conftest import TELEFONE, responder

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Palmeiras vence o Flamengo por 2 a 1</title>
    <description>Gols de Estevao e Rony no Allianz.</description>
    <link>https://ge.globo.com/x</link>
    <pubDate>{ontem}</pubDate>
  </item>
  <item>
    <title>Corinthians anuncia meia argentino</title>
    <description>Chega por emprestimo de um ano.</description>
    <link>https://ge.globo.com/y</link>
    <pubDate>{ontem}</pubDate>
  </item>
</channel></rss>"""


def _feed_falso(agora=None):
    ref = (agora or tempo.agora()) - _dt.timedelta(days=1)
    xml = FEED.format(ontem=ref.strftime("%a, %d %b %Y %H:%M:%S +0000"))
    return lambda url: xml


@pytest.fixture
def com_voz(monkeypatch):
    """Provedor de voz configurado e sintese que devolve bytes de mentira."""
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(voz, "sintetizar", lambda t: b"OggS-audio-falso")
    return True


@pytest.fixture
def com_noticia(monkeypatch):
    monkeypatch.setattr(noticias, "_baixar", _feed_falso())
    # o LLM nao e chamado em teste: cai no roteiro deterministico
    monkeypatch.setattr(podcast, "_chamar_llm",
                        lambda p: (_ for _ in ()).throw(RuntimeError("sem LLM")))
    return True


# ---------------------------------------------------------------------------
# 1. a escolha da landing chega no banco
# ---------------------------------------------------------------------------

def test_o_nicho_da_landing_e_guardado_na_primeira_mensagem(usuario):
    """A landing monta "(e o resumo semanal de Futebol)" no link do WhatsApp.

    Perguntar de novo no chat seria o bot mostrando que nao prestou atencao
    no clique que a pessoa acabou de dar.
    """
    responder("Oi! Quero testar o Resolve AI (e o resumo semanal de Futebol)")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "futebol"


def test_nicho_que_nao_existe_nao_e_guardado(usuario):
    responder("me manda o resumo semanal de criptomoeda")
    assert not db.get_user(usuario["id"])["podcast_nicho"]


def test_a_captura_nao_engole_a_mensagem(usuario, monkeypatch):
    """A pessoa tambem esta se apresentando: o resto do fluxo tem que rodar."""
    chegou = {}

    def _viu(*a, **k):
        chegou["sim"] = True
        return None

    monkeypatch.setattr(wa_bot.motor_v8, "route", _viu, raising=False)
    responder("Oi! Quero testar (e o resumo semanal de Games)")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "games"
    assert chegou.get("sim"), "a mensagem parou na captura do nicho"


# ---------------------------------------------------------------------------
# 2. o convite: 6h depois, uma vez, com botao
# ---------------------------------------------------------------------------

def _com_nicho(usuario, nicho="futebol", horas_atras=7):
    db.update_user_fields(usuario["id"], podcast_nicho=nicho)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=horas_atras)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


def test_o_convite_sai_depois_das_seis_horas(usuario, horario_util, com_voz):
    _com_nicho(usuario, horas_atras=7)
    d = scheduler.check_podcast()
    assert len(d) == 1, d
    assert d[0]["kind"] == "podcast"
    assert "?" in d[0]["message"]
    assert d[0]["botoes"] == ["Quero ouvir", "Agora não", "Não quero mais"]


def test_antes_das_seis_horas_nao_convida(usuario, horario_util, com_voz):
    _com_nicho(usuario, horas_atras=2)
    assert not scheduler.check_podcast()


def test_sem_voz_configurada_o_bot_nem_convida(usuario, horario_util,
                                               monkeypatch):
    """Perguntar "quer ouvir?" sem ter como gerar o audio e prometer o que
    nao da pra entregar — a pessoa toca no botao e nao recebe nada."""
    _com_nicho(usuario)
    monkeypatch.setattr(voz, "disponivel", lambda: False)
    assert not scheduler.check_podcast()


def test_quem_ouviu_essa_semana_nao_e_convidado_de_novo(usuario, horario_util,
                                                        com_voz):
    """Teto DURO de 1x por semana. Audio e a mensagem mais intrusiva que
    existe no WhatsApp, e este numero ja foi restringido duas vezes."""
    _com_nicho(usuario)
    db.podcast_marcar_envio(usuario["id"])
    assert not scheduler.check_podcast()


def test_o_convite_do_disparo_leva_os_botoes_ate_o_envio(usuario,
                                                         horario_util,
                                                         com_voz):
    """Sem isto a pessoa teria que DIGITAR "quero ouvir" — o oposto do
    pedido do dono ("BOTOES pra facilitar a vida")."""
    _com_nicho(usuario)
    d = scheduler.check_podcast()[0]
    assert wa_bot._botoes_do_disparo(d) == ["Quero ouvir", "Agora não",
                                            "Não quero mais"]


# ---------------------------------------------------------------------------
# 3. o toque no botao: o audio sai pelo canal
# ---------------------------------------------------------------------------

def test_quero_ouvir_manda_audio_de_verdade(usuario, com_voz, com_noticia,
                                            monkeypatch):
    """O audio tem que sair pelo `canal.falar_audio`, que e quem respeita a
    janela de 24h. Caminho novo que chame o envio por fora e como a gente
    reabre um buraco ja fechado."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    enviados = {}

    def _falar_audio(tel, dados, **kw):
        enviados["bytes"] = dados
        enviados["tel"] = tel
        return {"enviado": True, "via": "audio", "motivo": ""}

    monkeypatch.setattr(wa_bot.wasender, "falar_audio", _falar_audio,
                        raising=False)
    r = responder("Quero ouvir")

    assert enviados.get("bytes"), "nao mandou audio nenhum"
    assert "futebol" in r.lower(), r
    assert "ge.globo" in r, "nao citou a fonte na mensagem de fecho"
    assert db.get_user(usuario["id"])["podcast_ultimo"], (
        "nao carimbou o envio — o teto semanal nao seguraria")


def test_semana_sem_noticia_nao_manda_audio(usuario, com_voz, monkeypatch):
    """Episodio fabricado pra cumprir tabela e como se perde a confianca de
    alguem de uma vez so."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    monkeypatch.setattr(noticias, "_baixar", lambda url: "<rss></rss>")
    mandou = {}
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda *a, **k: mandou.setdefault("sim", True) or
                        {"enviado": True}, raising=False)
    r = responder("Quero ouvir")
    assert not mandou, "mandou audio sem ter noticia"
    assert "não achei novidade" in r.lower(), r
    assert not db.get_user(usuario["id"])["podcast_ultimo"]


def test_se_a_voz_falhar_nao_manda_audio_quebrado(usuario, com_noticia,
                                                  monkeypatch):
    """Audio que a pessoa toca e nao sai som ensina que o produto nao
    funciona — pior que nao mandar."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(voz, "sintetizar", lambda t: None)
    mandou = {}
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda *a, **k: mandou.setdefault("sim", True) or
                        {"enviado": True}, raising=False)
    r = responder("Quero ouvir")
    assert not mandou
    assert "não consegui gerar" in r.lower(), r


def test_sem_nicho_escolhido_o_bot_explica(usuario, com_voz):
    r = responder("Quero ouvir")
    assert "assunto" in r.lower(), r


# ---------------------------------------------------------------------------
# 4. as saidas
# ---------------------------------------------------------------------------

def test_agora_nao_adia_sem_desligar(usuario, com_voz):
    db.update_user_fields(usuario["id"], podcast_nicho="games")
    r = responder("Agora não")
    assert "próxima" in r.lower(), r
    u = db.get_user(usuario["id"])
    assert u["podcast_nicho"] == "games", "desligou quem so pediu pra esperar"
    assert not u["podcast_ultimo"], "carimbou envio que nao aconteceu"


def test_nao_quero_mais_desliga_de_verdade(usuario):
    """Sem uma saida facil, a unica saida da pessoa e bloquear o numero — e
    bloqueio conta contra a qualidade na Meta."""
    db.update_user_fields(usuario["id"], podcast_nicho="moda",
                          podcast_dia="Sexta")
    r = responder("Não quero mais")
    u = db.get_user(usuario["id"])
    assert not u["podcast_nicho"] and not u["podcast_dia"], u
    assert "lembretes continuam" in r.lower(), r


def test_desligar_o_podcast_nao_mexe_nos_lembretes(usuario):
    db.update_user_fields(usuario["id"], podcast_nicho="moda")
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="dentista", data_vencimento="2026-12-01",
                status="pendente")
    responder("Não quero mais")
    assert len(db.list_items(usuario["id"], status="pendente")) == 1


# ---------------------------------------------------------------------------
# 5. a pergunta do dia, 10 min depois
# ---------------------------------------------------------------------------

def test_a_pergunta_do_dia_vem_depois_do_audio(usuario, horario_util):
    _com_nicho(usuario)
    db.podcast_marcar_envio(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(minutes=11))
    d = scheduler.check_podcast_dia()
    assert len(d) == 1 and d[0]["kind"] == "podcast-dia", d
    assert d[0]["botoes"] == ["Segunda", "Sexta", "Domingo"]


def test_logo_depois_do_audio_ainda_nao_pergunta(usuario, horario_util):
    _com_nicho(usuario)
    db.podcast_marcar_envio(usuario["id"])
    assert not scheduler.check_podcast_dia()


def test_nao_pergunta_duas_vezes(usuario, horario_util):
    """Ela ouviu, nao quis assinar. Insistir e o caminho pro bloqueio."""
    _com_nicho(usuario)
    db.podcast_marcar_envio(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(minutes=11))
    db.podcast_marcar_pergunta_do_dia(usuario["id"])
    assert not scheduler.check_podcast_dia()


def test_a_resposta_do_dia_vira_assinatura(usuario):
    db.update_user_fields(usuario["id"], podcast_nicho="ia")
    db.podcast_marcar_pergunta_do_dia(usuario["id"])
    r = responder("Sexta")
    assert db.get_user(usuario["id"])["podcast_dia"] == "Sexta"
    assert "sexta" in r.lower(), r


def test_dia_solto_sem_a_pergunta_pendente_nao_vira_assinatura(usuario):
    """"segunda" numa frase qualquer nao pode virar assinatura de audio."""
    db.update_user_fields(usuario["id"], podcast_nicho="ia")
    responder("Segunda")
    assert not db.get_user(usuario["id"])["podcast_dia"]


def test_so_quem_escolheu_dia_entra_no_ciclo_semanal(usuario, horario_util,
                                                     com_voz):
    """Quem ouviu uma vez e nao pediu recorrencia nao vira assinante.

    Transformar silencio em assinatura semanal de audio e o tipo de coisa
    que faz a pessoa bloquear o numero.
    """
    _com_nicho(usuario)
    db.podcast_marcar_envio(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
    db.podcast_marcar_convite(usuario["id"])
    assert not scheduler.check_podcast(), "convidou quem nao pediu semanal"

    db.update_user_fields(usuario["id"], podcast_dia="Terça")
    terca = _dt.datetime(2026, 8, 18, 10, 0, 0)      # a fixture congela terça
    assert terca.weekday() == 1
    assert scheduler.check_podcast(ref=terca), "assinante de terça nao recebeu"


# ---------------------------------------------------------------------------
# 6. guardrails
# ---------------------------------------------------------------------------

def test_audio_nao_sai_fora_da_janela_de_24h(usuario, monkeypatch):
    """Audio nao e excecao a janela, e nao existe template de audio."""
    monkeypatch.setattr(db, "dentro_da_janela", lambda *a, **k: False)
    monkeypatch.setattr(canal, "send_audio", lambda *a, **k: True,
                        raising=False)
    r = canal.falar_audio(TELEFONE, b"xxx", user_id=usuario["id"])
    assert not r["enviado"] and r["motivo"] == "fora_da_janela", r


def test_canal_sem_audio_recusa_em_vez_de_estourar(usuario, monkeypatch):
    """No canal reserva `send_audio` e None: recusar e o certo, estourar
    AttributeError no meio de um ciclo nao."""
    monkeypatch.setattr(canal, "send_audio", None, raising=False)
    monkeypatch.setattr(db, "dentro_da_janela", lambda *a, **k: True)
    r = canal.falar_audio(TELEFONE, b"xxx", user_id=usuario["id"])
    assert not r["enviado"] and r["motivo"] == "canal_sem_audio", r


def test_o_convite_nao_tem_template_e_isso_e_deliberado():
    """Pedir pra alguem ouvir um audio e o motivo que a Meta classifica como
    marketing — e marketing neste numero e o que ja rendeu duas restricoes."""
    import templates as T
    assert "podcast" in T.KINDS_SEM_TEMPLATE
    assert "podcast-dia" in T.KINDS_SEM_TEMPLATE
    assert "podcast" not in T.KIND_TEMPLATE
    assert "podcast" in scheduler.KINDS_PROATIVOS
