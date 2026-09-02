# -*- coding: utf-8 -*-
"""Os achados da SEGUNDA passada da auditoria.

A primeira rodada fechou o P0 e os cinco P1. A segunda mostrou que dois deles
tinham fechado o sintoma e deixado a causa — e que o sensor novo media a
pergunta errada. Este arquivo prende cada um.

O fio que liga todos: **uma correcao que nao conversa com os vizinhos nao e
uma correcao**. O `break` estava certo e sozinho; a legenda continuava saindo
antes do envio; o carimbo trancava a pessoa por um erro nosso; e o fecho
contava metade dos motivos de um audio nao ter chegado.
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
    return True


def _pronto(usuario, nichos="futebol,economia,moda", freq="30"):
    db.update_user_fields(usuario["id"], podcast_nicho=nichos,
                          podcast_frequencia=freq)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=7)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


def _canal(monkeypatch, falha_no=None):
    """Captura textos e áudios; `falha_no` diz qual envio de áudio quebra."""
    estado = {"n": 0}
    textos, audios = [], []

    def falar_audio(tel, a, **k):
        estado["n"] += 1
        if falha_no and estado["n"] == falha_no:
            return {"enviado": False, "motivo": "canal fora"}
        audios.append(a)
        return {"enviado": True}

    monkeypatch.setattr(wa_bot.wasender, "falar_audio", falar_audio)
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: textos.append(t) or {"enviado": True})
    return textos, audios


# ===========================================================================
# P1-A — entrega parcial: legenda, carimbo e fecho
# ===========================================================================

def test_nenhuma_legenda_sai_sem_audio(usuario, com_voz, monkeypatch):
    """A causa, nao o sintoma: antes de mandar nao da pra saber se o audio
    vai, entao a legenda so pode sair depois dele."""
    textos, audios = _canal(monkeypatch, falha_no=2)
    _pronto(usuario)
    responder("quero ouvir")
    legendas = [t for t in textos if " de 3" in t]
    assert len(legendas) == len(audios), (legendas, len(audios))


def test_a_legenda_nomeia_o_audio_que_veio_antes(usuario, com_voz, monkeypatch):
    textos, _ = _canal(monkeypatch)
    _pronto(usuario)
    responder("quero ouvir")
    legendas = [t for t in textos if " de 3" in t]
    assert len(legendas) == 3, legendas
    assert "Futebol" in legendas[0] and "1 de 3" in legendas[0]
    assert "Moda" in legendas[2] and "3 de 3" in legendas[2]


def test_entrega_parcial_nao_carimba(usuario, com_voz, monkeypatch):
    """Carimbar trancaria a pessoa ate a proxima janela — 30 dias, aqui —
    por um erro que foi nosso."""
    _canal(monkeypatch, falha_no=2)
    _pronto(usuario, freq="30")
    responder("quero ouvir")
    assert not (db.get_user(usuario["id"])["podcast_ultimo"] or "")


def test_entrega_completa_carimba(usuario, com_voz, monkeypatch):
    """A outra metade: sem carimbo no caminho normal, o teto de janela
    sumiria e "quero ouvir" viraria audio ilimitado."""
    _canal(monkeypatch)
    _pronto(usuario)
    responder("quero ouvir")
    assert db.get_user(usuario["id"])["podcast_ultimo"]


def test_a_retomada_manda_so_o_que_faltou(usuario, com_voz, monkeypatch):
    """Sem carimbo ela pode pedir de novo — e nao pode receber repetido, que
    e audio duplicado e TTS pago duas vezes pelo mesmo conteudo."""
    _canal(monkeypatch, falha_no=2)
    _pronto(usuario)
    responder("quero ouvir")

    textos, audios = _canal(monkeypatch)
    responder("quero ouvir")
    assert len(audios) == 2, "remandou o que ja tinha chegado"


def test_a_retomada_continua_a_contagem(usuario, com_voz, monkeypatch):
    """"1 de 3" de novo faria a pessoa procurar um primeiro que ela ja tem."""
    _canal(monkeypatch, falha_no=2)
    _pronto(usuario)
    responder("quero ouvir")

    textos, _ = _canal(monkeypatch)
    responder("quero ouvir")
    legendas = [t for t in textos if " de 3" in t]
    assert any("2 de 3" in t for t in legendas), legendas
    assert not any("1 de 3" in t for t in legendas), legendas


def test_o_fecho_reconhece_o_que_nao_foi_entregue(usuario, com_voz,
                                                  monkeypatch):
    """Ela contou os audios e viu que faltou. Nao reconhecer parece defeito
    calado — e a promessa era "o que nao saiu e dito, nao escondido"."""
    _canal(monkeypatch, falha_no=2)
    _pronto(usuario)
    r = responder("quero ouvir")
    assert "não consegui te mandar" in r.lower(), r
    assert "economia" in r.lower(), r


def test_falha_logo_no_primeiro_nao_culpa_a_geracao(usuario, com_voz,
                                                    monkeypatch):
    """"Nao consegui gerar o audio" quando o episodio existia e so nao
    atravessou culpa a coisa errada — e esconde que tentar de novo resolve."""
    _canal(monkeypatch, falha_no=1)
    _pronto(usuario)
    r = responder("quero ouvir")
    assert "gerar" not in r.lower(), r
    assert "quero ouvir" in r.lower(), r


def test_o_lote_interrompido_nao_atrapalha_o_periodo_seguinte(usuario, com_voz,
                                                              monkeypatch):
    """A primeira versao do dedup olhava a JANELA inteira: com frequencia
    semanal, o episodio da semana seguinte encontrava a entrega da semana
    passada e era pulado. A pessoa pararia de receber, calada."""
    _canal(monkeypatch)
    _pronto(usuario, "futebol", freq="7")
    responder("quero ouvir")

    # o periodo seguinte
    db.update_user_fields(usuario["id"], podcast_ultimo=(
        tempo.agora() - _dt.timedelta(days=8)).isoformat())
    _, audios = _canal(monkeypatch)
    responder("quero ouvir")
    assert len(audios) == 1, "o episodio do periodo seguinte foi pulado"


def test_sem_falha_recente_nada_e_pulado(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    assert db.podcast_lote_interrompido(usuario["id"]) == set()


def test_com_falha_recente_o_que_chegou_e_lembrado(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    db.podcast_registrar_episodio(usuario["id"], "economia", 0, False, "canal")
    assert db.podcast_lote_interrompido(usuario["id"]) == {"futebol"}


# ===========================================================================
# P1-B — "da semana" mentia pra quem escolheu quinzenal ou mensal
# ===========================================================================

@pytest.mark.parametrize("nichos", ["futebol", "futebol,economia,moda"])
def test_o_convite_nao_promete_semana(nichos):
    """A regularidade virou escolha — 5, 7, 15 ou 30 dias. "Da semana" mente
    pra metade das opcoes."""
    c = podcast.convite(nichos, nome="Ana")
    assert "da semana" not in c["texto"].lower(), c["texto"]


def test_o_convite_continua_dizendo_o_que_e(nichos="futebol"):
    c = podcast.convite(nichos, nome="Ana")
    assert "mini podcast" in c["texto"].lower()
    assert "futebol" in c["texto"].lower()


# ===========================================================================
# P2 — o sensor de fonte caida media a pergunta errada
# ===========================================================================
# Contava falha so quando o GET levantava. O jeito mais comum de um RSS morrer
# hoje nao e dar erro: o site passa a servir HTML de desafio de bot com HTTP
# 200 — e "<html>Just a moment...</html>" e XML valido, que parseia liso e da
# zero item. O farol lia "semana quieta" pra sempre.

@pytest.mark.parametrize("corpo", [
    "<html>Just a moment...</html>",       # Cloudflare, HTTP 200
    "<html><body>404</body></html>",
    "{'json': true}",
    "",
    "   ",
    "<rss><channel>",                      # XML quebrado
])
def test_corpo_que_nao_e_feed_conta_como_fonte_caida(corpo):
    rel = {}
    noticias.buscar("futebol", baixar=lambda u: corpo, relatorio=rel)
    assert rel["falharam"] == rel["fontes"], (corpo, rel)


@pytest.mark.parametrize("corpo", [
    "<rss><channel></channel></rss>",
    '<feed xmlns="http://www.w3.org/2005/Atom"></feed>',
])
def test_feed_valido_e_vazio_e_semana_quieta(corpo):
    """Semana quieta e resposta legitima da fonte e nao pode acender nada."""
    rel = {}
    noticias.buscar("futebol", baixar=lambda u: corpo, relatorio=rel)
    assert rel["falharam"] == 0, (corpo, rel)


@pytest.mark.parametrize("corpo,esperado", [
    ("<rss><channel><item><title>x</title></item></channel></rss>", True),
    ('<feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>', True),
    ("<rss><channel></channel></rss>", True),
    ("<html>Just a moment...</html>", False),
    (None, False),
    ("", False),
])
def test_parece_feed(corpo, esperado):
    assert noticias.parece_feed(corpo) is esperado


def test_o_parser_continua_nunca_levantando():
    """Contrato antigo, e ele estava certo: parser que levanta obriga todo
    chamador a lembrar do try. Eu tinha quebrado isso pra medir a fonte."""
    for lixo in ("", None, "isso nao e xml", "<rss><channel><item>",
                 "{'json': true}", "<html>Just a moment...</html>"):
        assert noticias.parse_feed(lixo, "ge.globo") == []


# ===========================================================================
# P2 — a duracao prometida, e o catalogo que o dono le
# ===========================================================================

def test_nenhum_texto_promete_dois_minutos(usuario):
    """O motor mira 3 min (PALAVRAS_ALVO=400, teto 450 = 3min cravados). O convite evita
    prometer duracao DE PROPOSITO; os outros textos desfaziam isso."""
    import inspect
    for fonte in (inspect.getsource(wa_bot), inspect.getsource(scheduler)):
        for frase in ("uns dois minutos", "uns 2 minutos"):
            achados = [l.strip() for l in fonte.split("\n")
                       if frase in l and not l.strip().startswith("#")]
            assert not achados, achados


def test_a_aba_de_poderes_descreve_o_produto_de_hoje():
    """E o catalogo que o dono le pra saber o que vende — ele foi prospectar
    cliente com ela aberta."""
    pod = [p for p in wa_bot.PODERES if "podcast" in p["titulo"].lower()]
    assert pod, "a aba perdeu o podcast"
    desc = pod[0]["desc"]
    assert "3 assunto" in desc or "ATÉ 3" in desc, desc
    assert "16" in desc, desc
    assert "5, 7, 15 ou 30" in desc, desc
    assert "um nicho por pessoa" not in desc, desc
    assert "Cinco nichos" not in desc, desc


def test_a_frequencia_por_extenso_nao_aceita_numero_de_dia_solto():
    """"5" valia cinco dias, mas "7"/"15"/"30" nao valiam nada — e o menu
    numera 1 a 4, entao "5" nem e opcao."""
    assert wa_bot._frequencia_por_numero("5") is None
    assert wa_bot._frequencia_por_numero("7") is None
    for n, dias in (("1", 5), ("2", 7), ("3", 15), ("4", 30)):
        assert wa_bot._frequencia_por_numero(n) == dias


# ===========================================================================
# 3a passada — o corte do lote e o ULTIMO LOTE CONCLUIDO, nao um prazo
# ===========================================================================
# O auditor: "a janela de 6h expira e o carimbo nao". Se a pessoa voltasse
# depois de 6h, o dedup nao lembrava mais e ela recebia repetido — audio
# duplicado e TTS pago duas vezes pelo mesmo texto. E o teste que existia
# provava o gate `houve_falha`, nao o corte: trocar 6h por 720h continuava
# verde.

def test_a_retomada_lembra_mesmo_depois_de_horas(usuario, com_voz, monkeypatch):
    """Entrega parcial nao carimba — entao "sem carimbo desde X" e a
    definicao exata de lote em aberto, e ela nao vence."""
    _canal(monkeypatch, falha_no=2)
    _pronto(usuario)
    responder("quero ouvir")

    # a pessoa volta bem depois: o log envelhece, o lote continua em aberto
    with db.get_conn() as c:
        c.execute("UPDATE podcast_log SET quando=? WHERE user_id=?",
                  ((tempo.agora() - _dt.timedelta(hours=20)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))

    _, audios = _canal(monkeypatch)
    responder("quero ouvir")
    assert len(audios) == 2, "remandou o que ja tinha chegado"


def test_o_carimbo_fecha_o_lote(usuario, com_voz, monkeypatch):
    """Depois de um lote CONCLUIDO, nada anterior conta — senao o episodio do
    periodo seguinte encontraria a entrega do periodo passado."""
    _canal(monkeypatch)
    _pronto(usuario, "futebol", freq="7")
    responder("quero ouvir")
    carimbo = db.get_user(usuario["id"])["podcast_ultimo"]
    assert carimbo

    # uma falha depois do carimbo nao pode ressuscitar o que veio antes dele
    db.podcast_registrar_episodio(usuario["id"], "economia", 0, False, "canal")

    # MESMO SEGUNDO, DE PROPOSITO. E assim que acontece em producao — o
    # carimbo e escrito logo depois das linhas do lote que ele fecha — e e o
    # unico estado em que o "+1s" do corte discrimina. Deixando o relogio
    # decidir, uma maquina mais lenta poe as linhas um segundo antes do
    # carimbo e o teste passa mesmo sem a correcao: verde intermitente pelo
    # motivo errado.
    with db.get_conn() as c:
        c.execute("UPDATE podcast_log SET quando=? WHERE user_id=?",
                  (carimbo.replace("T", " ")[:19], usuario["id"]))
    assert db.podcast_lote_interrompido(
        usuario["id"], ultimo=carimbo) == set()


def test_sem_carimbo_nenhum_o_piso_de_horas_vale(usuario):
    """Quem nunca recebeu nada nao tem carimbo — o piso de horas e o que
    sobra pra delimitar o lote."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    db.podcast_registrar_episodio(usuario["id"], "economia", 0, False, "canal")
    assert db.podcast_lote_interrompido(
        usuario["id"], ultimo=None) == {"futebol"}
    # passado o piso, o lote e velho demais pra valer: a noticia envelheceu e
    # reenviar sai mais barato que raciocinar sobre um lote de anteontem.
    with db.get_conn() as c:
        c.execute("UPDATE podcast_log SET quando=? WHERE user_id=?",
                  ((tempo.agora() - _dt.timedelta(hours=96)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    assert db.podcast_lote_interrompido(usuario["id"], ultimo=None) == set()


# ===========================================================================
# 3a passada — o /health separa fonte muda de semana quieta
# ===========================================================================

def test_o_health_acusa_fonte_que_respondeu_sem_ser_feed():
    """A docstring do `verificar` diz que ele existe porque "feed morto vira
    episodio que nunca sai — em silencio". Tratar Cloudflare-com-200 como
    "feed vazio" era justamente o silencio."""
    linhas = noticias.verificar(baixar=lambda u: "<html>Just a moment...</html>")
    assert linhas and all(not l["ok"] for l in linhas)
    assert all("nao e feed" in l["erro"] for l in linhas), linhas[0]


def test_o_health_nao_acusa_semana_quieta():
    """Feed que responde e nao teve noticia esta saudavel. Acusar isso faria
    o /health gritar lobo e o dono aprender a ignora-lo."""
    linhas = noticias.verificar(
        baixar=lambda u: "<rss><channel></channel></rss>")
    assert linhas and all(l["ok"] for l in linhas)
    assert all(l["erro"] == "sem noticia recente" for l in linhas)


def test_o_template_do_podcast_esta_congelado():
    """O corpo ja esta aprovado na Meta: editar no codigo faria ele divergir
    em silencio do que sai pro cliente."""
    import test_portugues
    assert "resolveai_podcast_pronto" in test_portugues.CONGELADOS_NA_META
