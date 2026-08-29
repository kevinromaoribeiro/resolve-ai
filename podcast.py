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
NICHOS = {
    "futebol": {
        "rotulo": "Futebol",
        "emoji": "⚽",
        "fontes": (
            ("ge.globo", "https://ge.globo.com/futebol/"),
            ("ESPN Brasil", "https://www.espn.com.br/futebol/"),
            ("Lance!", "https://www.lance.com.br/"),
        ),
        "assuntos": ("resultados da rodada", "contratações",
                     "tabela e classificação", "lesões que mudam escalação"),
    },
    "games": {
        "rotulo": "Games",
        "emoji": "🎮",
        "fontes": (
            ("IGN Brasil", "https://br.ign.com/"),
            ("The Enemy", "https://www.theenemy.com.br/"),
            ("Adrenaline", "https://www.adrenaline.com.br/games/"),
        ),
        "assuntos": ("lançamentos da semana", "promoções que valem a pena",
                     "atualizações grandes", "o que saiu de graça"),
    },
    "ia": {
        "rotulo": "Inteligência artificial",
        "emoji": "🤖",
        "fontes": (
            ("Canaltech IA", "https://canaltech.com.br/inteligencia-artificial/"),
            ("Olhar Digital", "https://olhardigital.com.br/editorias/pro/"),
            ("MIT Technology Review Brasil", "https://mittechreview.com.br/"),
        ),
        "assuntos": ("ferramenta nova que dá pra usar hoje",
                     "o que mudou nos modelos", "impacto no trabalho",
                     "golpe e cuidado com IA"),
    },
    "moda": {
        "rotulo": "Moda",
        "emoji": "👗",
        "fontes": (
            ("Vogue Brasil", "https://vogue.globo.com/moda/"),
            ("Elle Brasil", "https://elle.com.br/moda"),
            ("FFW", "https://ffw.uol.com.br/"),
        ),
        "assuntos": ("tendência da estação", "o que saiu nas passarelas",
                     "peça-chave do mês", "quem está usando o quê"),
    },
    "varejo online": {
        "rotulo": "Varejo online",
        "emoji": "🛍️",
        "fontes": (
            ("E-Commerce Brasil", "https://www.ecommercebrasil.com.br/"),
            ("Mercado&Consumo", "https://mercadoeconsumo.com.br/"),
            ("NeoFeed varejo", "https://neofeed.com.br/varejo/"),
        ),
        "assuntos": ("data de promoção chegando", "mudança de frete e prazo",
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
    permitidas = {f[0].lower() for f in d["fontes"]}

    bons = []
    for it in itens:
        if not isinstance(it, dict):
            continue
        titulo = (it.get("titulo") or "").strip()
        fonte = (it.get("fonte") or "").strip()
        if not titulo or not fonte:
            continue
        # FONTE FORA DA LISTA NÃO ENTRA. A lista existe pra que a pessoa possa
        # conferir; aceitar qualquer fonte devolveria o problema que ela
        # resolve.
        if fonte.lower() not in permitidas:
            continue
        bons.append({"titulo": titulo,
                     "resumo": (it.get("resumo") or "").strip(),
                     "fonte": fonte})
        if len(bons) >= BLOCOS:
            break
    if not bons:
        return None

    primeiro = (nome or "").split()[0] if nome else ""
    saudacao = f"Oi, {primeiro}!" if primeiro else "Oi!"
    partes = [
        f"{saudacao} Seu resumo de {d['rotulo'].lower()} da semana, "
        f"em três minutos.",
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

    roteiro = "\n".join(partes).strip()

    # CORTE PELO FIM, NUNCA PELO MEIO. Passar do teto é quebrar a promessa dos
    # três minutos; cortar uma notícia inteira mantém o áudio coerente, e
    # cortar no meio de uma frase deixa o ouvinte no ar.
    while _conta_palavras(roteiro) > PALAVRAS_TETO and len(bons) > 1:
        bons.pop()
        return montar_roteiro(k, bons, nome)
    return roteiro


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
    if not ultimo_envio_iso:
        return True
    ref = agora or tempo.agora()
    try:
        texto = str(ultimo_envio_iso)[:19].replace("T", " ")
        from datetime import datetime
        ultimo = datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        # Data ilegível conta como "acabou de enviar": o erro seguro aqui é
        # mandar de menos.
        return False
    return (ref - ultimo) >= timedelta(days=DIAS_ENTRE_EPISODIOS)
