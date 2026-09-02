# -*- coding: utf-8 -*-
"""O dono colhe áudio quando quiser, escolhendo o tema na hora (M10).

Pedido dele (02/09/2026): "permita que apenas eu o admin master colha os
áudios a hora que eu quiser, pois preciso sempre validar como está. Então eu
mando quero audio, ai ele me pergunta qual tema eu escolho e ai gera na hora,
dos últimos 7 dias e me manda."

A regra que separa os dois mundos: pro CLIENTE, "quero ouvir" respeita o teto
da janela, carimba o envio e entra na conta do farol. Pro DONO é inspeção —
não carimba nada, não gasta o episódio dele, e roda quantas vezes ele quiser.
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


@pytest.fixture(autouse=True)
def slots_limpos():
    wa_bot.PODCAST_AMOSTRA_PERGUNTA.clear()
    yield
    wa_bot.PODCAST_AMOSTRA_PERGUNTA.clear()


@pytest.fixture
def dono(usuario, monkeypatch):
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", TELEFONE)
    return usuario


# ---------------------------------------------------------------------------
# 1. o caminho do dono
# ---------------------------------------------------------------------------

def test_o_dono_pede_e_ele_pergunta_o_tema(dono, com_voz):
    r = responder("quero áudio")
    assert "De qual tema" in r, r
    assert "Futebol" in r and "Gastronomia" in r, r


def test_o_menu_do_dono_traz_os_dezesseis(dono, com_voz):
    r = responder("quero áudio")
    assert "*16*" in r, r


def test_ele_diz_a_janela_que_vai_usar(dono, com_voz):
    """"dos últimos 7 dias" — ele valida o produto de hoje, e a janela do
    cliente varia (5/7/15/30) conforme a escolha de cada um."""
    r = responder("quero áudio")
    assert "7 dias" in r, r


def test_o_numero_gera_aquele_tema(dono, com_voz, monkeypatch):
    pedidos = []
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: pedidos.append((n, k.get("dias"))) or [
                            {"titulo": "N", "resumo": "r", "fonte": "F",
                             "link": "http://x", "data": None}])
    responder("quero áudio")
    responder("1")
    assert pedidos and pedidos[0][0] == "futebol", pedidos


def test_a_janela_da_amostra_e_de_sete_dias(dono, com_voz, monkeypatch):
    pedidos = []
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: pedidos.append(k.get("dias")) or [])
    responder("quero áudio")
    responder("6")
    assert pedidos and all(d == 7 for d in pedidos), pedidos


def test_o_nome_do_tema_tambem_vale(dono, com_voz, monkeypatch):
    pedidos = []
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: pedidos.append(n) or [])
    responder("quero áudio")
    responder("gastronomia")
    assert pedidos and pedidos[0] == "gastronomia", pedidos


# ---------------------------------------------------------------------------
# 2. inspecao nao e entrega
# ---------------------------------------------------------------------------

def test_a_amostra_nao_gasta_o_episodio_do_dono(dono, com_voz):
    """Se marcasse, ele ficaria uma semana sem receber o dele por ter
    testado."""
    responder("quero áudio")
    responder("1")
    assert not (db.get_user(dono["id"])["podcast_ultimo"] or "")


def test_ele_pode_pedir_de_novo_na_hora(dono, com_voz, monkeypatch):
    """O teto existe pra proteger CLIENTE de rajada, nao pra impedir
    inspecao. Sem isso ele levaria "voce ja ouviu o episodio deste periodo"
    justamente quando quer conferir."""
    geradas = []
    monkeypatch.setattr(voz, "sintetizar",
                        lambda *a, **k: geradas.append(1) or b"OggS")
    for _ in range(3):
        responder("quero áudio")
        responder("1")
    assert len(geradas) == 3, geradas


# ---------------------------------------------------------------------------
# 3. so o dono
# ---------------------------------------------------------------------------

def test_cliente_nao_entra_nesse_caminho(usuario, com_voz, monkeypatch):
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5599999999999")
    r = responder("quero áudio")
    assert "De qual tema" not in (r or ""), r


def test_sem_admin_configurado_ninguem_entra(usuario, com_voz, monkeypatch):
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "")
    r = responder("quero áudio")
    assert "De qual tema" not in (r or ""), r


# ---------------------------------------------------------------------------
# 4. a guarda do numero, igual aos outros dois slots
# ---------------------------------------------------------------------------

def test_numero_fora_da_pergunta_nao_gera_audio(dono, com_voz, monkeypatch):
    geradas = []
    monkeypatch.setattr(voz, "sintetizar",
                        lambda *a, **k: geradas.append(1) or b"OggS")
    wa_bot.PODCAST_AMOSTRA_PERGUNTA.pop(TELEFONE, None)
    responder("3")
    assert not geradas, "digito solto virou amostra"


def test_a_pergunta_expira(dono, com_voz, monkeypatch):
    geradas = []
    monkeypatch.setattr(voz, "sintetizar",
                        lambda *a, **k: geradas.append(1) or b"OggS")
    responder("quero áudio")
    wa_bot.PODCAST_AMOSTRA_PERGUNTA[TELEFONE] = (
        tempo.agora() - _dt.timedelta(seconds=wa_bot.AJUSTE_TTL_S + 60))
    responder("1")
    assert not geradas, geradas


def test_nao_insiste_quando_ele_fala_de_outra_coisa(dono, com_voz):
    responder("quero áudio")
    responder("luz 120 dia 10")
    assert TELEFONE not in wa_bot.PODCAST_AMOSTRA_PERGUNTA


def test_decisao_viva_vem_antes(dono, com_voz, monkeypatch):
    """O extra nunca passa na frente da decisao — nem pro dono."""
    monkeypatch.setattr(wa_bot, "_decisao_de_conversa_viva", lambda p: True)
    r = responder("quero áudio")
    assert "me responde a de cima" in r, r
    assert TELEFONE not in wa_bot.PODCAST_AMOSTRA_PERGUNTA


def test_numero_fora_da_lista_repergunta(dono, com_voz):
    """"17" num menu de 16 é engano de dedo, não mudança de assunto.
    Desistindo ali, a frase caía no motor de anotação e ele levava "não
    identifiquei conta, data nem valor" por ter errado o número."""
    responder("quero áudio")
    r = responder("17")
    assert "não está na lista" in r, r
    assert TELEFONE in wa_bot.PODCAST_AMOSTRA_PERGUNTA, "desistiu da pergunta"
    assert wa_bot._frequencia_por_numero("2") or True
    r2 = responder("2")
    assert "não está na lista" not in (r2 or ""), r2


def test_o_cliente_nao_queima_tts_nesse_caminho(usuario, com_voz, monkeypatch):
    """A guarda antiga só olhava se o texto do menu apareceu. Isso passaria
    com o cliente gastando síntese paga — que é o risco de verdade."""
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5599999999999")
    geradas = []
    monkeypatch.setattr(voz, "sintetizar",
                        lambda *a, **k: geradas.append(1) or b"OggS")
    responder("quero áudio")
    responder("1")
    assert not geradas, "cliente queimou TTS pelo caminho do dono"


def test_a_amostra_nao_entra_no_farol(dono, com_voz):
    """O farol conta episódio que foi pra CLIENTE. Amostra do dono ali seria
    métrica mentindo pro próprio dono."""
    responder("quero áudio")
    responder("1")
    assert db.podcast_farois()["na_semana"] == 0


def test_o_dono_ainda_alcanca_o_caminho_do_cliente(dono, com_voz, monkeypatch):
    """Ele pediu pra validar como está — e o que o cliente percorre é o
    `_mandar_podcast`, não a amostra. O `elif` antigo engolia "quero ouvir"
    e tirava isso dele."""
    db.update_user_fields(dono["id"], podcast_nicho="futebol",
                          podcast_frequencia="7")
    chamou = []
    monkeypatch.setattr(wa_bot, "_mandar_podcast",
                        lambda u, p: chamou.append(1) or "ok")
    responder("quero ouvir")
    assert chamou, "o dono perdeu o caminho do cliente"


# ---------------------------------------------------------------------------
# Os dois botões do template de novidade (auditoria M11)
# ---------------------------------------------------------------------------
# Medido pelo auditor, num cliente que nunca teve podcast:
#   "Nunca mais"         -> cancelava o PODCAST (opt-out permanente do produto)
#   "Quero experimentar" -> "não identifiquei conta, data nem valor"
# O botão principal do lançamento era decorativo, e o de recusa desligava a
# coisa errada.

def test_quero_experimentar_abre_a_lista_de_assuntos(usuario, com_voz):
    """É o próximo passo da jornada. Botão que o bot não atende é a regra
    que já custou um P0 nesta base."""
    r = responder("Quero experimentar")
    assert "não identifiquei" not in (r or "").lower(), r
    assert "Futebol" in r and "Gastronomia" in r, r
    assert TELEFONE in wa_bot.PODCAST_PERGUNTA


def test_quero_experimentar_espera_a_decisao_viva(usuario, com_voz,
                                                  monkeypatch):
    monkeypatch.setattr(wa_bot, "_decisao_de_conversa_viva", lambda p: True)
    r = responder("Quero experimentar")
    assert "me responde a de cima" in r, r
    assert TELEFONE not in wa_bot.PODCAST_PERGUNTA


def test_nunca_mais_recusa_tambem_a_proxima_novidade(usuario, com_voz):
    """A justificativa submetida à Meta promete "quem recusa não recebe o
    próximo aviso". Esse carimbo não existia — e promessa de opt-out não
    cumprida, num template MARKETING, é risco de número, não de UX."""
    responder("Nunca mais")
    u = db.get_user(usuario["id"])
    assert u["podcast_recusado_em"], "não registrou a recusa do áudio"
    assert u["novidade_recusada_em"], "não registrou a recusa da novidade"


def test_nunca_mais_continua_cancelando_o_audio(usuario, com_voz):
    responder("Nunca mais")
    assert not (db.get_user(usuario["id"])["podcast_nicho"] or "")
