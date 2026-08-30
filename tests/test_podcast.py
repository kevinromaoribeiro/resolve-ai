# -*- coding: utf-8 -*-
"""MINI-PODCAST SEMANAL DE 3 MINUTOS — a estrutura.

Ideia do Kevin (28-29/08/2026): um audio de 3 min por semana, do assunto que
a pessoa escolheu, estilo podcast, com as fontes no final.

O que este arquivo cobra: as regras que o Kevin definiu e que, se quebrarem,
custam o numero — um nicho por pessoa, no maximo 1x por semana, nunca manda
sem perguntar, e toda afirmacao com fonte verificavel.

O que ele NAO cobra: geracao de audio. A sintese de voz e servico externo com
custo por minuto, e o Kevin vai testar na mao antes de a gente escolher.
"""
import datetime as _dt

import pytest

import podcast
import tempo


# ---------------------------------------------------------------------------
# os cinco nichos e as fontes
# ---------------------------------------------------------------------------

def test_os_cinco_nichos_que_o_kevin_definiu():
    assert set(podcast.NICHOS) == {
        "futebol", "games", "ia", "moda", "varejo online"}


@pytest.mark.parametrize("nicho", list(podcast.NICHOS))
def test_cada_nicho_tem_tres_fontes_verificaveis(nicho):
    """Tres fontes, com URL. Fonte que a pessoa nao consegue abrir nao serve
    pra nada — o motivo de citar e justamente deixar conferir."""
    fs = podcast.NICHOS[nicho]["fontes"]
    assert len(fs) == 3, (nicho, fs)
    for nome, url, rss in fs:
        assert nome.strip(), nicho
        assert url.startswith("https://"), (nicho, url)
        # O FEED E OBRIGATORIO. Sem ele a fonte e decorativa: o bot cita no
        # audio uma coisa que nunca leu.
        assert rss.startswith("https://"), (nicho, nome, rss)


def test_nicho_aceita_como_a_pessoa_escreve():
    """A landing manda o rotulo bonito, o botao manda outra coisa.

    Um `KeyError` aqui viraria pessoa cadastrada sem nicho nenhum.
    """
    for entrada in ("IA", "ia", "Inteligência artificial",
                    "inteligencia artificial"):
        assert podcast.nicho_valido(entrada) == "ia", entrada
    assert podcast.nicho_valido("Varejo online") == "varejo online"
    assert podcast.nicho_valido("Futebol") == "futebol"
    for lixo in ("", None, "  ", "criptomoeda", "futebol americano"):
        assert podcast.nicho_valido(lixo) is None, lixo


# ---------------------------------------------------------------------------
# o teto de 3 minutos
# ---------------------------------------------------------------------------

# TITULO DO ASSUNTO, nao generico: desde o M5.0 o `_validos` corta o que nao
# fala do nicho, e "Noticia numero 1" nao fala de nada. Fixture generica
# passaria a medir o filtro em vez do que o teste diz medir.
_TITULOS = {
    "futebol": "Palmeiras vence o jogo numero %d",
    "games": "Novo jogo de PlayStation numero %d",
    "ia": "ChatGPT ganha recurso numero %d",
    "moda": "Tendencia de moda numero %d na passarela",
    "varejo online": "Loja online cresce no e-commerce numero %d",
}


def _itens(nicho, n=3, resumo="Resumo curto do que aconteceu essa semana."):
    fontes = [f[0] for f in podcast.NICHOS[nicho]["fontes"]]
    molde = _TITULOS[nicho]
    return [{"titulo": molde % i, "resumo": resumo,
             "fonte": fontes[(i - 1) % len(fontes)]} for i in range(1, n + 1)]


def test_o_roteiro_cabe_em_tres_minutos():
    """Audio que promete 3 min e entrega 6 e a primeira coisa que faz
    alguem desativar o recurso."""
    r = podcast.montar_roteiro("futebol", _itens("futebol"), nome="Kevin")
    assert r
    assert podcast.duracao_estimada_s(r) <= 3 * 60 * 1.15, (
        "passou do teto: %ds" % podcast.duracao_estimada_s(r))


def test_noticia_gigante_perde_um_bloco_e_nao_a_frase():
    """Corte pelo FIM, nunca pelo meio: cortar no meio de uma frase deixa o
    ouvinte no ar; cortar uma noticia inteira mantem o audio coerente."""
    enorme = "palavra " * 400
    r = podcast.montar_roteiro("games", _itens("games", 3, resumo=enorme))
    assert r
    assert podcast.duracao_estimada_s(r) <= 3 * 60 * 1.15, (
        podcast.duracao_estimada_s(r))
    assert r.rstrip().endswith("Até lá!"), (
        "cortou no meio e o audio termina no vazio")


def test_o_roteiro_termina_citando_as_fontes():
    """Audio gerado por IA sem fonte e como a gente perde a confianca de
    alguem de uma vez so."""
    r = podcast.montar_roteiro("ia", _itens("ia"))
    assert "Canaltech IA" in r, r
    # e sai numa FALA, nao solto: o episodio e uma conversa desde o M5.0
    assert any("Canaltech IA" in t for _q, t in podcast.falas(r)), r


def test_noticia_sem_fonte_e_descartada():
    """Nao "conserta": o audio nao pode afirmar o que nao da pra conferir."""
    itens = [{"titulo": "Boato qualquer", "resumo": "sei la", "fonte": ""}]
    assert podcast.montar_roteiro("moda", itens) is None


def test_fonte_de_fora_da_lista_nao_entra():
    """A lista existe pra que a pessoa possa conferir; aceitar qualquer
    fonte devolveria o problema que ela resolve."""
    itens = [{"titulo": "Palmeiras vence o jogo", "resumo": "x",
             "fonte": "Blog do Zé"}]
    assert podcast.montar_roteiro("futebol", itens) is None


def test_semana_sem_noticia_nao_vira_episodio_vazio():
    """Silencio e melhor que um episodio de 30s dizendo que nao houve
    novidade — isso ensina a pessoa a desativar."""
    assert podcast.montar_roteiro("games", []) is None
    assert podcast.montar_roteiro("games", None) is None
    assert podcast.montar_roteiro(None, _itens("games")) is None


def test_no_maximo_tres_blocos():
    r = podcast.montar_roteiro("varejo online", _itens("varejo online", 9))
    assert r
    assert "4. " not in r, r


# ---------------------------------------------------------------------------
# o teto de 1x por semana
# ---------------------------------------------------------------------------

def test_nao_manda_dois_na_mesma_semana():
    """Teto DURO, nao sugestao. Audio e a mensagem mais intrusiva do
    WhatsApp, e este numero ja foi restringido duas vezes."""
    agora = _dt.datetime(2026, 9, 10, 10, 0, 0)
    assert podcast.pode_enviar(None, agora=agora), "o primeiro sempre pode"
    ontem = (agora - _dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert not podcast.pode_enviar(ontem, agora=agora)
    seis = (agora - _dt.timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")
    assert not podcast.pode_enviar(seis, agora=agora)
    sete = (agora - _dt.timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    assert podcast.pode_enviar(sete, agora=agora)


def test_data_ilegivel_conta_como_acabou_de_enviar():
    """O erro seguro aqui e mandar de MENOS."""
    agora = _dt.datetime(2026, 9, 10, 10, 0, 0)
    for lixo in ("ontem", "2026-13-45", "xxx"):
        assert not podcast.pode_enviar(lixo, agora=agora), lixo


def test_quatro_por_mes_no_maximo():
    """A conta que o Kevin fez: 1x por semana = 4 no mes."""
    agora = _dt.datetime(2026, 9, 1, 10, 0, 0)
    enviados, ultimo = 0, None
    for dia in range(30):
        ref = agora + _dt.timedelta(days=dia)
        if podcast.pode_enviar(ultimo, agora=ref):
            enviados += 1
            ultimo = ref.strftime("%Y-%m-%d %H:%M:%S")
    assert enviados <= 5, enviados   # 30 dias / 7 = 4 completos + o do dia 1


# ---------------------------------------------------------------------------
# nunca manda sem perguntar
# ---------------------------------------------------------------------------

def test_o_convite_pergunta_antes_e_tem_botao():
    """Audio de 3 min que chega sozinho no meio da reuniao e o tipo de coisa
    que faz a pessoa bloquear o numero."""
    c = podcast.convite("futebol", nome="Kevin Santos")
    assert c and "?" in c["texto"], c
    assert c["botoes"] == ["Quero ouvir", "Agora não", "Não quero mais"]
    assert "Kevin" in c["texto"] and "Santos" not in c["texto"], c["texto"]
    assert len(c["botoes"]) <= 3, "a Meta aceita no maximo 3"
    assert all(len(b) <= 20 for b in c["botoes"]), c["botoes"]


def test_convite_sem_nicho_nao_existe():
    assert podcast.convite(None) is None
    assert podcast.convite("criptomoeda") is None


def test_a_pergunta_do_dia_vem_depois_do_audio():
    """Perguntar antes de a pessoa ouvir e pedir compromisso sobre algo que
    ela ainda nao sabe se gosta."""
    assert podcast.MINUTOS_ATE_PERGUNTAR_O_DIA >= 5
    p = podcast.pergunta_do_dia(nome="Ana")
    assert "?" in p["texto"]
    assert len(p["botoes"]) <= 3 and all(len(b) <= 20 for b in p["botoes"])


def test_a_saida_existe_no_proprio_convite():
    """Sem "Nao quero mais" visivel, a unica saida da pessoa e bloquear."""
    c = podcast.convite("games")
    assert any("não quero" in b.lower() for b in c["botoes"]), c["botoes"]


# ---------------------------------------------------------------------------
# o briefing que vai pro gerador
# ---------------------------------------------------------------------------

def test_o_briefing_fecha_as_fontes_e_a_duracao():
    """O modelo nao escolhe fonte e nao escolhe duracao — ele preenche uma
    estrutura ja decidida. Deixar ele escolher a fonte e como se inventa
    manchete: ele preenche o que nao sabe."""
    b = podcast.briefing("moda")
    assert b["palavras_teto"] <= 520, b
    assert b["blocos"] == 3
    assert len(b["fontes"]) == 3
    assert podcast.briefing("nao existe") is None


def test_o_fluxo_do_kevin_esta_nas_constantes():
    """6h apos o cadastro pergunta, 10 min depois do audio pergunta o dia,
    1x por semana."""
    assert podcast.HORAS_ATE_O_CONVITE == 6
    assert podcast.MINUTOS_ATE_PERGUNTAR_O_DIA == 10
    assert podcast.DIAS_ENTRE_EPISODIOS == 7


# ---------------------------------------------------------------------------
# auditoria M4.0
# ---------------------------------------------------------------------------

def test_uma_noticia_gigante_tambem_cabe_em_tres_minutos():
    """Com UMA noticia nao da pra cortar bloco, e o teto continua valendo.

    Sem isto, um resumo grande virava um "audio de tres minutos" de onze
    horas: promessa quebrada, e TTS e cobrado por minuto.
    """
    for n in (5_000, 100_000):
        r = podcast.montar_roteiro(
            "games", [{"titulo": "Novo jogo de PlayStation",
                       "resumo": "palavra " * n,
                       "fonte": "Adrenaline"}])
        assert r
        assert podcast.duracao_estimada_s(r) <= 3 * 60 * 1.15, (
            "%d palavras -> %ds" % (n, podcast.duracao_estimada_s(r)))
        assert r.rstrip().endswith("Até lá!"), r[-80:]
        # e continua sendo dialogo depois do corte
        assert len(podcast.falas(r)) >= 4, r


def test_noticia_sem_titulo_e_descartada():
    """O par do "sem fonte": manchete vazia nao vira bloco de audio.

    Este teste existe porque o de fonte NAO cobria esta metade — a fonte
    vazia ja caia na lista de permitidas, e apagar a checagem de titulo
    deixava a suite verde (auditoria M4.0).
    """
    itens = [{"titulo": "", "resumo": "algo", "fonte": "IGN Brasil"},
             {"titulo": "   ", "resumo": "algo", "fonte": "IGN Brasil"}]
    assert podcast.montar_roteiro("games", itens) is None


def test_fonte_pode_vir_como_dominio_ou_url():
    """Um scraper devolve "ge.globo.com" ou a URL, nao o rotulo bonito.

    Casar so por nome exato virava episodio vazio, em silencio.
    """
    for f in ("ge.globo", "ge.globo.com", "https://ge.globo.com/futebol/"):
        assert podcast.montar_roteiro(
            "futebol", [{"titulo": "Palmeiras vence o jogo",
                       "resumo": "r", "fonte": f}]), f
    assert podcast.montar_roteiro(
        "futebol", [{"titulo": "Palmeiras vence o jogo", "resumo": "r",
                     "fonte": "https://blogdoze.com/x"}]) is None


@pytest.mark.parametrize("valor,esperado", [
    ("2026-09-01 10:00:00", True),
    ("2026-09-01T10:00:00.123456", True),       # microssegundos
    ("2026-09-01T10:00:00-03:00", True),        # timezone
    ("2026-09-01", True),                       # so a data
    ("2026-09-09", False),                      # ontem
    (_dt.datetime(2026, 9, 1, 10), True),       # objeto, nao string
    (_dt.date(2026, 9, 1), True),
])
def test_pode_enviar_aceita_o_que_o_banco_devolve(valor, esperado):
    """Se a coluna virar datetime (ou vier com timezone), a versao anterior
    devolvia False PARA SEMPRE — o podcast morria calado, que e o pior tipo
    de defeito porque ninguem vai procurar."""
    agora = _dt.datetime(2026, 9, 10, 10, 0, 0)
    assert podcast.pode_enviar(valor, agora=agora) is esperado, valor


def test_pode_enviar_com_relogio_de_data_nao_estoura():
    """`tempo.hoje()` devolve `date`; subtrair de `datetime` estourava
    TypeError FORA do try e mataria o cron inteiro."""
    assert podcast.pode_enviar("2026-09-01 10:00:00",
                               agora=_dt.date(2026, 9, 10)) is True


def test_todas_as_urls_tem_dominio_coerente_com_o_nome():
    """Fonte cujo dominio nao bate com o nome e fonte que ninguem confere."""
    esperado = {
        "ge.globo": "ge.globo.com", "ESPN Brasil": "espn.com.br",
        "Trivela": "trivela.com.br",
        "Adrenaline": "adrenaline.com.br",
        "Arkade": "arkade.com.br",
        "GameBlast": "gameblast.com.br",
        "Canaltech IA": "canaltech.com.br", "Olhar Digital": "olhardigital.com.br",
        "MIT Technology Review Brasil": "mittechreview.com.br",
        "Vogue Brasil": "vogue.globo.com",
        "Steal the Look": "stealthelook.com.br",
        "FFW": "ffw.uol.com.br",
        "Consumidor Moderno": "consumidormoderno.com.br",
        "Meio&Mensagem": "meioemensagem.com.br",
        "NeoFeed": "neofeed.com.br",
    }
    vistos = 0
    for dados in podcast.NICHOS.values():
        for nome, url, _rss in dados["fontes"]:
            assert nome in esperado, "fonte nova sem dominio conferido: %s" % nome
            assert podcast._dominio(url) == esperado[nome], (nome, url)
            vistos += 1
    assert vistos == 15
