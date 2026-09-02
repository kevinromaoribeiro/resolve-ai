# -*- coding: utf-8 -*-
"""Os achados da auditoria do M9, cada um com o cenario que o expos.

O auditor REPROVOU o marco com 1 P0 e 5 P1. Este arquivo e a prova de que
cada um foi fechado — e, mais importante, de que fica fechado: sao os testes
que faltavam, nao testes do conserto.

Regra que se repete em quase todos: **o que a gente DIZ tem que ser o que a
gente FEZ**, e resposta de menu tem que SER a resposta, nunca conte-la.
"""
import datetime as _dt

import pytest

import db
import noticias
import podcast
import scheduler
import templates
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
    return True


@pytest.fixture(autouse=True)
def slots_limpos():
    wa_bot.PODCAST_PERGUNTA.clear()
    wa_bot.PODCAST_FREQ_PERGUNTA.clear()
    yield
    wa_bot.PODCAST_PERGUNTA.clear()
    wa_bot.PODCAST_FREQ_PERGUNTA.clear()


def _pronto(usuario, nichos="futebol"):
    db.update_user_fields(usuario["id"], podcast_nicho=nichos,
                          podcast_frequencia="7")
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=7)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


# ===========================================================================
# P0-1 — o lembrete semanal nao saía pra quem tem 2 ou 3 assuntos
# ===========================================================================
# O template e o UNICO caminho do podcast que atravessa a janela de 24h.
# `rotulo()` le chave unica e devolvia "" pra "futebol,economia" — e "" ali
# significa "nao sai". Quem nao falava com o bot ha dois dias simplesmente
# parava de ser avisado, sem erro no log.

@pytest.mark.parametrize("guardado", [
    "futebol",
    "futebol,economia",
    "futebol,economia,moda",
])
def test_o_lembrete_semanal_sai_com_qualquer_quantidade_de_assuntos(usuario,
                                                                    guardado):
    db.update_user_fields(usuario["id"], podcast_nicho=guardado)
    nome, variaveis = templates.para_disparo(
        {"kind": "podcast", "user_id": usuario["id"], "user_nome": "Ana"})
    assert nome == "resolveai_podcast_pronto", (guardado, nome)
    assert len(variaveis) == 2, variaveis
    assert variaveis[1], "a variavel do assunto saiu vazia — o template nao sai"


def test_o_lembrete_nomeia_os_tres_assuntos(usuario):
    db.update_user_fields(usuario["id"], podcast_nicho="futebol,economia,moda")
    _, variaveis = templates.para_disparo(
        {"kind": "podcast", "user_id": usuario["id"], "user_nome": "Ana"})
    assert "futebol" in variaveis[1] and "moda" in variaveis[1], variaveis


@pytest.mark.parametrize("guardado", [None, "", "extinto"])
def test_sem_assunto_valido_o_lembrete_continua_nao_saindo(usuario, guardado):
    """A outra metade: o corpo promete "seu resumo de *X*". Sem X, mandar
    seria pior que nao mandar — e "nossas fontes" (o default do `_lista`)
    sairia como "seu resumo de nossas fontes esta pronto"."""
    db.update_user_fields(usuario["id"], podcast_nicho=guardado)
    nome, variaveis = templates.para_disparo(
        {"kind": "podcast", "user_id": usuario["id"], "user_nome": "Ana"})
    assert nome is None, (guardado, nome, variaveis)


def test_rotulos_vazio_e_string_vazia_nao_texto_de_fonte():
    """Quem chama isto pra montar template usa o vazio como "nao manda"."""
    for bruto in (None, "", "extinto", []):
        assert podcast.rotulos_da_pessoa(bruto) == "", bruto


# ===========================================================================
# P1-1 — "a cada 15 dias" gravava 5 dias
# ===========================================================================
# "5 dias" esta contido em "a cada 15 dias", e o ramo dos 5 era testado
# primeiro. O menu escreve exatamente "a cada 15 dias": quem respondia com as
# palavras levava TRES VEZES a taxa de mensagem que pediu.

@pytest.mark.parametrize("resposta,dias", [
    ("a cada 5 dias", 5),
    ("1x por semana", 7),
    ("a cada 15 dias", 15),
    ("1x por mês", 30),
])
def test_a_redacao_do_proprio_menu_e_aceita(resposta, dias):
    """Se o menu oferece a frase, a frase tem que valer o que ela diz."""
    assert wa_bot._frequencia_por_numero(resposta) == dias


def test_o_menu_e_o_parser_nao_divergem():
    """A pergunta e o parser vivem em funcoes diferentes; nada garantia que
    continuassem falando a mesma lingua."""
    pergunta = wa_bot._pergunta_da_regularidade()
    conferidas = 0
    for linha in pergunta.split("\n"):
        if "*" in linha and "—" in linha:
            frase = linha.split("—", 1)[1].strip()
            assert wa_bot._frequencia_por_numero(frase), frase
            conferidas += 1
    # SEM ISTO O TESTE PASSA COM ZERO ITERACOES. Se o formato do menu mudar
    # (o travessao virar dois-pontos), o filtro nao casa nada e o teste fica
    # verde medindo o vazio — a mesma forma do `estrela\b` passando por
    # "galaxia".
    assert conferidas == len(db.FREQUENCIAS), conferidas


# ===========================================================================
# P1-2 — o slot da frequencia engolia pedido de verdade
# ===========================================================================
# Com o slot vivo por 20 min, QUALQUER frase contendo "semana" virava
# resposta do menu e dava `return`: o lembrete nunca chegava ao motor. E a
# mesma jaula do menu numerico de 30/08, com outra porta.

@pytest.mark.parametrize("frase", [
    "me lembra do IPTU semana que vem",
    "me lembra disso daqui a 5 dias",
    "boleto de 30 dias",
    "paguei a conta essa semana",
    "todo mes vence dia 10",
    "luz 120 dia 10",
])
def test_frase_de_verdade_nao_e_resposta_de_menu(frase):
    assert wa_bot._frequencia_por_numero(frase) is None, frase


def test_o_pedido_real_chega_ao_motor_com_a_pergunta_viva(usuario, com_voz,
                                                          monkeypatch):
    """O cenario inteiro: primeiro episodio -> pergunta viva -> a pessoa pede
    um lembrete. O pedido nao pode virar "Combinado, uma vez por semana"."""
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})
    _pronto(usuario)
    db.update_user_fields(usuario["id"], podcast_frequencia=None)
    responder("quero ouvir")
    assert TELEFONE in wa_bot.PODCAST_FREQ_PERGUNTA, "a pergunta nem foi feita"

    r = responder("me lembra do IPTU semana que vem")
    assert "Combinado" not in (r or ""), r
    assert not (db.get_user(usuario["id"])["podcast_frequencia"] or "")


# ===========================================================================
# P1-3 — legenda orfa, e "1 de 3" repetido
# ===========================================================================

def test_falha_de_envio_nao_deixa_legenda_sem_audio(usuario, com_voz,
                                                    monkeypatch):
    """NENHUMA legenda sem audio atras dela.

    A primeira versao deste teste aceitava UMA (`<= 1`), porque a correcao da
    1a rodada — o `break` — matou a multiplicacao das legendas orfas e nao a
    causa: a legenda saía antes do envio, e antes de mandar nao da pra saber
    se ele vai. O auditor pegou: a docstring dizia que o defeito era a legenda
    orfa e a asserçao permitia uma. Verde, sem guardar o que declarava.

    A causa fechou mandando a legenda DEPOIS do audio. Agora o numero certo
    e zero."""
    textos = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": False,
                                             "motivo": "canal fora"})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: textos.append(t) or {"enviado": True})

    _pronto(usuario, "futebol,economia,moda")
    responder("quero ouvir")
    legendas = [t for t in textos if " de 3" in t]
    assert not legendas, legendas


def test_a_contagem_nunca_repete_o_mesmo_numero(usuario, com_voz, monkeypatch):
    saiu = {"n": 0}

    def falar_audio(tel, a, **k):
        saiu["n"] += 1
        return {"enviado": saiu["n"] != 2, "motivo": "canal fora"}

    textos = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio", falar_audio)
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: textos.append(t) or {"enviado": True})

    _pronto(usuario, "futebol,economia,moda")
    responder("quero ouvir")
    numeros = [t.split("—")[1].strip() for t in textos if "—" in t and " de " in t]
    assert len(numeros) == len(set(numeros)), numeros


# ===========================================================================
# P1-4 — sete mensagens de enfiada, sem o freio que o resto da casa usa
# ===========================================================================

def test_o_envio_espaca_entre_assuntos(usuario, com_voz, monkeypatch):
    """Rajada e a assinatura de ritmo que ja rendeu 3h de restricao neste
    numero. As outras duas rotas de lote esperam entre envios; esta era a
    unica sem freio — e a unica que roda pra cliente."""
    import time as _t
    esperas = []
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 8.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 15.0)
    monkeypatch.setattr(_t, "sleep", lambda s: esperas.append(s))
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "futebol,economia,moda")
    responder("quero ouvir")
    assert len(esperas) == 2, esperas          # entre os tres, nao antes
    assert all(8.0 <= s <= 15.0 for s in esperas), esperas


def test_com_um_assunto_a_pessoa_nao_espera(usuario, com_voz, monkeypatch):
    """Fazer quem tocou "quero ouvir" esperar pelo PRIMEIRO audio seria pagar
    o preco do freio sem o motivo dele."""
    import time as _t
    esperas = []
    monkeypatch.setattr(_t, "sleep", lambda s: esperas.append(s))
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "futebol")
    responder("quero ouvir")
    assert not esperas, esperas


# ===========================================================================
# P1-5 — o fecho afirmava entrega que nao aconteceu
# ===========================================================================

def test_o_fecho_so_cita_o_que_saiu(usuario, com_voz, monkeypatch):
    """Assinante de moda+futebol, moda sem noticia: saía UM audio e o fecho
    dizia "seu resumo de moda e futebol esta ai em cima", com as fontes da
    Vogue por um episodio que nao existe."""
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: [] if n == "moda" else [
                            {"titulo": "N", "resumo": "r", "fonte": "F",
                             "link": "http://x", "data": None}])
    monkeypatch.setattr(podcast, "locucao",
                        lambda k, itens, **kw: "BIA: oi.\nLEO: oi." if itens else "")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "moda,futebol")
    r = responder("quero ouvir")
    cabeca = r.split("_Fontes:")[0]
    assert "futebol" in cabeca, r
    assert "moda" not in cabeca, "o fecho afirmou um episodio que nao saiu"


def test_o_fecho_diz_o_que_faltou(usuario, com_voz, monkeypatch):
    """Semana quieta num assunto e comportamento CORRETO, mas a pessoa contou
    os audios e viu que faltou um. Nao dizer nada parece defeito."""
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: [] if n == "moda" else [
                            {"titulo": "N", "resumo": "r", "fonte": "F",
                             "link": "http://x", "data": None}])
    monkeypatch.setattr(podcast, "locucao",
                        lambda k, itens, **kw: "BIA: oi.\nLEO: oi." if itens else "")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "moda,futebol")
    r = responder("quero ouvir")
    assert "moda" in r, r
    assert "não achei novidade" in r, r


def test_as_fontes_do_fecho_sao_as_do_episodio_que_saiu(usuario, com_voz,
                                                        monkeypatch):
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: [] if n == "moda" else [
                            {"titulo": "N", "resumo": "r", "fonte": "F",
                             "link": "http://x", "data": None}])
    monkeypatch.setattr(podcast, "locucao",
                        lambda k, itens, **kw: "BIA: oi.\nLEO: oi." if itens else "")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "moda,futebol")
    r = responder("quero ouvir")
    citadas = r.split("_Fontes: ")[1].split("._")[0]
    for _f in podcast.fontes("moda"):
        assert _f[0] not in citadas, (_f[0], citadas)


# ===========================================================================
# P2 — tres termos que ainda deixavam assunto errado passar
# ===========================================================================
# "garanta que vamos entregar o que a pessoa pedir e nada mais" (Kevin,
# 31/08/2026). O primeiro e o pior: materia de doenca lida em voz de
# horoscopo e um estrago de verdade com um cliente.

@pytest.mark.parametrize("tema,titulo", [
    ("horoscopo", "Câncer de mama: novo exame chega ao SUS"),
    ("ciencia", "Restaurante premiado ganha estrela Michelin"),
    ("gastronomia", "Show do Rock in Rio tem bar novo no Palco Sunset"),
])
def test_a_palavra_ambigua_nao_entra_sozinha(tema, titulo):
    assert not podcast.e_do_assunto(tema, titulo, ""), titulo


@pytest.mark.parametrize("tema,titulo", [
    # e o sinal continua existindo — o que saiu foi o vale-tudo
    ("horoscopo", "Previsão do dia para Câncer e Leão"),
    ("horoscopo", "Horóscopo de hoje: Câncer recebe boas notícias"),
    ("horoscopo", "Signo de Câncer entra em fase de mudanças"),
    ("ciencia", "Astrônomos observam estrela que explodiu na Via Láctea"),
    ("ciencia", "Telescópio registra estrelas em formação"),
    ("gastronomia", "Novo bar de coquetéis abre nos Jardins"),
    ("gastronomia", "Bar da esquina inaugura menu de petiscos"),
])
def test_com_o_contexto_do_assunto_ela_vale(tema, titulo):
    assert podcast.e_do_assunto(tema, titulo, ""), titulo
