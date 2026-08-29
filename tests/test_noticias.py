# -*- coding: utf-8 -*-
"""DE ONDE VEM A NOTICIA DO PODCAST: os feeds RSS das fontes fixas.

A regra que este arquivo protege: **o fato vem do feed, nunca do modelo.**
Deixar o LLM "lembrar" a noticia da semana e como se inventa manchete — ele
preenche o que nao sabe, e com voz de locutor soa verdade.

O parser roda contra XML DE VERDADE (RSS e Atom, com namespace, com CDATA,
com HTML no resumo), sem tocar a rede. Parser testado so contra o que ele
mesmo gera e parser nao testado.
"""
import datetime as _dt

import pytest

import noticias
import podcast
import tempo

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Feed de teste</title>
  <item>
    <title>Palmeiras vence o Flamengo por 2 a 1</title>
    <description>&lt;p&gt;Gols de &lt;b&gt;Estevao&lt;/b&gt; e Rony.&lt;/p&gt;</description>
    <link>https://ge.globo.com/materia-1</link>
    <pubDate>{recente}</pubDate>
  </item>
  <item>
    <title>Noticia velha que nao entra</title>
    <description>De um mes atras.</description>
    <link>https://ge.globo.com/velha</link>
    <pubDate>{antiga}</pubDate>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom de teste</title>
  <entry>
    <title>Lancamento do jogo tal</title>
    <summary>Sai em outubro.</summary>
    <link href="https://br.ign.com/materia"/>
    <published>{iso}</published>
  </entry>
</feed>"""


def _rss(agora=None):
    ref = agora or tempo.agora()
    return RSS.format(
        recente=(ref - _dt.timedelta(days=1)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"),
        antiga=(ref - _dt.timedelta(days=40)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"))


# ---------------------------------------------------------------------------
# o parser
# ---------------------------------------------------------------------------

def test_le_rss_de_verdade():
    itens = noticias.parse_feed(_rss(), "ge.globo")
    assert len(itens) == 1, itens
    it = itens[0]
    assert it["titulo"] == "Palmeiras vence o Flamengo por 2 a 1"
    assert it["fonte"] == "ge.globo"
    assert it["link"] == "https://ge.globo.com/materia-1"


def test_le_atom_com_namespace():
    """Atom usa namespace e <link href=...>; ignorar isso perderia o IGN."""
    iso = (tempo.agora() - _dt.timedelta(hours=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    itens = noticias.parse_feed(ATOM.format(iso=iso), "IGN Brasil")
    assert len(itens) == 1, itens
    assert itens[0]["titulo"] == "Lancamento do jogo tal"
    assert itens[0]["link"] == "https://br.ign.com/materia"


def test_html_do_resumo_vira_texto_falavel():
    """O resumo vai pra locucao: tag e entidade lidas em voz alta soam como
    defeito."""
    it = noticias.parse_feed(_rss(), "ge.globo")[0]
    assert "<" not in it["resumo"] and "&lt;" not in it["resumo"], it["resumo"]
    assert "Estevao" in it["resumo"]


def test_noticia_velha_nao_entra():
    """Materia de um mes num "resumo da semana" e o bot mostrando que nao
    olhou."""
    for it in noticias.parse_feed(_rss(), "ge.globo"):
        assert "velha" not in it["titulo"].lower()


def test_item_sem_data_entra():
    """Feed que nao datou o item nao e motivo pra perder a noticia — a ordem
    do RSS ja e do mais novo pro mais velho."""
    xml = ("<rss><channel><item><title>Sem data</title>"
           "<description>x</description></item></channel></rss>")
    assert len(noticias.parse_feed(xml, "ge.globo")) == 1


def test_item_sem_titulo_nao_entra():
    xml = ("<rss><channel><item><description>so resumo</description>"
           "</item></channel></rss>")
    assert noticias.parse_feed(xml, "ge.globo") == []


@pytest.mark.parametrize("lixo", [
    "", None, "isso nao e xml", "<rss><channel><item>", "{'json': true}",
])
def test_feed_quebrado_nao_estoura(lixo):
    """Site pode devolver HTML de erro, JSON, ou nada. Nenhum deles pode
    derrubar o ciclo."""
    assert noticias.parse_feed(lixo, "ge.globo") == []


def test_teto_por_fonte():
    itens = "".join(
        "<item><title>Noticia %d</title><description>x</description></item>" % i
        for i in range(40))
    lidos = noticias.parse_feed("<rss><channel>%s</channel></rss>" % itens,
                                "ge.globo")
    assert len(lidos) <= noticias.POR_FONTE


# ---------------------------------------------------------------------------
# a busca: tres fontes, sem rede
# ---------------------------------------------------------------------------

def test_busca_intercala_as_fontes():
    """Sem intercalar, as tres noticias sairiam do mesmo site sempre que ele
    publicasse mais — e o audio prometeria tres fontes citando uma."""
    itens = noticias.buscar("futebol", baixar=lambda url: _rss())
    # o mesmo XML pras tres fontes: o dedup por titulo deixa so uma
    assert len(itens) == 1, itens

    def _por_url(url):
        nome = "A" if "globo" in url else ("B" if "espn" in url else "C")
        return ("<rss><channel>"
                "<item><title>%s um</title><description>x</description></item>"
                "<item><title>%s dois</title><description>x</description></item>"
                "</channel></rss>" % (nome, nome))

    itens = noticias.buscar("futebol", baixar=_por_url)
    fontes = [i["fonte"] for i in itens[:3]]
    assert len(set(fontes)) == 3, ("nao intercalou: %r" % fontes)


def test_uma_fonte_fora_do_ar_nao_derruba_o_episodio():
    """Um `raise` aqui deixaria a pessoa sem audio porque um site de
    terceiro caiu."""
    def _um_quebra(url):
        if "espn" in url:
            raise RuntimeError("502")
        return _rss()

    itens = noticias.buscar("futebol", baixar=_um_quebra)
    assert itens, "uma fonte caiu e o episodio inteiro sumiu"


def test_todas_fora_do_ar_devolve_vazio_sem_estourar():
    def _tudo_quebra(url):
        raise RuntimeError("sem rede")
    assert noticias.buscar("futebol", baixar=_tudo_quebra) == []


def test_nicho_desconhecido_nao_busca_nada():
    chamou = []
    noticias.buscar("criptomoeda", baixar=lambda u: chamou.append(u) or "")
    assert not chamou, "bateu na rede por um nicho que nao existe"


def test_a_mesma_noticia_em_duas_fontes_vira_um_bloco():
    """No futebol as tres fontes cobrem o mesmo jogo, e um resumo com o mesmo
    placar tres vezes e o audio se desmentindo sozinho."""
    igual = ("<rss><channel><item><title>Palmeiras 2 x 1 Flamengo</title>"
             "<description>x</description></item></channel></rss>")
    itens = noticias.buscar("futebol", baixar=lambda u: igual)
    assert len(itens) == 1, itens


# ---------------------------------------------------------------------------
# so as fontes declaradas
# ---------------------------------------------------------------------------

def test_busca_so_bate_nos_feeds_declarados():
    """Descobrir fonte sozinho quebraria a promessa de "da pra conferir"."""
    batidos = []

    def _espiao(url):
        batidos.append(url)
        return _rss()

    noticias.buscar("moda", baixar=_espiao)
    esperados = {f[2] for f in podcast.NICHOS["moda"]["fontes"]}
    assert set(batidos) == esperados, (batidos, esperados)


def test_o_roteiro_so_aceita_o_que_veio_das_fontes():
    """A ponte entre este modulo e o `podcast`: fonte de fora e descartada."""
    itens = [{"titulo": "Manchete", "resumo": "x", "fonte": "Blog do Ze"}]
    assert podcast.montar_roteiro("futebol", itens) is None


def test_verificar_diz_qual_feed_morreu():
    """Site troca de endereco sem avisar, e feed morto vira episodio que
    nunca sai — em silencio, que e como uma feature morre sem ninguem
    perceber."""
    def _metade(url):
        if "espn" in url:
            raise RuntimeError("404")
        return _rss()

    r = noticias.verificar(baixar=_metade)
    assert len(r) == 15, len(r)
    ruins = [x for x in r if not x["ok"]]
    assert any("ESPN" in x["fonte"] for x in ruins), ruins
    assert all(x["erro"] for x in ruins), "feed ruim sem motivo no relatorio"
