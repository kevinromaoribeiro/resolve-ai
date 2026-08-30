# -*- coding: utf-8 -*-
"""De onde vem a notícia do mini-podcast: os feeds RSS das fontes fixas.

REGRA 2, aplicada aqui: **o fato vem do feed, nunca do modelo.** Título, data
e link saem do XML; o LLM, quando entra (`podcast.locucao`), só reescreve pra
soar falado. Deixar o modelo "lembrar" a notícia da semana é como se inventa
manchete — ele preenche o que não sabe, e com voz de locutor soa verdade.

TRÊS DECISÕES QUE VALEM MAIS QUE O CÓDIGO:

1. SÓ OS FEEDS DECLARADOS em `podcast.NICHOS`. Nada de descobrir fonte nova
   sozinho: a promessa do áudio é "dá pra conferir", e conferir só funciona
   se a lista for curta e conhecida.

2. SÓ O QUE É DESTA SEMANA. Notícia de vinte dias atrás num "resumo da
   semana" é o bot mostrando que não olhou. Fora da janela, descarta.

3. FALHA DE REDE NÃO INVENTA NADA. Feed fora do ar vira zero item daquela
   fonte, e o episódio sai com as outras duas — ou não sai. Silêncio é uma
   resposta honesta; manchete inventada não.

`xml.etree` da stdlib em vez de `feedparser`: dependência nova num Dockerfile
que já builda é risco sem retorno, e RSS/Atom são XML simples.
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import podcast
import tempo

log = logging.getLogger("resolveai")

# Quantos dias pra trás ainda contam como "desta semana". Sete, com folga de
# um dia porque o episódio pode sair na segunda de manhã sobre a semana que
# fechou no domingo.
DIAS_DE_FRESCOR = 8

# Teto por fonte.
#
# Era 8 e o Kevin achou o buraco ouvindo a primeira amostra: com pouco
# candidato, os três blocos acabavam sendo as três matérias mais recentes —
# que num sábado à noite são o mesmo jogo em três manchetes. Com 20 por fonte
# (60 no total) há semana de verdade pra escolher: transferência de segunda,
# polêmica de quarta, jogo de sábado.
#
# O custo é memória de uma lista, não requisição: é o MESMO download de feed.
POR_FONTE = 20

TIMEOUT_S = 12

# User-Agent honesto. Alguns sites recusam cliente sem identificação, e
# fingir ser navegador seria mentir sobre quem está batendo na porta.
UA = {"User-Agent": "Mozilla/5.0 (compatible; ResolveAI/1.0; leitor de RSS)"}

_TAG_RE = re.compile(r"<[^>]+>")
_ESPACO_RE = re.compile(r"\s+")

# Formatos de data que RSS e Atom usam na prática.
_FORMATOS = (
    "%a, %d %b %Y %H:%M:%S %z",      # RFC 822 com offset
    "%a, %d %b %Y %H:%M:%S %Z",      # RFC 822 com nome de zona
    "%a, %d %b %Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",           # ISO/Atom
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _limpar(bruto: Optional[str]) -> str:
    """HTML do resumo -> texto que dá pra ler em voz alta."""
    if not bruto:
        return ""
    t = html.unescape(str(bruto))
    t = _TAG_RE.sub(" ", t)
    t = html.unescape(t)          # entidade dentro de tag some só no 2o passe
    return _ESPACO_RE.sub(" ", t).strip()


def _data(bruto: Optional[str]) -> Optional[datetime]:
    """Data do item, sem timezone. None quando o feed não diz."""
    if not bruto:
        return None
    t = str(bruto).strip()
    for forma in _FORMATOS:
        try:
            d = datetime.strptime(t, forma)
            return d.replace(tzinfo=None) if d.tzinfo else d
        except ValueError:
            continue
    # Alguns feeds põem "GMT" onde o strptime espera offset numérico.
    try:
        return datetime.strptime(
            re.sub(r"\s*(GMT|UTC)$", " +0000", t),
            "%a, %d %b %Y %H:%M:%S %z").replace(tzinfo=None)
    except ValueError:
        return None


def _texto(elem, *nomes) -> str:
    """Primeiro filho que existir, ignorando namespace do Atom."""
    for nome in nomes:
        for filho in elem:
            tag = filho.tag.split("}")[-1].lower()
            if tag == nome:
                if filho.text and filho.text.strip():
                    return filho.text
                # Atom usa <link href="...">
                if filho.attrib.get("href"):
                    return filho.attrib["href"]
    return ""


def parse_feed(xml_bruto: str, fonte: str,
               agora: Optional[datetime] = None) -> list[dict]:
    """XML de RSS/Atom -> lista de {"titulo","resumo","fonte","link","data"}.

    Separado do download de propósito: assim o teste exercita o parser de
    verdade, com XML de verdade, sem tocar na rede. Parser testado só contra
    o que ele mesmo gera é parser não testado.
    """
    if not xml_bruto:
        return []
    bruto = xml_bruto.strip()
    try:
        raiz = ET.fromstring(bruto)
    except ET.ParseError as e:
        # LIXO DEPOIS DO FECHAMENTO E COMUM E NAO PODE CUSTAR A FONTE.
        #
        # O feed do Mercado&Consumo devolve HTML de rodape depois do
        # `</rss>` ("junk after document element"), e isso zerava a fonte
        # inteira — num nicho de tres fontes, um terco do material. Cortar no
        # ultimo fechamento e o conserto honesto: o que vem depois nao e o
        # feed.
        raiz = None
        for fecho in ("</rss>", "</feed>", "</rdf:RDF>"):
            corte = bruto.rfind(fecho)
            if corte > 0:
                try:
                    raiz = ET.fromstring(bruto[:corte + len(fecho)])
                    log.info("[noticias] feed de %s tinha lixo no fim — "
                             "cortado", fonte)
                    break
                except ET.ParseError:
                    continue
        if raiz is None:
            log.warning("[noticias] feed de %s nao e XML valido: %s", fonte, e)
            return []

    ref = agora or tempo.agora()
    corte = ref - timedelta(days=DIAS_DE_FRESCOR)
    itens = []
    for elem in raiz.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        titulo = _limpar(_texto(elem, "title"))
        if not titulo:
            continue
        quando = _data(_texto(elem, "pubdate", "published", "updated", "date"))
        # SEM DATA, ENTRA. Feed que não datou o item não é motivo pra perder
        # a notícia — e a ordem do RSS já é do mais novo pro mais velho.
        if quando and quando < corte:
            continue
        itens.append({
            "titulo": titulo,
            "resumo": _limpar(_texto(elem, "description", "summary",
                                     "content"))[:400],
            "fonte": fonte,
            "link": _limpar(_texto(elem, "link")),
            "data": quando.isoformat() if quando else None,
        })
        if len(itens) >= POR_FONTE:
            break
    return itens


# Teto de download. Feed de RSS honesto tem centenas de KB; 5 MB e folga de
# uma ordem de grandeza. Sem teto, um feed de 43 MB fazia o parse subir a
# 125 MB de memoria — no MESMO container que atende o webhook, ou seja, a
# conversa da pessoa competindo com o download de um site de terceiro.
MAX_FEED_BYTES = 5 * 1024 * 1024


def _baixar(url: str) -> str:
    import httpx
    with httpx.stream("GET", url, headers=UA, timeout=TIMEOUT_S,
                      follow_redirects=True) as r:
        r.raise_for_status()
        pedacos, total = [], 0
        for pedaco in r.iter_bytes():
            total += len(pedaco)
            if total > MAX_FEED_BYTES:
                log.warning("[noticias] %s passou de %d bytes — cortado",
                            url, MAX_FEED_BYTES)
                break
            pedacos.append(pedaco)
    bruto = b"".join(pedacos)
    return bruto.decode(r.encoding or "utf-8", errors="replace")


def buscar(nicho: Optional[str], agora: Optional[datetime] = None,
           baixar=None) -> list[dict]:
    """As notícias da semana do nicho, das três fontes. [] quando não há.

    `baixar` é injetável pra teste — nenhum teste desta base toca a rede.

    UMA FONTE FORA DO AR NÃO DERRUBA O EPISÓDIO: cada feed é tentado dentro
    do seu próprio try, e o que falhou vira zero item daquela fonte. Um
    `raise` aqui deixaria a pessoa sem áudio porque um site de terceiro caiu.
    """
    k = podcast.nicho_valido(nicho)
    if not k:
        return []
    fetch = baixar or _baixar
    ref = agora or tempo.agora()

    achados: list[dict] = []
    for nome, _pagina, rss in podcast.NICHOS[k]["fontes"]:
        try:
            achados.extend(parse_feed(fetch(rss), nome, agora=ref))
        except Exception as e:
            log.warning("[noticias] %s (%s) falhou: %r", nome, rss, e)

    # INTERCALA AS FONTES em vez de concatenar. Sem isto, as três notícias do
    # roteiro sairiam todas do mesmo site sempre que ele publicasse mais —
    # e o áudio prometeria três fontes citando uma.
    por_fonte: dict = {}
    for it in achados:
        por_fonte.setdefault(it["fonte"], []).append(it)
    saida, i = [], 0
    while any(len(v) > i for v in por_fonte.values()):
        for nome in [f[0] for f in podcast.NICHOS[k]["fontes"]]:
            fila = por_fonte.get(nome) or []
            if len(fila) > i:
                saida.append(fila[i])
        i += 1
    return _sem_repetido(saida)


def _sem_repetido(itens: list) -> list:
    """Mesma notícia em duas fontes vira um bloco só.

    Compara pelo título normalizado: no futebol as três fontes cobrem o mesmo
    jogo, e um "resumo em três blocos" com o mesmo placar três vezes é o
    áudio se desmentindo sozinho.
    """
    vistos, saida = set(), []
    for it in itens:
        chave = _ESPACO_RE.sub(" ", (it.get("titulo") or "").lower()).strip()
        chave = re.sub(r"[^\wà-ÿ ]+", "", chave)[:60]
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        saida.append(it)
    return saida


def verificar(baixar=None) -> list[dict]:
    """Bate em todos os feeds e diz quais respondem. Diagnóstico, não rotina.

    Existe porque site troca de endereço sem avisar, e feed morto vira
    episódio que nunca sai — em silêncio, que é como uma feature morre sem
    ninguém perceber. O resultado aparece no /health.
    """
    fetch = baixar or _baixar
    saida = []
    for chave, dados in podcast.NICHOS.items():
        for nome, _pagina, rss in dados["fontes"]:
            try:
                n = len(parse_feed(fetch(rss), nome))
                saida.append({"nicho": chave, "fonte": nome, "rss": rss,
                              "itens": n, "ok": n > 0,
                              "erro": "" if n else "feed vazio"})
            except Exception as e:
                saida.append({"nicho": chave, "fonte": nome, "rss": rss,
                              "itens": 0, "ok": False, "erro": repr(e)[:120]})
    return saida
