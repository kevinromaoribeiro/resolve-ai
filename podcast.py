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
from datetime import date, timedelta
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
            # Trivela no lugar da Gazeta Esportiva: a Gazeta cobre
            # esporte em geral e foi por onde a Formula 1 entrou num
            # episodio de futebol. A Trivela e futebol e mais nada.
            ("Trivela", "https://trivela.com.br/",
             "https://trivela.com.br/feed/"),
        ),
        "assuntos": ("resultados da rodada", "contratações",
                     "tabela e classificação",
                     "lesões que mudam escalação"),
    },
    "games": {
        "rotulo": "Games",
        "emoji": "🎮",
        "fontes": (
            # AS TRES SAO SO DE GAMES, e essa foi a licao da primeira
            # amostra. O feed geral do IGN trouxe "Esquenta 9.9: iPhone
            # 17e" e o do Critical Hits trouxe X-Men e anime — os dois
            # cobrem cultura pop inteira, nao games. Aqui: o feed
            # /games/ do Adrenaline, o Arkade e o GameBlast.
            ("Adrenaline", "https://www.adrenaline.com.br/games/",
             "https://www.adrenaline.com.br/games/feed/"),
            ("Arkade", "https://www.arkade.com.br/",
             "https://www.arkade.com.br/feed/"),
            ("GameBlast", "https://www.gameblast.com.br/",
             "https://www.gameblast.com.br/feeds/posts/default?alt=rss"),
        ),
        "assuntos": ("lançamentos da semana",
                     "promoções que valem a pena",
                     "atualizações grandes", "o que saiu de graça"),
    },
    "ia": {
        "rotulo": "Inteligência artificial",
        "emoji": "🤖",
        "fontes": (
            # OS FEEDS AGORA SAO DA EDITORIA DE IA, nao de tecnologia
            # inteira. `canaltech.com.br/rss/` trazia celular, streaming e
            # ciencia; `/rss/inteligencia-artificial/` traz IA.
            ("Canaltech IA", "https://canaltech.com.br/inteligencia-artificial/",
             "https://canaltech.com.br/rss/inteligencia-artificial/"),
            ("Olhar Digital",
             "https://olhardigital.com.br/tag/inteligencia-artificial/",
             "https://olhardigital.com.br/tag/inteligencia-artificial/feed/"),
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
            # Mercado&Consumo SAIU: o feed dele estava com a materia mais
            # recente de 19 dias atras (conferido em 30/08). Fonte que
            # publica de tres em tres semanas nao alimenta resumo SEMANAL —
            # ela nao contribui, e ainda ocupa uma das tres vagas.
            ("Meio&Mensagem", "https://www.meioemensagem.com.br/",
             "https://www.meioemensagem.com.br/feed"),
            # O feed /varejo/feed/ do NeoFeed devolve ZERO item — conferido
            # em 30/08. Fica o geral, e o filtro de assunto separa o que e
            # varejo do que e mercado financeiro.
            ("NeoFeed", "https://neofeed.com.br/varejo/",
             "https://neofeed.com.br/feed/"),
            ("Consumidor Moderno", "https://consumidormoderno.com.br/",
             "https://consumidormoderno.com.br/feed/"),
        ),
        "assuntos": ("data de promoção chegando",
                     "mudança de frete e prazo",
                     "o que subiu e o que caiu de preço",
                     "novidade das grandes lojas"),
    },
    "economia": {
        "rotulo": "Economia e seu bolso",
        "emoji": "💰",
        "fontes": (
            ("InfoMoney", "https://www.infomoney.com.br/",
             "https://www.infomoney.com.br/feed/"),
            ("G1 Economia", "https://g1.globo.com/economia/",
             "https://g1.globo.com/rss/g1/economia/"),
            ("Exame", "https://exame.com/",
             "https://exame.com/feed/"),
        ),
        "assuntos": ("juros e inflação", "dólar e bolsa",
                     "impostos e benefícios", "preço de energia e combustível"),
    },
    "brasil": {
        "rotulo": "Notícias do Brasil",
        "emoji": "🇧🇷",
        "fontes": (
            ("G1", "https://g1.globo.com/brasil/",
             "https://g1.globo.com/rss/g1/brasil/"),
            ("Agência Brasil", "https://agenciabrasil.ebc.com.br/",
             "https://agenciabrasil.ebc.com.br/rss/geral/feed.xml"),
            ("BBC News Brasil", "https://www.bbc.com/portuguese",
             "https://feeds.bbci.co.uk/portuguese/rss.xml"),
        ),
        "assuntos": ("o que aconteceu no país", "decisões que afetam todo mundo",
                     "clima e infraestrutura"),
    },
    "saude": {
        "rotulo": "Saúde e bem-estar",
        "emoji": "🩺",
        "fontes": (
            ("G1 Bem Estar", "https://g1.globo.com/bemestar/",
             "https://g1.globo.com/rss/g1/bemestar/"),
            ("Drauzio Varella", "https://drauziovarella.uol.com.br/",
             "https://drauziovarella.uol.com.br/feed/"),
            ("Agência Fiocruz", "https://agencia.fiocruz.br/",
             "https://agencia.fiocruz.br/rss.xml"),
        ),
        "assuntos": ("estudos e descobertas", "prevenção e vacinas",
                     "alimentação e sono"),
    },
    "celebridades": {
        "rotulo": "Celebridades e TV",
        "emoji": "🌟",
        "fontes": (
            ("Quem", "https://revistaquem.globo.com/",
             "https://revistaquem.globo.com/rss/quem/"),
            ("Gshow", "https://gshow.globo.com/",
             "https://gshow.globo.com/rss/gshow/"),
            ("Hugo Gloss", "https://hugogloss.uol.com.br/",
             "https://hugogloss.uol.com.br/feed/"),
        ),
        "assuntos": ("novela e reality", "quem casou, quem terminou",
                     "bastidores da TV"),
    },
    "carros": {
        "rotulo": "Carros",
        "emoji": "🚗",
        "fontes": (
            ("Autoesporte", "https://autoesporte.globo.com/",
             "https://autoesporte.globo.com/rss/autoesporte/"),
            ("Quatro Rodas", "https://quatrorodas.abril.com.br/",
             "https://quatrorodas.abril.com.br/feed/"),
            ("AutoPapo", "https://autopapo.uol.com.br/",
             "https://autopapo.uol.com.br/feed/"),
        ),
        "assuntos": ("lançamentos e recalls", "vendas e preços",
                     "elétricos e híbridos"),
    },
    "viagens": {
        "rotulo": "Viagens",
        "emoji": "✈️",
        "fontes": (
            ("Melhores Destinos", "https://www.melhoresdestinos.com.br/",
             "https://www.melhoresdestinos.com.br/feed"),
            ("Viagem e Turismo", "https://viagemeturismo.abril.com.br/",
             "https://viagemeturismo.abril.com.br/feed/"),
            ("Passageiro de Primeira", "https://passageirodeprimeira.com/",
             "https://passageirodeprimeira.com/feed/"),
        ),
        "assuntos": ("promoção de passagem", "regras de bagagem e visto",
                     "destinos e roteiros"),
    },
    "horoscopo": {
        "rotulo": "Horóscopo",
        "emoji": "🔮",
        "fontes": (
            # LIDO, NUNCA INVENTADO. Horoscopo so entrou porque existe veiculo
            # publicando — se um dia estas tres cairem, o assunto sai do ar em
            # vez de o bot escrever previsao por conta propria.
            ("João Bidu", "https://joaobidu.com.br/",
             "https://joaobidu.com.br/feed/"),
            ("Terra Horóscopo",
             "https://www.terra.com.br/vida-e-estilo/horoscopo/",
             "https://www.terra.com.br/vida-e-estilo/horoscopo/rss.xml"),
            ("Metrópoles Horóscopo",
             "https://www.metropoles.com/colunas/horoscopo",
             "https://www.metropoles.com/colunas/horoscopo/feed"),
        ),
        "assuntos": ("previsão dos signos", "fases da lua e retrógrados",
                     "energia da semana"),
    },
    "geopolitica": {
        "rotulo": "Geopolítica",
        "emoji": "🌍",
        "fontes": (
            ("G1 Mundo", "https://g1.globo.com/mundo/",
             "https://g1.globo.com/rss/g1/mundo/"),
            ("RFI Brasil", "https://www.rfi.fr/br/",
             "https://www.rfi.fr/br/rss"),
            ("Opera Mundi", "https://operamundi.uol.com.br/",
             "https://operamundi.uol.com.br/feed/"),
        ),
        "assuntos": ("conflitos e acordos", "eleições fora do Brasil",
                     "comércio entre países"),
    },
    "ciencia": {
        "rotulo": "Ciência e espaço",
        "emoji": "🔬",
        "fontes": (
            ("Super Interessante", "https://super.abril.com.br/",
             "https://super.abril.com.br/feed/"),
            ("Galileu", "https://revistagalileu.globo.com/",
             "https://revistagalileu.globo.com/rss/galileu/"),
            ("Agência FAPESP", "https://agencia.fapesp.br/",
             "https://agencia.fapesp.br/rss/"),
        ),
        "assuntos": ("descobertas e pesquisas", "espaço e astronomia",
                     "arqueologia e história"),
    },
    "musica": {
        "rotulo": "Música",
        "emoji": "🎵",
        "fontes": (
            ("Rolling Stone Brasil", "https://rollingstone.com.br/",
             "https://rollingstone.com.br/feed/"),
            ("POPline", "https://portalpopline.com.br/",
             "https://portalpopline.com.br/feed/"),
            ("Tenho Mais Discos Que Amigos",
             "https://tenhomaisdiscosqueamigos.com/",
             "https://tenhomaisdiscosqueamigos.com/feed/"),
        ),
        "assuntos": ("lançamentos e álbuns", "shows e festivais",
                     "bastidores de artista"),
    },
    "gastronomia": {
        "rotulo": "Gastronomia",
        "emoji": "🍳",
        "fontes": (
            ("Paladar", "https://paladar.estadao.com.br/",
             "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/"
             "sections/paladar/"),
            ("Guia da Cozinha", "https://guiadacozinha.com.br/",
             "https://guiadacozinha.com.br/feed/"),
            # VejaSP e fonte AMPLA (cidade e cultura), e entra pela regra do
            # proprio dono: "falar de futebol na UOL ok, mas pegue apenas
            # noticias do tema". Quem garante a pureza aqui e o `_ASSUNTO`.
            ("VejaSP", "https://vejasp.abril.com.br/comer-beber/",
             "https://vejasp.abril.com.br/feed/"),
        ),
        "assuntos": ("restaurantes e chefs", "técnicas e ingredientes",
                     "bebidas e cafés"),
    },
}

# ---------------------------------------------------------------------------
# O QUE E DO ASSUNTO — e o que NAO e
# ---------------------------------------------------------------------------
# O Kevin ouviu a primeira amostra e achou dois furos no mesmo lugar:
#   "o audio de games ta falando de desconto em iPhone na Amazon"
#   "games e games, e nao compra de celular ou filmes"
#
# A causa: veiculo de nicho nao publica so o nicho. O IGN cobre cinema e
# celular, a Gazeta cobre Formula 1, o Canaltech cobre tudo de tecnologia.
# Filtrar pela FONTE nao basta — tem que filtrar pelo ASSUNTO.
#
# Duas camadas, e a ordem importa:
#   1. VETO DE OFERTA, em todos os nichos. Post de "menor preco" e conteudo
#      comercial, nao noticia — e o bot que promete lembrar conta virando
#      vitrine de Black Friday e a forma mais rapida de perder confianca.
#   2. MARCA DE ASSUNTO, por nicho. A materia tem que falar da coisa.
#
# Na duvida a materia CAI FORA. Episodio com duas noticias certas e melhor
# que episodio com tres, sendo uma sobre iPhone num podcast de games.

# 1. Post de oferta. "R$" sozinho nao veta (noticia de transferencia tem
#    valor); o que veta e a linguagem de vitrine.
_E_OFERTA_RE = re.compile(
    r"\b(oferta|ofertas|promo[çc][ãa]o|promo[çc][õo]es|desconto|descontos|"
    r"cupom|cupons|menor\s+pre[çc]o|mais\s+barato|pechincha|"
    r"vale\s+a\s+pena\s+comprar|onde\s+comprar|compre|comprar\s+agora|"
    # "NOTICIA E PONTO" (Kevin, 30/08/2026). Nao e so o post de oferta: e
    # qualquer formato que exista pra vender. Lista de produto, resenha com
    # link de compra e "os N melhores" sao vitrine — o veiculo ganha
    # comissao, e o bot que promete lembrar conta nao vira canal disso.
    r"review|resenha|testamos|os?\s+\d+\s+melhores|"
    r"melhores\s+\w+\s+para\s+comprar|vale\s+o\s+investimento|"
    r"custo-?benef[íi]cio|"
    # GUIA E LISTA TAMBEM NAO SAO NOTICIA. "Onde assistir ao vivo" e
    # programacao; "6 combinacoes para investir" e vitrine com outro nome.
    # Os dois vazaram pras amostras e o Kevin apontou os dois.
    r"onde\s+assistir|onde\s+ver|que\s+horas|hor[áa]rio\s+e\s+onde|"
    r"escala[çc][õo]es|prov[áa]veis|palpites?|\bodds?\b|"
    r"\d+\s+(combina[çc][õo]es|looks?|jeitos?|maneiras?|dicas?)|"
    r"para\s+investir|voc[êe]\s+precisa\s+ter|"
    # "Black Friday" e "Prime Day" SAIRAM do veto: sao nomes de evento, e
    # "Black Friday deve crescer 12%, diz pesquisa" e noticia de varejo
    # legitima. Quem veta o post de vitrine e "oferta", "desconto", "cupom" —
    # que aparecem em "Ofertas da Black Friday" e nao na noticia.
    r"frete\s+gr[áa]tis|"
    r"cai\s+de\s+pre[çc]o|por\s+apenas|a\s+partir\s+de\s+R\$)\b", re.I)

# 2. Do que cada nicho fala. Casa no titulo OU no resumo.
_ASSUNTO = {
    "futebol": (
        r"futebol|gol|gols|jogo|jogos|partida|rodada|campeonato|brasileir",
        r"libertadores|copa|s[ée]rie\s+[ab]|t[ée]cnico|treinador|escala",
        r"contrata|transferi|zagueiro|atacante|meia|goleiro|lateral",
        r"palmeiras|flamengo|corinthians|s[ãa]o\s+paulo|santos|gr[êe]mio",
        r"internacional|cruzeiro|atl[ée]tico|botafogo|vasco|fluminense",
        r"bahia|fortaleza|sele[çc][ãa]o|cbf|est[áa]dio|torcida|artilheiro",
        # FUTEBOL DE FORA TAMBEM E FUTEBOL. Sem isto, "Gerrard critica a
        # postura do Liverpool" caiu do filtro — a lista so tinha clube
        # brasileiro, e as fontes cobrem Europa o tempo todo.
        r"liverpool|real\s+madrid|barcelona|manchester|chelsea|arsenal",
        r"bayern|juventus|inter\s+de\s+mil[ãa]o|psg|premier\s+league",
        r"champions|uefa|fifa|la\s+liga|jogador|craque|elenco|treino",
        r"clube|time\b|times\b|derrota|vit[óo]ria|empate|p[êe]nalti",
    ),
    "games": (
        r"\bgame\b|\bgames\b|jogo|jogos|gameplay|console|videogame",
        r"playstation|\bps5\b|\bxbox\b|nintendo|switch|steam|\bpc\b",
        r"\bdlc\b|expans[ãa]o|patch|atualiza[çc][ãa]o\s+do\s+jogo|beta",
        r"early\s+access|\brpg\b|\bfps\b|indie|estúdio|desenvolvedora",
        r"e-?sports|campeonato\s+de|\bcs2?\b|valorant|league\s+of\s+legends",
        r"\bgta\b|fifa|\bea\b|ubisoft|rockstar|blizzard|sony|lan[çc]amento",
    ),
    "ia": (
        r"intelig[êe]ncia\s+artificial|\bia\b|\bai\b|modelo\s+de\s+linguagem",
        r"chatgpt|openai|gemini|claude|anthropic|copilot|llama|deepseek",
        # "automacao" e "algoritmo" SAIRAM: soltas, deixaram entrar uma
        # materia sobre trabalhar de Uber, que o Kevin ouviu e apontou. As
        # duas aparecem em qualquer texto sobre plataforma ou planilha.
        # Ficaram as marcas que so existem quando o assunto E IA.
        r"\bllm\b|machine\s+learning|aprendizado\s+de\s+m[áa]quina",
        r"rede\s+neural|redes\s+neurais|chatbot|deep\s+learning",
        r"generativ[ao]|modelo\s+de\s+ia|treinar\s+modelo",
        r"nvidia|hugging\s+face|midjourney|stable\s+diffusion",
    ),
    "moda": (
        r"moda|cole[çc][ãa]o|passarela|desfile|estilo|tend[êe]ncia|look",
        r"grife|estilista|fashion|semana\s+de\s+moda|alfaiataria|tecido",
        r"sapato|bolsa|vestido|jaqueta|acess[óo]rio|beleza|maquiagem",
        r"gucci|prada|chanel|dior|louis\s+vuitton|zara|farm|osklen",
    ),
    # VAREJO E COMPRA E VENDA, nao "mundo corporativo".
    #
    # A primeira versao aceitava "consumidor" e "vendas" soltos, e por ai
    # entrou "fim da escala 6x1" e "engajamento de funcionarios" — pauta de
    # RH num podcast de varejo online. As marcas agora falam de LOJA, de
    # COMPRA e de ENTREGA.
    "varejo online": (
        r"varejo|varejista|e-?commerce|com[ée]rcio\s+eletr[ôo]nico",
        r"marketplace|loja\s+online|lojas?\b|shopping|\bcompra",
        r"frete|entrega|log[íi]stica|centro\s+de\s+distribui",
        r"mercado\s+livre|amazon|magalu|magazine\s+luiza|shopee|americanas",
        r"shein|aliexpress|temu|ifood|rappi|mercado\s+pago",
        r"black\s+friday|consumidor\s+online|carrinho|checkout",
        r"vendas\s+online|vendas\s+do\s+varejo|ticket\s+m[ée]dio",
    ),
    "economia": (
        r"juros?|selic|infla[çc][ãa]o|ipca|d[óo]lar|c[âa]mbio|euro\b",
        r"bolsa|ibovespa|a[çc][õo]es|investimento|renda\s+fixa|tesouro",
        r"imposto|tributa|receita\s+federal|ir\b|declara[çc][ãa]o",
        r"sal[áa]rio|emprego|desemprego|caged|renda|benef[íi]cio|inss",
        r"banco\s+central|copom|pib|economia|mercado|empresa|lucro",
        r"gasolina|combust[íi]vel|energia|tarifa|pre[çc]o|custo\s+de\s+vida",
        r"fgts|aposentadoria|pens[ãa]o|financiamento|cr[ée]dito|d[íi]vida",
    ),
    "brasil": (
        # FONTE AMPLA, FILTRO APERTADO: G1 e Agencia Brasil publicam de tudo.
        # Sem estes termos, esporte e celebridade entrariam como "noticia".
        r"governo|congresso|senado|c[âa]mara|stf|supremo|minist[ée]rio",
        r"lei\b|projeto\s+de\s+lei|decreto|medida\s+provis[óo]ria|reforma",
        r"pol[íi]cia|opera[çc][ãa]o|justi[çc]a|julgamento|preso|investiga",
        r"chuva|enchente|seca|temporal|queimada|inmet|defesa\s+civil",
        r"educa[çc][ãa]o|enem|escola|universidade|professor|greve",
        r"transporte|rodovia|aeroporto|metr[ôo]|obra|infraestrutura",
        r"brasil|brasileiro|estado|munic[íi]pio|prefeitura|popula[çc][ãa]o",
    ),
    "saude": (
        r"sa[úu]de|doen[çc]a|sintoma|diagn[óo]stico|tratamento|rem[ée]dio",
        r"vacina|imuniza|surto|epidemia|v[íi]rus|bact[ée]ria|infec[çc][ãa]o",
        r"m[ée]dico|hospital|sus\b|anvisa|minist[ée]rio\s+da\s+sa[úu]de",
        r"estudo|pesquisa|cientistas?\s+descobr|ensaio\s+cl[íi]nico",
        r"alimenta[çc][ãa]o|dieta|nutri|exerc[íi]cio|sono|dormir|estresse",
        r"c[âa]ncer|diabetes|press[ãa]o|colesterol|obesidade|depress[ãa]o",
        r"corpo|mente|bem-?estar|preven[çc][ãa]o|exame|check-?up",
    ),
    "celebridades": (
        r"novela|reality|bbb\b|a\s+fazenda|programa|apresentador",
        r"ator|atriz|cantor|cantora|artista|famos[oa]|celebridade",
        r"namoro|casamento|separa[çc][ãa]o|divorcio|div[óo]rcio|romance",
        r"gravidez|filho|beb[êe]|fam[íi]lia|homenagem|desabafo",
        r"globo|record|sbt|netflix|estreia|elenco|bastidores|s[ée]rie",
        r"instagram|redes\s+sociais|post|declara[çc][ãa]o|entrevista",
        r"pol[êe]mica|briga|resposta|processo|indireta",
    ),
    "carros": (
        r"carro|autom[óo]vel|ve[íi]culo|suv|sedã|sed[ãa]|hatch|picape",
        r"motor|c[âa]mbio|pot[êe]ncia|cavalos|consumo|autonomia",
        r"el[ée]trico|h[íi]brido|flex|combust[íi]vel|etanol|diesel",
        r"lan[çc]amento|recall|montadora|fabricante|conce[ss]ion[áa]ria",
        r"fiat|volkswagen|chevrolet|toyota|honda|hyundai|renault|jeep",
        r"byd|tesla|ford|nissan|caoa|gwm|ram\b|peugeot|citro[ëe]n",
        r"ipva|licenciamento|multa|cnh|tr[âa]nsito|seguro\s+do\s+carro",
        r"test-?drive|avalia[çc][ãa]o|vers[ãa]o|pre[çc]o\s+do\s+carro",
    ),
    "viagens": (
        r"viagem|viajar|passagem|voo|voos|a[ée]rea|companhia\s+a[ée]rea",
        r"destino|roteiro|turismo|turista|hotel|pousada|hospedagem",
        r"aeroporto|bagagem|check-?in|conex[ãa]o|escala|milhas",
        r"visto|passaporte|imigra[çc][ãa]o|fronteira|embaixada",
        # "pa[íi]s" pegava "acordo entre paises" da geopolitica. Destino
        # se reconhece pelos outros termos, nao por essa palavra.
        r"praia|montanha|europa|caribe|nordeste|litoral|ilha\b",
        r"latam|gol\b|azul\b|smiles|tudoazul|promo[çc][ãa]o\s+de\s+passag",
        r"cruzeiro|pacote|feriado|alta\s+temporada|baixa\s+temporada",
    ),
    "horoscopo": (
        r"signo|signos|hor[óo]scopo|astrolog|zod[íi]aco|mapa\s+astral",
        # CANCER SAIU DA LISTA SOLTA (auditoria M9, P2): ele casava
        # "Câncer de mama: novo exame chega ao SUS", e essa materia lida
        # em voz de horoscopo e um estrago com o cliente. O signo
        # continua valendo quando vem com companhia de horoscopo — que e
        # como ele aparece numa materia de horoscopo de verdade.
        r"[áa]ries|touro|g[êe]meos|le[ãa]o|virgem|libra",
        r"(signo|hor[óo]scopo|previs[ãa]o|astral|regente)[^.]{0,40}c[âa]ncer|c[âa]ncer[^.]{0,40}(signo|ascendente|regente|hor[óo]scopo)",
        r"escorpi[ãa]o|sagit[áa]rio|capric[óo]rnio|aqu[áa]rio|peixes",
        r"lua\s+(cheia|nova|crescente|minguante)|eclipse|retr[óo]grado",
        r"merc[úu]rio|v[êe]nus|marte|j[úu]piter|saturno|urano|netuno",
        r"previs[ãa]o|energia\s+d[ao]|astral|ascendente|casa\s+astrol",
        r"ritual|simpatia|tarot|numerologia|cristal",
    ),
    "geopolitica": (
        r"guerra|conflito|cessar-?fogo|tr[ée]gua|invas[ãa]o|ataque",
        r"acordo|tratado|c[úu]pula|negocia[çc][ãa]o|di[áa]logo|san[çc][õo]es",
        r"onu\b|otan|nato|uni[ãa]o\s+europeia|brics|mercosul|g7\b|g20",
        r"presidente|primeiro-?ministro|chanceler|embaixador|diplomacia",
        r"elei[çc][ãa]o|elei[çc][õo]es|parlamento|governo\s+d[eoa]",
        r"tarifa|com[ée]rcio|exporta[çc][ãa]o|importa[çc][ãa]o|embargo",
        r"eua|estados\s+unidos|china|r[úu]ssia|ucr[âa]nia|israel|ir[ãa]",
        r"gaza|palestina|venezuela|argentina|frontei|refugiad",
    ),
    "ciencia": (
        r"ci[êe]ncia|cientista|pesquisa|estudo|descobert|experimento",
        r"espa[çc]o|nasa|sat[ée]lite|foguete|sonda|telesc[óo]pio|[óo]rbita",
        # `estrela\b` NAO pega "chef estrelado", e a lua saiu daqui: ela
        # aparece muito mais em horoscopo que em astronomia.
        r"planeta|marte|estrelas?\b(?!\s+michelin)|gal[áa]xia|asteroide|cometa",
        r"universo|buraco\s+negro|astronom|f[íi]sica|qu[íi]mica|biologia",
        r"f[óo]ssil|dinossauro|arqueolog|escava[çc][ãa]o|ru[íi]na|antig",
        r"evolu[çc][ãa]o|esp[ée]cie|animal|planta|clima|oceano|geolog",
        r"universidade|instituto|revista\s+cient|publicad[oa]\s+n[ao]",
    ),
    "musica": (
        r"m[úu]sica|can[çc][ãa]o|[áa]lbum|disco|single|\bep\b|faixa",
        r"cantor|cantora|banda|artista|dupla|grupo|rapper|dj\b",
        r"show|turn[êe]|festival|palco|rock\s+in\s+rio|lollapalooza",
        # "lan[çc]a" sozinho pegava "Fiat lanca nova picape". Lancamento
        # so conta quando o objeto e musical.
        r"lan[çc]a\w*\s+(?:o\s+|a\s+|seu\s+|novo\s+|nova\s+)?(?:[áa]lbum|disco|single|clipe|m[úu]sica|faixa|\bep\b)",
        r"clipe|videoclipe|feat\b|parceria\s+musical|regravou",
        r"spotify|deezer|streaming|billboard|grammy|premia[çc][ãa]o",
        r"sertanejo|funk|samba|pagode|rock|pop\b|rap\b|trap|mpb",
        r"guitarr|vocal|letra\s+d[ae]|estúdio|produtor\s+musical",
    ),
    "gastronomia": (
        # FONTE AMPLA, FILTRO APERTADO: VejaSP publica cidade e cultura.
        # Sem estes termos, show e novela entrariam como "gastronomia".
        # `bar\b` SOLTO casava "Show do Rock in Rio tem bar novo" — e a
        # VejaSP e feed amplo (ver o comentario acima). Bar continua
        # entrando; precisa e vir falando de bar.
        r"restaurante|boteco|chef|cozinha|cozinhar|culin[áa]ria",
        r"\bbar(es)?\b[^.]{0,40}(drink|coquete|chopp|cerveja|petisco|menu|card[áa]pio|vinho|abre|inaugur)|(novo|melhor|abre|inaugura)[^.]{0,20}\bbar\b",
        r"receita|prato|ingrediente|tempero|molho|massa|carne|peixe",
        r"sobremesa|doce|bolo|p[ãa]o|padaria|confeitaria|panificad",
        r"caf[ée]|vinho|cerveja|drink|coquetel|destilado|bebida",
        r"gastronom|sabor|menu|card[áa]pio|degusta[çc][ãa]o|harmoniza",
        r"michelin|premiad[oa]|abertura\s+d[eo]|inaugura|casa\s+nova",
        r"assar|refogar|fritar|grelhar|forno|panela|fog[ãa]o",
    ),
}


def e_do_assunto(nicho: str, titulo: str, resumo: str = "") -> bool:
    """A materia fala do nicho? Post de oferta nunca fala."""
    texto = "%s %s" % (titulo or "", resumo or "")
    if _E_OFERTA_RE.search(texto):
        return False
    marcas = _ASSUNTO.get(nicho or "")
    if not marcas:
        return True
    return any(re.search(m, texto, re.I) for m in marcas)


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
# O PISO, decisao do dono: "no maximo a cada 5 dias". Nao e limite tecnico —
# e o que impede o podcast de virar radio num numero que a Meta ja restringiu
# duas vezes.
MENOR_INTERVALO_DIAS = 5

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


# TETO DE TRES, decisao do dono. Nao e limite tecnico: e o que mantem o dia
# de entrega com tres audios em vez de virar radio.
MAX_ASSUNTOS = 3


def nichos_da_pessoa(bruto) -> list:
    """As chaves de assunto desta pessoa, ate `MAX_ASSUNTOS`, sem repetir.

    Aceita os DOIS formatos que existem na base: a chave unica de quem
    escolheu antes do M9.3 e a lista separada por virgula de quem escolheu
    depois. Assunto que nao existe mais no catalogo e descartado em silencio
    — a alternativa seria o episodio inteiro morrer por causa de uma chave
    velha.
    """
    if not bruto:
        return []
    if isinstance(bruto, dict):
        bruto = bruto.get("podcast_nicho") or ""
    if isinstance(bruto, (list, tuple)):
        partes = list(bruto)
    else:
        partes = str(bruto).split(",")
    saida: list = []
    for p in partes:
        k = _chave(p)
        if k and k not in saida:
            saida.append(k)
        if len(saida) >= MAX_ASSUNTOS:
            break
    return saida


def guardar_nichos(chaves) -> str:
    """Como a lista vai pro banco. Porta unica, pra ninguem gravar a mao."""
    return ",".join(nichos_da_pessoa(chaves))


def nichos_do_texto(txt) -> list:
    """Os assuntos citados num texto livre: "futebol, economia e moda".

    A landing manda os rotulos bonitos separados por virgula desde que ganhou
    selecao de tema. Passar isso por `_chave` inteiro devolve None — e nicho
    None e pessoa cadastrada sem podcast nenhum, calada.
    """
    bruto = str(txt or "")
    # O TEXTO INTEIRO PRIMEIRO. "Economia e seu bolso" e UM rotulo e tem " e "
    # dentro: quebrar por " e " antes de tentar o todo transformava o assunto
    # mais escolhido da landing em nenhum assunto, calado.
    inteiro = _chave(bruto)
    if inteiro:
        return [inteiro]
    achados: list = []
    for pedaco in re.split(r"[,;/]", bruto):
        for parte in ([pedaco] if _chave(pedaco.strip())
                      else re.split(r"\se\s", pedaco)):
            k = _chave(parte.strip())
            if k and k not in achados:
                achados.append(k)
            if len(achados) >= MAX_ASSUNTOS:
                return achados
    return achados


def rotulos_da_pessoa(bruto) -> str:
    """"futebol, economia e moda" — o que ela assinou, por extenso.

    VAZIO QUANDO NAO HA ASSUNTO, e isso importa: quem chama isto pra montar
    template usa o vazio como "nao manda". O `_lista` sozinho devolveria
    "nossas fontes" (o default dele serve pro rodape de fontes), e o lembrete
    sairia dizendo "seu resumo de nossas fontes esta pronto".
    """
    nomes = [rotulo(k).lower() for k in nichos_da_pessoa(bruto)]
    return _lista(nomes) if nomes else ""


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


# ---------------------------------------------------------------------------
# QUANDO A NOTICIA E — dito como gente diz
# ---------------------------------------------------------------------------
# Pedido do Kevin (29/08/2026): "importante dizer de quando e, com datas mais
# ou menos".
#
# Num resumo semanal, "quando" muda o sentido: "o Palmeiras venceu" e uma
# informacao; "o Palmeiras venceu no sabado" e outra — a segunda deixa a
# pessoa saber se ela ja sabia disso.
#
# DIA DA SEMANA, NAO NUMERO. Duas razoes: locucao falada diz "na quinta", nao
# "no dia 27"; e numero no roteiro passa pela conferencia de alucinacao, que
# reprovaria uma data legitima que o modelo escreveu de outro jeito.
def data_falada(iso: Optional[str], hoje=None) -> str:
    """"2026-08-28" -> "ontem" / "na quinta". "" quando nao da pra saber."""
    if not iso:
        return ""
    try:
        a, m, d = (int(x) for x in str(iso)[:10].split("-"))
        quando = date(a, m, d)
    except (ValueError, TypeError):
        return ""
    ref = hoje or tempo.hoje()
    dias = (ref - quando).days
    if dias < 0:
        return ""
    if dias == 0:
        return "hoje"
    if dias == 1:
        return "ontem"
    if dias == 2:
        return "anteontem"
    if dias <= 6:
        return ("na segunda", "na terça", "na quarta", "na quinta",
                "na sexta", "no sábado", "no domingo")[quando.weekday()]
    if dias <= 13:
        return "semana passada"
    return ""


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
    bons = escolher_variado(_validos(k, itens))
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
        # DO ASSUNTO, e nao so da fonte certa. Veiculo de nicho publica fora
        # do nicho o tempo todo — e foi assim que um desconto de iPhone
        # entrou num episodio de games.
        if not e_do_assunto(k, titulo, it.get("resumo") or ""):
            continue
        bons.append({"titulo": titulo,
                     "resumo": (it.get("resumo") or "").strip(),
                     "fonte": fonte,
                     "data": it.get("data"),
                     # `link` e a CHAVE de "ja falei disso": ele identifica a
                     # materia melhor que o titulo, que muda de manchete
                     # entre uma coleta e outra.
                     "link": it.get("link") or ""})
    return bons


# ---------------------------------------------------------------------------
# VARIEDADE: tres noticias DIFERENTES, nao a mesma vista de tres angulos
# ---------------------------------------------------------------------------
# O Kevin ouviu a primeira amostra: "o audio de futebol falou apenas de um
# jogo de 2 times e mais nada; em 7 dias tem muito mais noticia relevante —
# transferencia, polemica".
#
# Ele esta certo, e a causa era boba: o roteiro pegava os TRES PRIMEIROS itens
# do feed. Feed vem em ordem cronologica, entao os tres primeiros sao o que
# saiu nas ultimas horas — e num sabado a noite isso e o mesmo jogo em tres
# manchetes. O dedup por titulo nao pega, porque "Palmeiras vence" e
# "Flamengo perde em casa" sao titulos diferentes falando da mesma coisa.
#
# A escolha agora e por DISSEMELHANCA: entre os candidatos da semana, pega o
# proximo que menos parece com o que ja foi escolhido. E o mesmo criterio que
# um editor usa montando a primeira pagina — nao "o mais recente tres vezes",
# e sim "o que cobre mais assunto".

# Palavras que aparecem em qualquer manchete e nao dizem do que ela trata.
# Sem esta lista, "o", "de" e "com" fariam duas noticias parecerem iguais.
_VAZIAS = {
    "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "e",
    "em", "no", "na", "nos", "nas", "por", "para", "pra", "com", "sem", "que",
    "se", "ao", "aos", "the", "sobre", "apos", "após", "contra", "mais",
    "menos", "ja", "já", "nao", "não", "seu", "sua", "ele", "ela", "isso",
    "veja", "confira", "saiba", "entenda", "video", "vídeo", "fotos",
}


def _marcas(texto: str) -> set:
    """As palavras que dizem do que a materia trata."""
    t = "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower())
                if unicodedata.category(c) != "Mn")
    return {w for w in re.split(r"[^a-z0-9]+", t)
            if len(w) >= 4 and w not in _VAZIAS}


def _parecidas(a: dict, b: dict) -> float:
    """Quanto duas materias se sobrepoem, de 0 a 1."""
    ma, mb = _marcas(a.get("titulo")), _marcas(b.get("titulo"))
    if not ma or not mb:
        return 0.0
    return len(ma & mb) / float(min(len(ma), len(mb)))


# Acima disto, sao a mesma historia. Medido em manchete real de futebol:
# "Palmeiras vence o Flamengo por 2 a 1" e "Flamengo perde para o Palmeiras
# fora de casa" dividem "palmeiras" e "flamengo" — 2 de 4 marcas.
LIMITE_DE_SEMELHANCA = 0.45


def _agrupar(itens: list) -> list:
    """Junta as materias que contam a MESMA historia.

    Duas fontes cobrindo o mesmo jogo escrevem manchetes diferentes
    ("Palmeiras vence" / "Flamengo perde em casa"), entao agrupar por titulo
    exato nao serve. Agrupa por sobreposicao de palavras-chave.
    """
    grupos: list = []
    for it in itens:
        for g in grupos:
            if _parecidas(it, g[0]) >= LIMITE_DE_SEMELHANCA:
                g.append(it)
                break
        else:
            grupos.append([it])
    return grupos


def _relevancia(grupo: list) -> tuple:
    """Quanto essa historia importa. Maior e melhor.

    O SINAL E A COBERTURA CRUZADA, e ele e de graca: quando as tres fontes
    do nicho publicam sobre a mesma coisa, aquela e A historia da semana.
    Nenhuma delas gasta espaco com o que nao importa, e as tres concordando
    e o mais perto de "relevante" que da pra medir sem opinar.
    """
    fontes = len({i.get("fonte") for i in grupo})
    materias = len(grupo)
    # Empate entre historias de mesma cobertura vai pra mais recente: numa
    # semana igual, a novidade ganha.
    recente = max((str(i.get("data") or "") for i in grupo), default="")
    return (fontes, materias, recente)


def escolher_variado(itens: list, quantos: int = 0) -> list:
    """As `quantos` historias mais relevantes, uma materia de cada.

    O Kevin, depois de ouvir a primeira amostra: "tem que ser as mais
    relevantes, impactantes, polemicas — o povo quer saber disso".
    Antes daqui a escolha era "as tres mais recentes", que num sabado a
    noite sao o mesmo jogo em tres manchetes.

    Agora: agrupa por historia, ordena por quantas FONTES cobriram, pega uma
    materia de cada grupo. Sai relevante e variado pelo mesmo mecanismo — sao
    grupos diferentes por definicao.
    """
    alvo = quantos or BLOCOS
    validos = [i for i in (itens or []) if isinstance(i, dict)]
    if not validos:
        return []
    grupos = _agrupar(validos)
    escolhidas, usadas = [], set()
    while grupos and len(escolhidas) < alvo:
        # RELEVANCIA PRIMEIRO, FONTE INEDITA COMO DESEMPATE.
        #
        # So por relevancia, o site que publica mais leva as tres vagas — foi
        # o que aconteceu na primeira versao (tres do ge.globo, tres do
        # GameBlast). E so por fonte, entra materia fraca. A ordem certa e:
        # historia coberta por mais fontes ganha; entre historias empatadas,
        # ganha a que traz uma fonte que ainda nao falou.
        melhor = max(grupos, key=lambda g: (
            len({i.get("fonte") for i in g}),
            1 if {i.get("fonte") for i in g} - usadas else 0,
            len(g),
            max((str(i.get("data") or "") for i in g), default="")))
        # DENTRO DO GRUPO, A FONTE INEDITA GANHA — e so depois o resumo
        # mais completo.
        #
        # Sem isto, numa historia coberta pelas tres fontes o representante
        # era sempre a de resumo mais longo (o ge.globo), e as tres vagas
        # do episodio acabavam com o mesmo site citado tres vezes. As tres
        # fontes existem justamente pra que o episodio soe apurado.
        escolhida = max(melhor, key=lambda i: (
            0 if i.get("fonte") in usadas else 1,
            len(i.get("resumo") or "")))
        escolhidas.append(escolhida)
        usadas.add(escolhida.get("fonte"))
        grupos.remove(melhor)
    return escolhidas


def _fonte_do_grupo(grupo: list) -> str:
    return max(grupo, key=lambda i: len(i.get("resumo") or "")).get("fonte")


# ---------------------------------------------------------------------------
# QUEM CONVERSA NO EPISODIO
# ---------------------------------------------------------------------------
# Pedido do Kevin depois de ouvir a primeira amostra: "nao tem tom de podcast
# e nem 2 vozes discutindo sobre o tema; coloque sempre um homem e uma
# mulher, muito humanizado".
#
# Ele acertou a causa. Uma voz so lendo tres paragrafos e LOCUCAO, e locucao
# soa robotica por mais natural que seja o timbre. O que faz soar podcast nao
# e a voz — e a CONVERSA: um comenta, o outro reage, um pergunta, o outro
# responde. Isso muda o ROTEIRO, nao so a sintese.
#
# Nomes curtos e comuns de proposito: o TTS erra menos, e quem ouve nao
# tropeca num nome estranho logo na primeira frase.
APRESENTADORES = {"mulher": "Bia", "homem": "Léo"}

_FALA_RE = re.compile(r"^\s*(BIA|L[ÉE]O)\s*:\s*(.+)$", re.I)


def falas(roteiro):
    """Roteiro -> [("mulher"|"homem", texto)]. [] quando nao e dialogo.

    Lista vazia NAO e erro: e como o `voz` sabe que aquele texto e pra uma
    voz so (qualquer outro uso que nao o episodio).
    """
    saida = []
    for linha in (roteiro or "").splitlines():
        m = _FALA_RE.match(linha)
        if not m:
            continue
        texto = m.group(2).strip()
        if not texto:
            continue
        saida.append(("mulher" if m.group(1).upper() == "BIA" else "homem",
                      texto))
    return saida


def _sem_marcacao(roteiro):
    """O texto falado, sem os nomes na frente.

    A conferencia de alucinacao e o teto de palavras contam CONTEUDO;
    contar "BIA:" dezesseis vezes inflaria o total e reprovaria roteiro bom.
    """
    ditas = falas(roteiro)
    if ditas:
        return " ".join(t for _q, t in ditas)
    return roteiro or ""


def _montar(d: dict, bons: list, nome: str) -> str:
    """Monta a CONVERSA. Separado do corte pra que remontar não recurse.

    Este é o roteiro DE RESERVA: sai quando o LLM falha ou quando o que ele
    escreveu é reprovado na conferência. Ele é mais seco que uma conversa de
    verdade, e é assim de propósito — só repete o que veio do feed, sem
    acrescentar nada.

    Mesmo assim ele fecha um assunto antes de abrir o próximo ("Fechado. A
    próxima:"), que foi o pedido do Kevin: dar a notícia, encerrar, anunciar
    a seguinte e só então falar dela.
    """
    bia, leo = APRESENTADORES["mulher"], APRESENTADORES["homem"]
    primeiro = (nome or "").split()[0] if nome else ""
    saudacao = f"Oi, {primeiro}!" if primeiro else "Oi!"
    linhas = [
        f"BIA: {saudacao} Aqui é a {bia}.",
        f"LEO: E eu sou o {leo}. Bora ao resumo de "
        f"{d['rotulo'].lower()} da semana.",
    ]
    for i, it in enumerate(bons, 1):
        quem = "BIA" if i % 2 else "LEO"
        outro = "LEO" if i % 2 else "BIA"
        quando = data_falada(it.get("data"))
        abre = ("Começando: " if i == 1
                else "Fechado. A próxima: " if i == 2
                else "E pra terminar: ")
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
    return "\n".join(linhas).strip()


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
    # QUANDO UM NOME JA TEM "E" DENTRO, O "E" DA JUNCAO CONFUNDE (M9.11).
    # "Economia e seu bolso" e um rotulo so, e a lista saía "futebol,
    # economia e seu bolso e moda" — três assuntos que parecem quatro. Nesse
    # caso a vírgula sozinha separa melhor que a conjunção.
    if any(" e " in n for n in nomes):
        return ", ".join(nomes)
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


# ---------------------------------------------------------------------------
# AS PERGUNTAS
# ---------------------------------------------------------------------------
def convite(nicho: Optional[str], nome: str = "") -> Optional[dict]:
    """"Seu podcast está pronto, quer ouvir?" — com botão. None sem nicho."""
    ks = nichos_da_pessoa(nicho)
    if not ks:
        return None
    k = ks[0]
    d = NICHOS[k]
    primeiro = (nome or "").split()[0] if nome else ""
    return {
        "texto": (f"{d['emoji']} {('Bom dia, ' + primeiro + '! ') if primeiro else ''}"
                  # SEM MINUTAGEM NO CONVITE (auditoria M4.3). O audio
                  # sai entre 40s e 3 min conforme a semana e conforme
                  # o roteiro venha da locucao ou do fallback. Prometer
                  # "3 minutos" na primeira frase e errar pra menos
                  # justo onde a pessoa decide se toca ou nao.
                  # SEM "DA SEMANA" (auditoria M9 2a passada, P1-B). A
                  # regularidade virou escolha da pessoa — 5, 7, 15 ou 30
                  # dias — e "da semana" mente pra quem pediu quinzenal ou
                  # mensal. "Novo" vale nos quatro casos.
                  + (f"Seu novo mini podcast de "
                     f"*{d['rotulo'].lower()}* está pronto.\n\n"
                     if len(ks) == 1 else
                     # O PLURAL E O NUMERO IMPORTAM: quem assinou tres
                     # recebe tres notas de voz seguidas, e o convite e o
                     # unico lugar onde da pra avisar disso antes.
                     f"Seus *{len(ks)}* novos mini podcasts estão "
                     f"prontos — "
                     f"{_lista([NICHOS[x]['rotulo'].lower() for x in ks])}."
                     f"\n\n")
                  + f"Quer ouvir agora?"),
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


def data_legivel(valor) -> bool:
    """A data de ultimo envio da pra ler? Usado pelo caminho MANUAL.

    `pode_enviar` trata data podre como "acabou de enviar", e esta certo no
    caminho proativo: la o erro seguro e mandar de menos. No caminho manual e
    o contrario — a pessoa PEDIU, e um valor corrompido no banco tirava a
    unica saida que ela tinha, pra sempre e em silencio.
    """
    if not valor:
        return True
    from datetime import date as _d, datetime as _dt
    if isinstance(valor, (_d, _dt)):
        return True
    texto = str(valor).strip().replace("T", " ")
    texto = re.sub(r"(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$", "", texto)[:19]
    for forma in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            _dt.strptime(texto, forma)
            return True
        except ValueError:
            continue
    return False


def pode_enviar(ultimo_envio_iso: Optional[str],
                agora=None, dias: Optional[int] = None) -> bool:
    """Passou o intervalo que ESTA pessoa escolheu desde o último episódio?

    TETO DURO, não sugestão: o Kevin definiu no máximo 1x por semana, quatro
    por mês. Áudio é a mensagem mais intrusiva do WhatsApp, e este número já
    foi restringido duas vezes.

    `dias` é a regularidade dela (5, 7, 15 ou 30 — M9.12). Sem ela, cai no
    padrão semanal. Antes isto era 7 fixo nos dois caminhos, e quem pedia "a
    cada 5 dias" continuava recebendo de 7 em 7: a escolha aparecia na
    confirmação e não acontecia no produto.

    Valor estranho cai no padrão em vez de virar intervalo zero — episódio no
    ritmo errado é recuperável, enxurrada de áudio não.

    Sem registro de envio, pode: é o primeiro episódio.
    """
    try:
        _gap = int(dias or DIAS_ENTRE_EPISODIOS)
    except Exception:
        _gap = DIAS_ENTRE_EPISODIOS
    if _gap < MENOR_INTERVALO_DIAS:
        _gap = DIAS_ENTRE_EPISODIOS
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
    return (ref - ultimo) >= timedelta(days=_gap)


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

_PROMPT_LOCUCAO = """Você escreve o roteiro de um mini-podcast em português \
do Brasil sobre {rotulo}. São DUAS pessoas conversando: {bia} (mulher) e \
{leo} (homem).

MATÉRIA-PRIMA (é tudo o que vocês sabem; não existe mais nada):
{materia}

FORMATO — uma fala por linha, começando com o nome e dois pontos:
BIA: ...
LEO: ...

REGRAS, nesta ordem de importância:

1. NÃO INVENTE. Nenhum placar, número, nome, data ou consequência que não \
esteja na matéria-prima. Item vago continua vago.

2. NÃO CITE fonte nenhuma além destas: {fontes}.

3. É CONVERSA, NÃO REVEZAMENTO DE LEITURA. Um dá a notícia, o outro REAGE de \
verdade — comenta, discorda, pergunta, completa, se surpreende. Se ninguém \
reagir, é locução com dois nomes na frente, e aí não valeu a pena.

4. UM ASSUNTO POR VEZ, FECHADO ANTES DO PRÓXIMO. Dá a notícia, comenta, \
ENCERRA ("é isso", "fica a conta", "vamos ver no que dá") e só então anuncia \
o próximo ("a próxima é sobre...", "mudando de assunto..."). Nunca misture \
dois assuntos na mesma fala.

5. DIGA QUANDO FOI. A matéria-prima traz o dia entre parênteses — diga em \
palavras: "no sábado", "ontem", "semana passada". Nunca invente data.

6. {blocos} assuntos, na ordem dada. Entre {minimo} e {alvo} palavras no \
total, contando só o que é falado.

7. PORTUGUÊS FALADO DE VERDADE. Frase curta. Contração ("tá", "pra", "né"). \
Pode começar frase com "e", "mas", "olha". Zero "outrossim", "vale ressaltar" \
e "é importante destacar". Ninguém fala assim, e é isso que faz soar robô.

8. {bia} abre cumprimentando {nome} e se apresentando; {leo} fecha dizendo \
de quais fontes veio e que semana que vem tem mais.

Devolva SOMENTE as linhas de fala, sem título, sem marcação, sem aspas."""


def _prompt_de_locucao(nicho: str, itens: list, nome: str = "") -> str:
    d = NICHOS[nicho]
    linhas = []
    for i, it in enumerate(itens, 1):
        quando = data_falada(it.get("data"))
        linhas.append("%d. [%s]%s %s" % (
            i, it["fonte"], (" (%s)" % quando) if quando else "",
            it["titulo"]))
        if it.get("resumo"):
            linhas.append("   %s" % it["resumo"][:400])
    return _PROMPT_LOCUCAO.format(
        bia=APRESENTADORES["mulher"],
        leo=APRESENTADORES["homem"],
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
                     materia: Optional[str] = None,
                     hoje=None) -> Optional[str]:
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
    # SEM OS NOMES NA FRENTE: "BIA:" e "LEO:" sao marcacao de quem fala,
    # nao conteudo, e conta-los inflaria o total em ~16 palavras.
    falado = _sem_marcacao(texto)
    n = _conta_palavras(falado)
    if n > PALAVRAS_TETO:
        return "passou de %d palavras (%d)" % (PALAVRAS_TETO, n)
    if n < 60:
        return "curto demais (%d palavras)" % n

    permitidas = {f[0].lower() for f in NICHOS[k]["fontes"]}
    permitidas |= {_dominio(f[1]) for f in NICHOS[k]["fontes"]}
    for achado in _VEICULO_RE.findall(falado):
        alvo = achado.lower().strip()
        if not any(alvo in p for p in permitidas):
            return "citou fonte de fora da lista: %r" % achado

    # NUMERO QUE NAO ESTAVA NA MATERIA-PRIMA (auditoria M4.2, P1-5;
    # recalibrado na M4.3).
    #
    # O teste de fonte pega o modelo citando a Folha; nao pega ele afirmando
    # "venceu por 7 a 0" sem citar ninguem — que e a alucinacao que importa,
    # porque a pessoa nao tem como desconfiar.
    #
    # A PRIMEIRA VERSAO ERA CEGA E REPROVAVA O LEGITIMO: exigia o numero
    # IDENTICO na fonte, e derrubou 5 de 12 reescritas normais — "3
    # noticias", "R$ 1,2 milhao" (fonte: 1.200.000), "mais de 60%" (fonte:
    # 62%), "temporada 2026". Pior: reprovava o PROPRIO roteiro
    # deterministico desta casa, que numera os blocos "1." "2." "3.".
    # Conferencia que reprova tudo nao protege nada — ela so desliga a
    # locucao, e sobra o roteiro cru de quarenta segundos.
    #
    # Agora ela permite o que a fala legitimamente faz e barra o que e
    # invencao:
    #   - o numero que esta na fonte, com escala ("1,2 milhao" == 1.200.000)
    #   - ARREDONDAMENTO de ate 10%: locucao arredonda, e o cabecalho deste
    #     modulo pede isso
    #   - 1 ate BLOCOS: a numeracao que o proprio formato cria
    #   - o ano corrente e o proximo
    #
    # O QUE ELA NAO COBRE, e vale dizer em voz alta: numero REUSADO em
    # outra relacao. Se a fonte diz "2 a 1" e "3 semanas", o roteiro pode
    # dizer "3 a 2" que passa — os dois numeros existem na materia, so
    # nao naquela combinacao. Verificar relacao exigiria entender a
    # frase, que e o problema que a checagem existe pra nao depender.
    #
    # Entao: esta camada pega numero que NAO EXISTE na fonte (valor de
    # transferencia, publico, ano fora da janela, goleada). Nao pega
    # placar plausivel remontado. Quem cobre esse resto e o proprio
    # prompt e o fato de o roteiro citar as fontes — a pessoa pode
    # conferir.
    if materia is not None:
        fonte = _valores(materia)
        ano = (hoje or tempo.hoje()).year
        for valor, cru, numeracao in _valores(falado, com_texto=True):
            if not _autorizado(valor, fonte, ano, numeracao):
                return "numero %r nao veio das fontes" % cru.rstrip(".,")
    return None


# Escala falada: "1,2 milhao" e "1.200.000" sao o mesmo numero, e a locucao
# escolhe a primeira forma.
_ESCALAS = (
    (r"bilh[õo]es|bilh[ãa]o", 1_000_000_000),
    (r"milh[õo]es|milh[ãa]o", 1_000_000),
    (r"mil\b", 1_000),
)

_NUM_BRUTO_RE = re.compile(r"\d[\d.,]*")


def _um_valor(cru: str):
    """"1.200.000,50" -> 1200000.5 ; "1,2" -> 1.2 ; "05" -> 5."""
    t = (cru or "").rstrip(".,")
    if not t:
        return None
    try:
        if "," in t:                       # virgula e o decimal no Brasil
            return float(t.replace(".", "").replace(",", "."))
        partes = t.split(".")
        if len(partes) > 1 and all(len(x) == 3 for x in partes[1:]):
            return float("".join(partes))  # 1.200.000 = separador de milhar
        return float(partes[0]) if len(partes) > 1 else float(t)
    except ValueError:
        return None


def _valores(texto: str, com_texto: bool = False) -> list:
    """Todos os numeros do texto, com a escala falada ja aplicada."""
    saida = []
    for m in _NUM_BRUTO_RE.finditer(texto or ""):
        valor = _um_valor(m.group(0))
        if valor is None:
            continue
        depois = (texto[m.end():m.end() + 24] or "").lower()
        for padrao, mult in _ESCALAS:
            if re.match(r"\s*(?:de\s+)?(?:%s)" % padrao, depois):
                valor *= mult
                break
        if com_texto:
            # "numeracao" = o numero ABRE a linha e vem seguido de ponto.
            antes = texto[:m.start()]
            inicio_de_linha = (not antes.strip()
                               or antes.rstrip(" ").endswith(chr(10)))
            saida.append((valor, m.group(0),
                          bool(inicio_de_linha
                               and texto[m.end():m.end() + 1] == ".")))
        else:
            saida.append(valor)
    return saida


def _autorizado(valor: float, fonte: list, ano: int,
                numeracao: bool = False) -> bool:
    # A NUMERACAO DE BLOCO SO VALE QUANDO E NUMERACAO (auditoria M4.5, P1-4).
    #
    # Liberar todo inteiro de 0 a BLOCOS deixava passar praticamente TODO
    # placar de futebol que existe: "3 a 2", "2 a 0", "1 a 0". O caso do
    # auditor ("7 a 0") era pego e a versao realista dele nao.
    #
    # Agora so entra por aqui o numero que abre linha seguido de ponto —
    # "1." "2." "3." —, que e a forma que o formato cria e o modelo copia.
    if numeracao and 1 <= valor <= BLOCOS and float(valor).is_integer():
        return True
    if valor in (ano, ano + 1):
        return True
    for f in fonte:
        if f == valor:
            return True
        maior = max(abs(f), abs(valor))
        if maior and abs(f - valor) <= 0.1 * maior:
            return True                    # arredondamento de locucao
    return False

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

    usados = escolher_variado(_validos(k, itens))
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
    return " ".join(
        "%s %s %s" % (i.get("titulo") or "", i.get("resumo") or "",
                      i.get("data") or "")
        for i in (itens or []))


def _chamar_llm(prompt: str) -> str:
    from litellm import completion
    import ai_engine
    resp = completion(model=ai_engine.LLM_MODEL, max_tokens=900,
                      messages=[{"role": "user", "content": prompt}])
    return resp.choices[0].message.content or ""
