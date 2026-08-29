# -*- coding: utf-8 -*-
"""Mini-podcast semanal de 3 minutos, um nicho por pessoa.

Ideia do Kevin (28-29/08/2026): "um áudio de 3 min, estilo podcast, com as
notícias da semana do assunto que a pessoa escolheu". Um nicho por pessoa, no
máximo 1x por semana — quatro áudios por mês, teto duro.

POR QUE ISTO EXISTE NUM BOT DE LEMBRETE: o produto sofre de um problema de
frequência. Quem tem duas contas por mês fala com o bot duas vezes por mês, e
quem fala pouco esquece que assinou. O áudio dá um motivo semanal de abrir a
conversa — e, ao contrário de "dica do dia", ele é conteúdo que a pessoa
escolheu receber.

O QUE ESTE MÓDULO FAZ E O QUE NÃO FAZ
    Faz:  a estrutura do roteiro, as fontes por nicho, o corte de duração, o
          controle de "já mandei esta semana" e o texto das perguntas.
    Não faz: gerar áudio. A síntese de voz é um serviço externo com custo por
          minuto, e a decisão de qual usar é do Kevin — ele vai testar na mão
          antes. `montar_roteiro` entrega o texto pronto pra TTS.

REGRAS QUE NÃO SÃO NEGOCIÁVEIS, e cada uma custou uma discussão:

1. UM NICHO POR PESSOA. Não é preferência de UI, é freio de volume: com dois
   nichos o teto semanal viraria dois áudios, e áudio é a mensagem mais
   intrusiva que existe no WhatsApp.

2. NUNCA MANDA SEM PERGUNTAR. O bot pergunta "seu mini podcast está pronto,
   quer ouvir?" e só manda depois do sim. Áudio de 3 min que chega sozinho no
   meio da reunião é o tipo de coisa que faz a pessoa bloquear o número.

3. A PERGUNTA VIVE DENTRO DA JANELA DE 24H. Não há template de podcast, e não
   vai haver: seria marketing, e marketing neste número é o que a régua da
   Meta pune. Fora da janela, o convite simplesmente não sai naquela semana.

4. TODA AFIRMAÇÃO TEM FONTE. O roteiro termina citando de onde veio, e as
   fontes são fixas por nicho (abaixo). Áudio gerado por IA sem fonte é como
   a gente perde a confiança de alguém de uma vez só.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from typing import Optional

import tempo

# ---------------------------------------------------------------------------
# OS CINCO NICHOS E DE ONDE VEM A NOTÍCIA
# ---------------------------------------------------------------------------
# Três fontes por nicho, escolhidas por três critérios: publicam em português,
# publicam todo dia, e são verificáveis (a pessoa pode abrir e conferir).
#
# São FIXAS no código de propósito. Deixar o modelo escolher a fonte é como se
# inventa manchete: ele preenche o que não sabe. Aqui ele só resume o que veio
# destes lugares, e o roteiro diz quais foram.
#
# O Kevin decidiu estes cinco em 29/08/2026; os outros nove ficam pro Motor 4.
# Cada fonte e (nome, pagina, feed RSS). O FEED FOI CONFERIDO CONTRA A REDE
# em 29/08/2026, um por um — feed adivinhado que nao existe vira episodio que
# nunca sai, em silencio, e ninguem descobre.
#
# Quatro nomes da primeira versao cairam por nao terem RSS que responde:
# Lance!, The Enemy, Elle Brasil e E-Commerce Brasil. Entraram no lugar
# Gazeta Esportiva, Critical Hits, Steal the Look e Consumidor Moderno — do
# mesmo nicho e com feed vivo. `noticias.verificar()` reconfere quando o
# Kevin quiser, porque site troca de endereco.
NICHOS = {
    "futebol": {
        "rotulo": "Futebol",
        "emoji": "⚽",
        "fontes": (
            ("ge.globo", "https://ge.globo.com/futebol/",
             "https://ge.globo.com/rss/ge/futebol/"),
            ("ESPN Brasil", "https://www.espn.com.br/futebol/",
             "https://www.espn.com.br/rss/futebol"),
            ("Gazeta Esportiva", "https://www.gazetaesportiva.com/",
             "https://www.gazetaesportiva.com/feed/"),
        ),
        "assuntos": ("resultados da rodada", "contratações",
                     "tabela e classificação",
                     "lesões que mudam escalação"),
    },
    "games": {
        "rotulo": "Games",
        "emoji": "🎮",
        "fontes": (
            ("IGN Brasil", "https://br.ign.com/", "https://br.ign.com/feed.xml"),
            ("Adrenaline", "https://www.adrenaline.com.br/games/",
             "https://www.adrenaline.com.br/feed/"),
            ("Critical Hits", "https://criticalhits.com.br/",
             "https://criticalhits.com.br/feed/"),
        ),
        "assuntos": ("lançamentos da semana",
                     "promoções que valem a pena",
                     "atualizações grandes", "o que saiu de graça"),
    },
    "ia": {
        "rotulo": "Inteligência artificial",
        "emoji": "🤖",
        "fontes": (
            ("Canaltech IA", "https://canaltech.com.br/inteligencia-artificial/",
             "https://canaltech.com.br/rss/"),
            ("Olhar Digital",
             "https://olhardigital.com.br/tag/inteligencia-artificial/",
             "https://olhardigital.com.br/feed/"),
            ("MIT Technology Review Brasil", "https://mittechreview.com.br/",
             "https://mittechreview.com.br/feed/"),
        ),
        "assuntos": ("ferramenta nova que dá pra usar hoje",
                     "o que mudou nos modelos", "impacto no trabalho",
                     "golpe e cuidado com IA"),
    },
    "moda": {
        "rotulo": "Moda",
        "emoji": "👗",
        "fontes": (
            ("Vogue Brasil", "https://vogue.globo.com/moda/",
             "https://pox.globo.com/rss/vogue/"),
            ("FFW", "https://ffw.uol.com.br/", "https://ffw.uol.com.br/rss/"),
            ("Steal the Look", "https://stealthelook.com.br/",
             "https://stealthelook.com.br/feed/"),
        ),
        "assuntos": ("tendência da estação",
                     "o que saiu nas passarelas",
                     "peça-chave do mês", "quem está usando o quê"),
    },
    "varejo online": {
        "rotulo": "Varejo online",
        "emoji": "🛍️",
        "fontes": (
            ("Mercado&Consumo", "https://mercadoeconsumo.com.br/",
             "https://mercadoeconsumo.com.br/feed/"),
            ("NeoFeed varejo", "https://neofeed.com.br/varejo/",
             "https://neofeed.com.br/feed/"),
            ("Consumidor Moderno", "https://consumidormoderno.com.br/",
             "https://consumidormoderno.com.br/feed/"),
        ),
        "assuntos": ("data de promoção chegando",
                     "mudança de frete e prazo",
                     "o que subiu e o que caiu de preço",
                     "novidade das grandes lojas"),
    },
}

# ---------------------------------------------------------------------------
# O FORMATO DE 3 MINUTOS
# ---------------------------------------------------------------------------
# Locução em português brasileiro roda perto de 150 palavras por minuto. Três
# minutos são ~450 palavras — e o teto é DURO: áudio que promete 3 min e
# entrega 6 é a primeira coisa que faz alguém desativar o recurso.
PALAVRAS_POR_MINUTO = 150
DURACAO_ALVO_MIN = 3
PALAVRAS_ALVO = PALAVRAS_POR_MINUTO * DURACAO_ALVO_MIN     # 450
PALAVRAS_TETO = int(PALAVRAS_ALVO * 1.15)                  # 517: 3min27

# Três blocos. Menos que isso vira nota solta; mais que isso não cabe em 3 min
# sem virar manchete lida em voz alta.
BLOCOS = 3

# ---------------------------------------------------------------------------
# O FLUXO NO TEMPO (desenho do Kevin, 29/08/2026)
# ---------------------------------------------------------------------------
# Landing coleta o nicho -> 6h depois do cadastro o bot pergunta se a pessoa
# quer ouvir -> manda o áudio -> 10 min depois pergunta em que dia ela prefere
# receber toda semana.
HORAS_ATE_O_CONVITE = 6
MINUTOS_ATE_PERGUNTAR_O_DIA = 10
DIAS_ENTRE_EPISODIOS = 7

BOTOES_CONVITE = ["Quero ouvir", "Agora não", "Não quero mais"]
BOTOES_DIA = ["Segunda", "Sexta", "Domingo"]


def _chave(nicho: Optional[str]) -> Optional[str]:
    """Normaliza o que veio da landing ou do botão. None se não conhecemos.

    Aceita "IA", "Inteligência artificial", "inteligencia-artificial": a
    landing manda o rótulo bonito e o botão manda outra coisa, e um `KeyError`
    aqui viraria pessoa cadastrada sem nicho nenhum.
    """
    if not nicho:
        return None
    t = "".join(c for c in unicodedata.normalize("NFD", str(nicho))
                if unicodedata.category(c) != "Mn").lower()
    t = re.sub(r"[^a-z ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t in ("ia", "inteligencia artificial", "inteligencia"):
        return "ia"
    for chave, dados in NICHOS.items():
        alvo = "".join(c for c in unicodedata.normalize("NFD", dados["rotulo"])
                       if unicodedata.category(c) != "Mn").lower()
        if t == chave or t == alvo:
            return chave
    return None


def nicho_valido(nicho: Optional[str]) -> Optional[str]:
    """A chave canônica do nicho, ou None. Porta única de entrada."""
    return _chave(nicho)


def fontes(nicho: Optional[str]) -> tuple:
    k = _chave(nicho)
    return NICHOS[k]["fontes"] if k else ()


def rotulo(nicho: Optional[str]) -> str:
    k = _chave(nicho)
    return NICHOS[k]["rotulo"] if k else ""


# ---------------------------------------------------------------------------
# O ROTEIRO
# ---------------------------------------------------------------------------
def briefing(nicho: Optional[str]) -> Optional[dict]:
    """O que o gerador de roteiro precisa saber. None se o nicho não existe.

    É isto que vai pro prompt: as fontes permitidas, os assuntos que
    interessam, o teto de palavras e o formato. O modelo não escolhe fonte e
    não escolhe duração — ele preenche uma estrutura que já está decidida.
    """
    k = _chave(nicho)
    if not k:
        return None
    d = NICHOS[k]
    return {
        "nicho": k,
        "rotulo": d["rotulo"],
        "emoji": d["emoji"],
        "fontes": list(d["fontes"]),
        "assuntos": list(d["assuntos"]),
        "blocos": BLOCOS,
        "palavras_alvo": PALAVRAS_ALVO,
        "palavras_teto": PALAVRAS_TETO,
        "duracao_min": DURACAO_ALVO_MIN,
    }


def _dominio(url: str) -> str:
    """"https://ge.globo.com/futebol/" -> "ge.globo.com"."""
    t = re.sub(r"^https?://", "", (url or "").strip().lower())
    t = re.sub(r"^www\.", "", t)
    return t.split("/")[0]


def _conta_palavras(texto: str) -> int:
    return len([p for p in re.split(r"\s+", (texto or "").strip()) if p])


def duracao_estimada_s(texto: str) -> int:
    """Quantos segundos de locução este roteiro dá."""
    return int(round(_conta_palavras(texto) * 60.0 / PALAVRAS_POR_MINUTO))


def montar_roteiro(nicho: Optional[str], itens: Optional[list],
                   nome: str = "") -> Optional[str]:
    """Roteiro pronto pra síntese de voz. None sem nicho ou sem notícia.

    `itens` é uma lista de dicts {"titulo", "resumo", "fonte"} — o que veio
    das fontes desta semana. Uma notícia sem fonte é DESCARTADA aqui, não
    "consertada": o áudio não pode afirmar o que não dá pra conferir.

    Devolve None quando não sobrou notícia nenhuma. Silêncio é melhor que um
    episódio de trinta segundos dizendo que não houve novidade — isso ensina a
    pessoa a desativar.
    """
    k = _chave(nicho)
    if not k or not itens:
        return None
    d = NICHOS[k]
    bons = _validos(k, itens)[:BLOCOS]
    if not bons:
        return None

    roteiro = _montar(d, bons, nome)

    # CORTE PELO FIM, NUNCA PELO MEIO. Passar do teto é quebrar a promessa dos
    # três minutos; cortar uma notícia inteira mantém o áudio coerente, e
    # cortar no meio de uma frase deixa o ouvinte no ar.
    while _conta_palavras(roteiro) > PALAVRAS_TETO and len(bons) > 1:
        bons.pop()
        roteiro = _montar(d, bons, nome)

    # COM UMA NOTÍCIA SÓ NÃO DÁ PRA CORTAR BLOCO — E O TETO CONTINUA VALENDO
    # (auditoria M4.0). Sem isto, um resumo gigante virava um "áudio de três
    # minutos" de onze horas: a promessa quebrada, e TTS é cobrado por
    # minuto. Aqui o resumo encurta até caber, sempre terminando em frase
    # fechada; se nem assim couber, fica só a manchete.
    if _conta_palavras(roteiro) > PALAVRAS_TETO:
        unico = dict(bons[0])
        unico["resumo"] = _encurtar(unico["resumo"], PALAVRAS_TETO // 2)
        roteiro = _montar(d, [unico], nome)
        if _conta_palavras(roteiro) > PALAVRAS_TETO:
            unico["resumo"] = ""
            roteiro = _montar(d, [unico], nome)
    return roteiro


def _validos(k: str, itens: list) -> list:
    """Só o que tem título E fonte da lista. Usado pelo roteiro e pela locução.

    A FONTE PODE CHEGAR COMO NOME OU COMO URL (auditoria M4.0): um scraper
    devolve "ge.globo.com" ou a URL inteira, não o rótulo bonito — e casar só
    por nome exato transformava isso em episódio vazio, em silêncio.

    Fonte de fora não entra: a lista existe pra que a pessoa possa conferir, e
    aceitar qualquer fonte devolveria o problema que ela resolve.
    """
    d = NICHOS[k]
    permitidas = set()
    for _f in d["fontes"]:
        permitidas.add(_f[0].lower())
        permitidas.add(_dominio(_f[1]))

    bons = []
    for it in itens or []:
        if not isinstance(it, dict):
            continue
        titulo = (it.get("titulo") or "").strip()
        fonte = (it.get("fonte") or "").strip()
        if not titulo or not fonte:
            continue
        if fonte.lower() not in permitidas and _dominio(fonte) not in permitidas:
            continue
        bons.append({"titulo": titulo,
                     "resumo": (it.get("resumo") or "").strip(),
                     "fonte": fonte})
    return bons


def _montar(d: dict, bons: list, nome: str) -> str:
    """Monta o texto. Separado do corte pra que remontar não recurse."""
    primeiro = (nome or "").split()[0] if nome else ""
    saudacao = f"Oi, {primeiro}!" if primeiro else "Oi!"
    partes = [
        # SEM PROMETER MINUTAGEM (auditoria M4.2, P2-8): o roteiro
        # deterministico sai entre 40s e 2 min, e um audio que se anuncia
        # como "três minutos" e entrega quarenta segundos e o produto
        # errando pra menos na primeira frase.
        f"{saudacao} Seu resumo de {d['rotulo'].lower()} da semana.",
        "",
    ]
    for i, it in enumerate(bons, 1):
        partes.append(f"{i}. {it['titulo']}.")
        if it["resumo"]:
            partes.append(it["resumo"])
        partes.append("")

    citadas = []
    for it in bons:
        if it["fonte"] not in citadas:
            citadas.append(it["fonte"])
    partes.append("Isso foi o que saiu em " + _lista(citadas) + ".")
    partes.append("Semana que vem eu te trago mais. Até lá!")
    return "\n".join(partes).strip()


def _encurtar(texto: str, palavras: int) -> str:
    """Corta o resumo terminando em FRASE FECHADA, nunca no meio.

    Se nem a primeira frase couber, devolve vazio — manchete sozinha é um
    áudio honesto; meia frase é o ouvinte no ar.
    """
    saida = []
    for frase in re.split(r"(?<=[.!?])\s+", (texto or "").strip()):
        if _conta_palavras(" ".join(saida + [frase])) > palavras:
            break
        saida.append(frase)
    return " ".join(saida).strip()


def _lista(nomes: list) -> str:
    if not nomes:
        return "nossas fontes"
    if len(nomes) == 1:
        return nomes[0]
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


# ---------------------------------------------------------------------------
# AS PERGUNTAS
# ---------------------------------------------------------------------------
def convite(nicho: Optional[str], nome: str = "") -> Optional[dict]:
    """"Seu podcast está pronto, quer ouvir?" — com botão. None sem nicho."""
    k = _chave(nicho)
    if not k:
        return None
    d = NICHOS[k]
    primeiro = (nome or "").split()[0] if nome else ""
    return {
        "texto": (f"{d['emoji']} {('Bom dia, ' + primeiro + '! ') if primeiro else ''}"
                  f"Seu mini podcast de *{d['rotulo'].lower()}* da semana "
                  f"está pronto — 3 minutos.\n\n"
                  f"Quer ouvir agora?"),
        "botoes": list(BOTOES_CONVITE),
        "nicho": k,
    }


def pergunta_do_dia(nome: str = "") -> dict:
    """Depois do primeiro áudio: em que dia mandar toda semana.

    Vem 10 minutos DEPOIS do áudio, não junto: perguntar antes de a pessoa
    ouvir é pedir compromisso sobre algo que ela ainda não sabe se gosta.
    """
    primeiro = (nome or "").split()[0] if nome else ""
    return {
        "texto": (f"{('E aí, ' + primeiro + '? ') if primeiro else ''}"
                  f"Curtiu?\n\n"
                  f"Se quiser, eu mando um desses toda semana. "
                  f"Que dia é melhor pra você?"),
        "botoes": list(BOTOES_DIA),
    }


def pode_enviar(ultimo_envio_iso: Optional[str],
                agora=None) -> bool:
    """Passou uma semana desde o último episódio?

    TETO DURO, não sugestão: o Kevin definiu no máximo 1x por semana, quatro
    por mês. Áudio é a mensagem mais intrusiva do WhatsApp, e este número já
    foi restringido duas vezes.

    Sem registro de envio, pode: é o primeiro episódio.
    """
    from datetime import date as _date, datetime as _datetime
    if not ultimo_envio_iso:
        return True
    ref = agora or tempo.agora()
    if isinstance(ref, _date) and not isinstance(ref, _datetime):
        # `tempo.hoje()` devolve `date`, e subtrair `date` de `datetime`
        # estoura TypeError FORA do try — o cron inteiro morreria por causa
        # de um argumento (auditoria M4.0).
        ref = _datetime(ref.year, ref.month, ref.day)

    # ACEITA O QUE O BANCO REALMENTE DEVOLVE, não só a string do formato
    # exato. Se um dia a coluna virar `datetime` (ou vier com timezone, ou
    # com microssegundos), a versão anterior devolvia False pra sempre — e o
    # podcast morria PERMANENTE E CALADO, que é o pior tipo de defeito
    # porque ninguém vai procurar.
    # `str()` DA CONTA DE TUDO: `datetime` vira "2026-09-01 10:00:00",
    # `date` vira "2026-09-01", timezone e microssegundos saem no regex
    # abaixo. Os ramos `isinstance` que estavam aqui eram codigo morto —
    # nenhum teste conseguia distinguir a presenca deles, e ramo que teste
    # nenhum alcanca e ramo que ninguem mantem (auditoria M4.0).
    if True:
        texto = str(ultimo_envio_iso).strip().replace("T", " ")
        texto = re.sub(r"(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$", "", texto)[:19]
        ultimo = None
        for forma in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                ultimo = _datetime.strptime(texto, forma)
                break
            except ValueError:
                continue
        if ultimo is None:
            # Data ilegível conta como "acabou de enviar": o erro seguro aqui
            # é mandar de MENOS. Mas ela sai no log — silêncio permanente sem
            # rastro é como uma feature morre sem ninguém perceber.
            import logging
            logging.getLogger("resolveai").warning(
                "[podcast] data de ultimo envio ilegivel: %r",
                ultimo_envio_iso)
            return False
    if ultimo.tzinfo is not None:
        ultimo = ultimo.replace(tzinfo=None)
    return (ref - ultimo) >= timedelta(days=DIAS_ENTRE_EPISODIOS)


# ---------------------------------------------------------------------------
# A LOCUÇÃO: o LLM reescreve pra soar falado, e NÃO acrescenta fato nenhum
# ---------------------------------------------------------------------------
# O roteiro determinístico acima lê o `description` do RSS em voz alta — e
# isso soa como jornal lido por robô, porque é texto ESCRITO. Locução é outra
# coisa: frase curta, sujeito antes do verbo, número arredondado.
#
# A DIVISÃO É A DE SEMPRE (regra 2): o LLM faz LÍNGUA, o Python faz FATO. Ele
# recebe só o que veio dos feeds e é proibido de acrescentar; o que ele
# devolve passa por uma conferência em Python antes de virar áudio. Se a
# conferência reprovar, cai no roteiro determinístico — que é feio mas é
# verdadeiro. Áudio com voz de locutor afirmando o que ninguém verificou é o
# jeito mais rápido de perder a confiança de alguém.

_PROMPT_LOCUCAO = """Você escreve o roteiro de um mini-podcast de 3 minutos \
em português do Brasil, sobre {rotulo}.

MATÉRIA-PRIMA (é tudo o que você sabe; não existe mais nada):
{materia}

REGRAS, nesta ordem de importância:
1. NÃO INVENTE. Não acrescente placar, número, nome, data ou consequência que \
não esteja na matéria-prima acima. Se um item está vago, mantenha vago.
2. NÃO CITE fonte nenhuma além destas: {fontes}.
3. {blocos} blocos, na ordem dada. Cada bloco: uma frase que diz o que \
aconteceu e uma que diz por que importa.
4. Entre {minimo} e {alvo} palavras no total. Isso é o que cabe em 3 minutos.
5. Português falado: frases curtas, sem "outrossim", sem "vale ressaltar", \
sem manchete lida. Você está conversando, não lendo.
6. Abra cumprimentando {nome} e feche dizendo de quais fontes veio.

Devolva SOMENTE o texto do roteiro, sem título, sem marcação, sem aspas."""


def _prompt_de_locucao(nicho: str, itens: list, nome: str = "") -> str:
    d = NICHOS[nicho]
    linhas = []
    for i, it in enumerate(itens, 1):
        linhas.append("%d. [%s] %s" % (i, it["fonte"], it["titulo"]))
        if it.get("resumo"):
            linhas.append("   %s" % it["resumo"][:400])
    return _PROMPT_LOCUCAO.format(
        rotulo=d["rotulo"],
        materia="\n".join(linhas),
        fontes=", ".join(f[0] for f in d["fontes"]),
        blocos=min(BLOCOS, len(itens)),
        minimo=int(PALAVRAS_ALVO * 0.75),
        alvo=PALAVRAS_ALVO,
        nome=(nome or "").split()[0] if nome else "a pessoa",
    )


# Fonte inventada é o sintoma mais fácil de detectar de roteiro alucinado, e o
# mais caro: a pessoa vai conferir onde não existe.
_VEICULO_RE = re.compile(
    r"\b(globo|uol|folha|estad[ãa]o|g1|terra|r7|band|sbt|record|cnn|bbc|"
    r"reuters|bloomberg|forbes|exame|veja|isto[ée]|metropoles|"
    r"the\s+\w+|new\s+york\s+times)\b", re.I)


def conferir_locucao(texto: Optional[str], nicho: Optional[str],
                     materia: Optional[str] = None) -> Optional[str]:
    """O roteiro do LLM passa? Devolve o motivo da recusa, ou None se passa.

    Conferência em PYTHON, sobre o texto pronto — não confiança no prompt.
    "Não invente" é instrução, e instrução o modelo às vezes ignora; isto é
    verificação, e verificação não depende de boa vontade.
    """
    k = _chave(nicho)
    if not k:
        return "nicho desconhecido"
    if not texto or not texto.strip():
        return "roteiro vazio"
    n = _conta_palavras(texto)
    if n > PALAVRAS_TETO:
        return "passou de %d palavras (%d)" % (PALAVRAS_TETO, n)
    if n < 60:
        return "curto demais (%d palavras)" % n

    permitidas = {f[0].lower() for f in NICHOS[k]["fontes"]}
    permitidas |= {_dominio(f[1]) for f in NICHOS[k]["fontes"]}
    for achado in _VEICULO_RE.findall(texto):
        alvo = achado.lower().strip()
        if not any(alvo in p for p in permitidas):
            return "citou fonte de fora da lista: %r" % achado

    # NUMERO QUE NAO ESTAVA NA MATERIA-PRIMA (auditoria M4.2, P1-5).
    #
    # O teste de fonte pega o modelo citando a Folha; NAO pegava ele
    # afirmando "venceu por 7 a 0 e contratou o Messi por 300 milhoes" sem
    # citar ninguem — que e a alucinacao que realmente importa, porque a
    # pessoa nao tem como desconfiar.
    #
    # Placar, valor e idade sao o que o modelo mais inventa, e sao a parte
    # verificavel de graca: todo numero do roteiro tem que aparecer no que
    # veio dos feeds. Numero por extenso ("dois a um") escapa desta rede —
    # e por isso ela e uma camada, nao a unica.
    if materia is not None:
        # COMPARA VALOR, NAO TEXTO: "05" na fonte e "5" na locucao sao o
        # mesmo numero, e reprovar por causa do zero a esquerda seria a
        # conferencia brigando com a reescrita que ela existe pra permitir.
        fonte_num = {int(x) for x in
                     _NUMERO_RE.findall(_so_digitos_e_espaco(materia))}
        # SEM ISENCAO POR TAMANHO. A primeira versao liberava tudo ate 31
        # "porque e dia do mes" — e placar inventado ("venceu por 7 a 0"),
        # que era o caso do auditor, passava por essa porta. Numero pequeno e
        # justamente o que o modelo mais inventa.
        #
        # Reprovar aqui NAO perde o episodio: cai no roteiro deterministico,
        # que e feio e verdadeiro. Errar pro lado do texto simples e barato;
        # errar pro lado do placar inventado, nao.
        for n_ in _NUMERO_RE.findall(_so_digitos_e_espaco(texto)):
            if int(n_) not in fonte_num:
                return "numero %r nao veio das fontes" % n_
    return None


_NUMERO_RE = re.compile(r"\d+")


def _so_digitos_e_espaco(t: str) -> str:
    """Tira pontuacao de milhar/decimal pra comparar numero com numero.

    "R$ 1.200,50" e "1200" tem que casar: o roteiro reescreve o formato, e
    recusar por causa do ponto seria a conferencia brigando com a locucao.
    """
    t = re.sub(r"(?<=\d)[.,](?=\d)", "", t or "")
    # "05" na fonte e "5" na locucao sao o mesmo numero: recusar por causa do
    # zero a esquerda seria a conferencia brigando com a reescrita.
    return re.sub(r"0+(\d)", r"", t)


def locucao(nicho: Optional[str], itens: Optional[list], nome: str = "",
            chamar=None) -> Optional[str]:
    """Roteiro falado. Cai no determinístico se o LLM falhar ou reprovar.

    `chamar(prompt) -> str` é injetável: nenhum teste desta base chama modelo
    pago, e a conferência tem que ser testável com resposta ruim de propósito.
    """
    k = _chave(nicho)
    if not k or not itens:
        return None
    seguro = montar_roteiro(k, itens, nome)
    if not seguro:
        return None            # sem notícia válida não há episódio, e ponto

    usados = _validos(k, itens)[:BLOCOS]
    try:
        bruto = (chamar or _chamar_llm)(_prompt_de_locucao(k, usados, nome))
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[podcast] locucao falhou; vai o roteiro simples", exc_info=True)
        return seguro
    motivo = conferir_locucao(bruto, k, materia=_materia_bruta(usados))
    if motivo:
        import logging
        logging.getLogger("resolveai").warning(
            "[podcast] roteiro do LLM recusado (%s); vai o simples", motivo)
        return seguro
    return bruto.strip()


def _materia_bruta(itens: list) -> str:
    """Tudo o que veio dos feeds, junto. E o teto do que o roteiro
    pode afirmar."""
    return " ".join("%s %s" % (i.get("titulo") or "", i.get("resumo") or "")
                    for i in (itens or []))


def _chamar_llm(prompt: str) -> str:
    from litellm import completion
    import ai_engine
    resp = completion(model=ai_engine.LLM_MODEL, max_tokens=900,
                      messages=[{"role": "user", "content": prompt}])
    return resp.choices[0].message.content or ""
