# -*- coding: utf-8 -*-
"""M5.0 — dialogo de duas vozes, transicao entre assuntos, veto a venda."""
import io

NL = chr(10)
p = "podcast.py"
s = io.open(p, encoding="utf-8").read()

# =========================================================================
# 1. VETO A VENDA, em todos os nichos
# =========================================================================
v = '''_E_OFERTA_RE = re.compile(
    r"\\b(oferta|ofertas|promo[çc][ãa]o|promo[çc][õo]es|desconto|descontos|"
    r"cupom|cupons|menor\\s+pre[çc]o|mais\\s+barato|pechincha|"
    r"vale\\s+a\\s+pena\\s+comprar|onde\\s+comprar|compre|comprar\\s+agora|"'''
n = '''_E_OFERTA_RE = re.compile(
    r"\\b(oferta|ofertas|promo[çc][ãa]o|promo[çc][õo]es|desconto|descontos|"
    r"cupom|cupons|menor\\s+pre[çc]o|mais\\s+barato|pechincha|"
    r"vale\\s+a\\s+pena\\s+comprar|onde\\s+comprar|compre|comprar\\s+agora|"
    # "NOTICIA E PONTO" (Kevin, 30/08/2026). Nao e so o post de oferta: e
    # qualquer coisa que exista pra vender. Lista de produto, resenha com
    # link, "review", "testamos" e "os melhores X" sao formato de vitrine —
    # o veiculo ganha comissao, e o bot que promete lembrar conta nao pode
    # virar canal disso.
    r"review|resenha|an[áa]lise\\s+do\\s+produto|testamos|"
    r"os?\\s+\\d+\\s+melhores|melhores\\s+\\w+\\s+para\\s+comprar|"
    r"vale\\s+o\\s+investimento|custo-?benef[íi]cio|"'''
assert v in s, "veto venda"
s = s.replace(v, n, 1)

# IA: "automacao" e "algoritmo" soltos deixaram entrar materia sobre Uber
v2 = '''    "ia": (
        r"intelig[êe]ncia\\s+artificial|\\bia\\b|\\bai\\b|modelo\\s+de\\s+linguagem",
        r"chatgpt|openai|gemini|claude|anthropic|copilot|llama|deepseek",
        r"\\bllm\\b|algoritmo|machine\\s+learning|aprendizado\\s+de\\s+m[áa]quina",
        r"rede\\s+neural|chatbot|assistente\\s+virtual|automa[çc][ãa]o",
        r"gerad[oa]|generativ",
    ),'''
n2 = '''    # IA TEM QUE FALAR DE IA.
    #
    # "automacao" e "algoritmo" soltos deixaram entrar uma materia sobre
    # trabalhar de Uber — o Kevin ouviu e apontou. As duas palavras aparecem
    # em qualquer texto sobre plataforma, gig economy ou planilha. Ficaram as
    # marcas que so existem quando o assunto E inteligencia artificial.
    "ia": (
        r"intelig[êe]ncia\\s+artificial|\\bIAs?\\b|modelo\\s+de\\s+linguagem",
        r"chatgpt|openai|gemini|\\bclaude\\b|anthropic|copilot|llama|deepseek",
        r"\\bllm\\b|machine\\s+learning|aprendizado\\s+de\\s+m[áa]quina",
        r"rede\\s+neural|redes\\s+neurais|chatbot|deep\\s+learning",
        r"\\bIA\\s+generativa|generativ[ao]|modelo\\s+de\\s+IA|treinar\\s+modelo",
        r"nvidia|hugging\\s+face|midjourney|stable\\s+diffusion|sora\\b",
    ),'''
assert v2 in s, "ia"
s = s.replace(v2, n2, 1)

# =========================================================================
# 2. DIALOGO: duas pessoas conversando
# =========================================================================
v3 = "BOTOES = [\"Confirmar\", \"Ajustar\", \"Esquece\"]"
assert v3 not in s

ANC = "def _montar(d: dict, bons: list, nome: str) -> str:"
BLOCO = '''# ---------------------------------------------------------------------------
# QUEM CONVERSA NO EPISODIO
# ---------------------------------------------------------------------------
# Pedido do Kevin depois de ouvir a primeira amostra: "nao tem tom de podcast
# e nem 2 vozes discutindo sobre o tema; coloque sempre um homem e uma mulher,
# muito humanizado".
#
# Ele esta certo sobre a causa: uma voz so lendo tres paragrafos e locucao, e
# locucao soa robotica por mais natural que seja a voz. O que faz soar podcast
# nao e a voz — e a CONVERSA: um comenta, o outro reage, um pergunta, o outro
# responde. E isso muda o ROTEIRO, nao so a sintese.
#
# Nomes curtos e comuns de proposito: o TTS erra menos, e a pessoa que ouve
# nao tropeça em nome estranho na primeira frase.
APRESENTADORES = {"mulher": "Bia", "homem": "Léo"}

_FALA_RE = re.compile(r"^\\s*(BIA|L[ÉE]O)\\s*:\\s*(.+)$", re.I)


def falas(roteiro: Optional[str]) -> list:
    """Roteiro -> [("mulher"|"homem", texto)]. [] quando nao e dialogo.

    Lista vazia NAO e erro: e como o `voz` sabe que aquele texto e pra uma
    voz so (o roteiro deterministico antigo, ou qualquer outro uso).
    """
    saida = []
    for linha in (roteiro or "").splitlines():
        m = _FALA_RE.match(linha)
        if not m:
            continue
        texto = m.group(2).strip()
        if not texto:
            continue
        quem = "mulher" if m.group(1).upper() == "BIA" else "homem"
        saida.append((quem, texto))
    return saida


def _sem_marcacao(roteiro: Optional[str]) -> str:
    """O texto falado, sem os nomes na frente.

    A conferencia de alucinacao e o teto de palavras contam CONTEUDO; contar
    "BIA:" dezesseis vezes inflaria o total e reprovaria roteiro bom.
    """
    ditas = falas(roteiro)
    if ditas:
        return " ".join(t for _q, t in ditas)
    return roteiro or ""


'''
assert ANC in s
s = s.replace(ANC, BLOCO + ANC, 1)

# o roteiro deterministico vira conversa
v4 = '''def _montar(d: dict, bons: list, nome: str) -> str:
    """Monta o texto. Separado do corte pra que remontar não recurse."""
    primeiro = (nome or "").split()[0] if nome else ""
    saudacao = f"Oi, {primeiro}!" if primeiro else "Oi!"
    partes = [
        # SEM PROMETER MINUTAGEM (auditoria M4.2, P2-8): o roteiro
        # deterministico sai entre 40s e 2 min, e um audio que se anuncia
        # como "três minutos" e entrega quarenta segundos é o produto
        # errando pra menos na primeira frase.
        f"{saudacao} Seu resumo de {d['rotulo'].lower()} da semana.",
        "",
    ]
    for i, it in enumerate(bons, 1):
        quando = data_falada(it.get("data"))
        partes.append(f"{i}. {it['titulo']}"
                      + (f" — {quando}." if quando else "."))
        if it["resumo"]:
            partes.append(it["resumo"])
        partes.append("")

    citadas = []
    for it in bons:
        if it["fonte"] not in citadas:
            citadas.append(it["fonte"])
    partes.append("Isso foi o que saiu em " + _lista(citadas) + ".")
    partes.append("Semana que vem eu te trago mais. Até lá!")
    return "\\n".join(partes).strip()'''
n4 = '''def _montar(d: dict, bons: list, nome: str) -> str:
    """Monta a conversa. Separado do corte pra que remontar não recurse.

    Este é o roteiro DE RESERVA: sai quando o LLM falha ou quando o que ele
    escreveu é reprovado na conferência. Ele é mais seco que uma conversa de
    verdade — e é assim de propósito, porque ele só repete o que veio do
    feed, sem acrescentar nada.
    """
    bia, leo = APRESENTADORES["mulher"], APRESENTADORES["homem"]
    primeiro = (nome or "").split()[0] if nome else ""
    saudacao = f"Oi, {primeiro}!" if primeiro else "Oi!"
    linhas = [
        f"BIA: {saudacao} Aqui é a {bia}.",
        f"LEO: E eu sou o {leo}. Vamos ao resumo de "
        f"{d['rotulo'].lower()} da semana.",
    ]
    for i, it in enumerate(bons, 1):
        quem = "BIA" if i % 2 else "LEO"
        outro = "LEO" if i % 2 else "BIA"
        quando = data_falada(it.get("data"))
        abre = ("Começando: " if i == 1 else
                "Fechado. A próxima: " if i == 2 else
                "E pra terminar: ")
        linhas.append(f"{quem}: {abre}{it['titulo']}"
                      + (f", {quando}." if quando else "."))
        if it["resumo"]:
            linhas.append(f"{outro}: {it['resumo']}")

    citadas = []
    for it in bons:
        if it["fonte"] not in citadas:
            citadas.append(it["fonte"])
    linhas.append(f"BIA: Isso foi o que saiu em {_lista(citadas)}.")
    linhas.append("LEO: Semana que vem a gente volta. Até lá!")
    return "\\n".join(linhas).strip()'''
assert v4 in s, "montar"
s = s.replace(v4, n4, 1)

# a conferencia conta so o que e falado
v5 = "    n = _conta_palavras(texto)"
n5 = '''    # SEM OS NOMES NA FRENTE: "BIA:" e "LEO:" são marcação de quem fala,
    # não conteúdo, e contá-los inflaria o total em ~16 palavras.
    falado = _sem_marcacao(texto)
    n = _conta_palavras(falado)'''
assert v5 in s, "conta"
s = s.replace(v5, n5, 1)
s = s.replace('''    permitidas = {f[0].lower() for f in NICHOS[k]["fontes"]}
    permitidas |= {_dominio(f[1]) for f in NICHOS[k]["fontes"]}
    for achado in _VEICULO_RE.findall(texto):''',
'''    permitidas = {f[0].lower() for f in NICHOS[k]["fontes"]}
    permitidas |= {_dominio(f[1]) for f in NICHOS[k]["fontes"]}
    for achado in _VEICULO_RE.findall(falado):''', 1)
s = s.replace("        for valor, cru, numeracao in _valores(texto, com_texto=True):",
              "        for valor, cru, numeracao in _valores(falado, com_texto=True):", 1)
io.open(p, "w", encoding="utf-8", newline=NL).write(s)
print("podcast ok")
