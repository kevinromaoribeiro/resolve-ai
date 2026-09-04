# -*- coding: utf-8 -*-
"""Os conselheiros do painel: crescimento e preco.

Duas regras que valem mais que o prompt:

1. SO OS NUMEROS DO PAINEL. O conselheiro recebe o retrato real do negocio e
   e proibido de inventar numero. Conselho de negocio com dado imaginado e
   pior que nenhum conselho — ele parece fundamentado.

2. SO POR BOTAO, e a resposta fica guardada com a data. Analise que se
   regenera sozinha a cada 20 segundos queimaria dinheiro em silencio e
   ainda mudaria de opiniao a cada leitura.

O que o conselheiro NAO faz: nao manda mensagem, nao mexe em cliente, nao
muda configuracao. Ele escreve um texto que o dono le e decide.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("resolveai")

CONSELHOS = ("crescimento", "preco", "marketing", "cx", "produto")

# UMA ANALISE POR CONSELHEIRO POR SEMANA, e o limite e do servidor.
#
# Decisao do dono. Duas razoes, e a segunda importa mais que o dinheiro:
#
# 1. O modelo bom custa centavos por analise em vez de fracoes. Cinco
#    conselheiros por semana e gasto previsivel; cinco por dia, nao.
# 2. O retrato do negocio nao muda de um dia pro outro. Reanalisar segunda
#    e terca devolveria o mesmo conselho com palavras diferentes — e ler o
#    mesmo conselho reescrito da a impressao de novidade sem ser novidade.
#
# Fica no servidor, e nao na tela, pela mesma razao da trava do lote: quem
# clica de novo e justamente quem nao viu o aviso.
LIMITE_DIAS = int(os.environ.get("CONSELHO_LIMITE_DIAS", "7"))

# Mantido por compatibilidade com quem chamava antes do limite semanal.
VALIDADE_H = LIMITE_DIAS * 24

# O CONSELHEIRO MERECE UM MODELO MELHOR QUE O DO BOT.
#
# O bot roda `gpt-4o-mini`, que e bom pra resumir e fraco pra raciocinar
# sobre numero e pra ser nao-obvio — e foi exatamente onde a primeira
# rodada falhou: recomendou preco sem fechar a conta e chamou de
# "surpreendente" uma ideia comum.
#
# Com uma analise por semana, o modelo caro cabe. Se ele falhar, cai no
# modelo do bot em vez de deixar o botao sem resposta.
MODELO = os.environ.get("LLM_MODEL_CONSELHO", "gpt-4o")

# VERSAO DO CONSELHEIRO. Sobe sempre que um prompt ou o retrato mudar.
#
# O limite semanal existe pra nao repetir a MESMA pergunta ao MESMO
# conselheiro. Quando eu mudo o prompt ou corrijo o retrato, nao e mais a
# mesma pergunta — e sem isto o dono ficava preso sete dias na resposta de
# um conselheiro que ja nao existe.
#
# Aconteceu de verdade: o conselheiro de preco recomendou baixar o preco
# porque o retrato so tinha o custo variavel. Consertei o retrato, e a
# trava segurou a recomendacao errada na tela por uma semana.
#
# Nao e brecha no limite: e a diferenca entre "voce ja perguntou isso" e
# "eu mudei a pergunta".
VERSAO = 3


def falta_para_liberar(quando: str, agora, versao=None) -> float:
    """Quantos dias faltam pra proxima analise. Zero quando ja liberou.

    Analise feita por uma versao antiga do conselheiro nao segura nada:
    ela responde a uma pergunta que nao existe mais.
    """
    if versao is not None and versao != VERSAO:
        return 0.0
    if not quando:
        return 0.0
    try:
        import datetime as _dt
        passou = (agora - _dt.datetime.fromisoformat(quando)).total_seconds()
    except Exception:
        # Carimbo ilegivel nao pode prender o dono pra sempre.
        return 0.0
    return max(0.0, LIMITE_DIAS - passou / 86400.0)

_PAPEL = {
    "crescimento": (
        "Você é um conselheiro de crescimento sênior, especialista em SaaS "
        "de consumo no Brasil, falando com um fundador solo. Ele é técnico, "
        "constrói rápido e valida devagar — esse é o padrão dele, e apontar "
        "isso quando for o caso é parte do seu trabalho."),
    "preco": (
        "Você é um consultor de precificação sênior. Sua especialidade é "
        "assinatura de baixo ticket para consumidor brasileiro. Você olha "
        "custo real, disposição a pagar e o que o produto substitui na vida "
        "da pessoa — não o que ele custou para construir."),
    "marketing": (
        "Você é um profissional de marketing sênior que já lançou produto "
        "de assinatura barata no Brasil sem verba de mídia. Você trabalha "
        "com o que existe: a base atual, o boca a boca e o próprio "
        "WhatsApp. Você não propõe campanha paga para quem não tem caixa."),
    "cx": (
        "Você é especialista em experiência do cliente, com foco em "
        "produtos que vivem dentro de aplicativo de mensagem para público "
        "brasileiro de classe B e C. Você entende que a pessoa não lê "
        "manual, tem vergonha de errar e abandona no primeiro atrito."),
    "produto": (
        "Você é um head de produto sênior. Seu trabalho aqui é o oposto do "
        "usual: este fundador constrói demais e valida de menos, então você "
        "só sugere o que puder ser feito rápido E que resolva um problema "
        "que os números do painel provam existir."),
}

_TAREFA = {
    "crescimento": (
        "Responda em no máximo 350 palavras, em português do Brasil, "
        "direto, sem introdução e sem elogio.\n\n"
        "1. O GARGALO. Qual é o único gargalo mais importante agora? "
        "Aponte o número do painel que prova isso.\n"
        "2. AS TRÊS AÇÕES desta semana, em ordem. Cada uma tem que ser "
        "executável por uma pessoa só, em um dia, sem construir feature "
        "nova. Diga o que fazer, não o princípio.\n"
        "3. O QUE PARAR de fazer. Uma coisa que ele está fazendo e que não "
        "está ajudando.\n"
        "4. O NÚMERO a olhar na semana que vem, e o valor que significa que "
        "funcionou."),
    "preco": (
        "Responda em no máximo 400 palavras, em português do Brasil, "
        "direto, sem introdução.\n\n"
        "ANTES DE COMEÇAR, faça esta conta e escreva o resultado: para "
        "cada preço que você considerar, a SOBRA por cliente é o preço "
        "menos o CUSTO CHEIO (não o variável). Depois divida a meta de "
        "lucro mais próxima por essa sobra: é o número de clientes que "
        "aquele preço exige. Baixar o preço aumenta esse número.\n\n"
        "1. O PREÇO ATUAL está certo, alto ou baixo? Justifique com o "
        "CUSTO CHEIO e com o que o produto substitui na vida da pessoa. "
        "Não use o número de pagantes como argumento: ninguém foi "
        "convidado a pagar ainda.\n"
        "2. QUANTO COBRAR, com um número, e quantos clientes esse preço "
        "exige para a meta mais próxima. Se for para manter, diga manter.\n"
        "3. O RISCO da sua recomendação — o que pode dar errado se ele "
        "seguir.\n"
        "4. COMO DESCOBRIR se está certo, com um teste que caiba em uma "
        "semana e na base atual dele. Não sugira pesquisa de mercado."),
    "marketing": (
        "Responda em no máximo 350 palavras, em português do Brasil, "
        "direto, sem introdução.\n\n"
        "1. QUEM É O CLIENTE deste produto, na prática, olhando o que a "
        "base atual faz com ele. Não invente persona.\n"
        "2. A FRASE de uma linha que faz essa pessoa entender para que "
        "serve, sem jargão.\n"
        "3. TRÊS AÇÕES de aquisição sem verba, executáveis nesta semana por "
        "uma pessoa só. Diga onde e o que fazer.\n"
        "4. O QUE NÃO FAZER agora, e por quê."),
    "cx": (
        "Responda em no máximo 350 palavras, em português do Brasil, "
        "direto, sem introdução.\n\n"
        "1. O ATRITO. Olhando os números de ativação e uso, onde a pessoa "
        "provavelmente trava? Aponte o número que sustenta sua hipótese.\n"
        "2. O PRIMEIRO DIA ideal, minuto a minuto, com os textos exatos que "
        "o bot manda. No máximo 4 passos.\n"
        "3. A MENSAGEM que traz de volta quem sumiu — escreva o texto.\n"
        "4. O QUE PODE MAGOAR ou constranger a pessoa neste produto, e como "
        "evitar."),
    "produto": (
        "Responda em no máximo 650 palavras, em português do Brasil, "
        "direto, sem introdução.\n\n"
        "1. O QUE JÁ EXISTE E NINGUÉM USA. Pelos números, qual capacidade "
        "está subaproveitada? Como fazer ela ser descoberta sem construir "
        "nada novo. Comece por aqui: é o mais barato.\n"
        "2. TRÊS FEATURES NOVAS que aumentariam o uso DIÁRIO. Para cada "
        "uma, nesta ordem: o que é, em uma frase; o gatilho que faz a "
        "pessoa usar sem lembrar de usar; o número do painel que prova que "
        "o problema existe; e o esforço em dias de trabalho de uma pessoa "
        "só. Priorize ideia que aproveite o que já está construído em vez "
        "de abrir frente nova.\n"
        "3. DUAS IDEIAS DE NEGÓCIO para chegar nas metas de lucro que estão "
        "no retrato. Podem ser novo público, nova forma de cobrar, plano "
        "diferente, parceria ou canal — não precisa ser feature. Diga, para "
        "cada uma, quantos clientes ela pode trazer e por quê. Se achar que "
        "o caminho é só vender mais do mesmo, diga isso e defenda.\n"
        "4. A JOGADA QUE ELE NÃO ENXERGOU. Esta é a parte mais importante "
        "da sua resposta, e é onde você tem que ser não-óbvio.\n"
        "   Procure uma COMBINAÇÃO de capacidades que já estão no ar e que, "
        "juntas, viram algo que nenhuma delas é sozinha. O fundador olha o "
        "produto todo dia: qualquer coisa que ele já teria pensado sozinho "
        "não serve aqui.\n"
        "   Descreva a ideia em uma frase, diga quais capacidades da lista "
        "ela usa, o momento exato do dia em que a pessoa a usaria, e por "
        "que é surpreendente. Ela tem que ser CONSTRUÍVEL em poucos dias "
        "usando o que já existe — ideia impossível não é jogada, é sonho.\n"
        "   Se você não encontrar nada realmente não-óbvio, diga isso com "
        "todas as letras em vez de inventar uma ideia comum e chamá-la de "
        "surpreendente.\n"
        "5. A IDEIA QUE PARECE BOA E NÃO É. Uma coisa tentadora que você "
        "recomenda NÃO fazer agora, com o motivo.\n"
        "6. O CORTE. Algo que já existe e deveria ser removido ou "
        "desligado, porque custa atenção e não devolve nada."),
}

_REGRAS = (
    "REGRAS INEGOCIÁVEIS:\n"
    "- Use SOMENTE os números do retrato abaixo. Não invente nenhum número, "
    "nem de mercado, nem de benchmark, nem estimativa sua.\n"
    "- Se um número que você precisaria não está no retrato, diga que falta "
    "e siga sem ele.\n"
    "- Nada de jargão de startup e nada de lista de boas práticas genéricas. "
    "Fale deste negócio, com estes números.\n"
    "- Antes de escrever CADA sugestão, procure-a na lista de capacidades "
    "que já estão no ar. Se ela estiver lá, você está mandando construir o "
    "que já está construído — o pior conselho possível para quem constrói "
    "demais. Isso já aconteceu: um conselheiro mandou 'fazer um mini "
    "podcast' com o podcast pronto e listado.\n"
    "- Se a sua ideia já está na lista, ela vira COMO FAZER DESCOBRIREM, e "
    "não como construir. Diga a frase 'já existe' quando for o caso.\n"
    "- Toda ideia sua tem que caber numa pessoa só trabalhando. Não existe "
    "time, não existe verba de mídia e não existe caixa.\n"
    "- Amarre cada sugestão a uma das metas de lucro do retrato: diga o que "
    "ela move em direção a elas. Ideia que não aproxima da meta não entra.\n"
    "- TESTE DE SANIDADE, obrigatório: se você calcular quantos clientes "
    "uma ideia traz e o resultado for menos de 5% da meta mais próxima, "
    "ela NÃO é uma ideia para chegar na meta. Descarte e proponha outra, "
    "ou diga que não encontrou nenhuma dessa magnitude. Uma ideia que "
    "rende 1 ou 2 clientes contra uma meta de 100 não é ideia, é ruído.\n"
    "- NINGUÉM FOI CONVIDADO A PAGAR ainda. Então zero pagantes não é "
    "evidência de que o preço está errado, nem de que o produto é ruim: é "
    "ausência de pedido. Não use o número de pagantes para concluir nada "
    "sobre preço ou sobre valor percebido.\n"
    "- Não use o nome de nenhum cliente."
)


def _linha(rot: str, valor) -> str:
    return "- %s: %s\n" % (rot, valor)


def retrato(dados: dict) -> str:
    """O estado do negocio em texto, so com o que o painel realmente mede."""
    val = dados.get("validacao") or {}
    eng = dados.get("engajamento") or {}
    fin = dados.get("financeiro") or {}
    cus = dados.get("custo_usuario") or {}
    met = dados.get("metas") or {}
    tpl = dados.get("templates") or {}
    env = dados.get("envio") or {}
    pod = dados.get("podcast") or {}

    t = "RETRATO DO NEGÓCIO (dados reais do painel, hoje)\n\n"
    t += "PRODUTO: assistente pessoal dentro do WhatsApp. Assinatura de "
    t += "R$ %.2f por mês. Fundador solo.\n\n" % (
        (fin.get("margem") or {}).get("preco") or 0)

    t += "FUNIL:\n"
    t += _linha("pessoas na base", val.get("base"))
    t += _linha("ativados (3+ itens cadastrados)", val.get("ativados"))
    t += _linha("o bot já salvou (deu baixa após lembrete)", val.get("salvos"))
    t += _linha("retidos", val.get("retidos"))
    t += _linha("PAGANTES", val.get("pagantes"))
    t += ("- observação: todas as pessoas da base são testers convidados "
          "pessoalmente pelo fundador, e NENHUMA foi convidada a pagar até "
          "hoje.\n\n")

    t += "USO:\n"
    t += _linha("mensagens por pessoa por dia", eng.get("por_pessoa_dia"))
    t += _linha("pessoas que falaram nos últimos 7 dias", eng.get("pessoas"))
    t += _linha("mensagens do próprio dono em 7 dias",
                eng.get("mensagens_do_dono_7d"))
    t += _linha("veredito de hábito", eng.get("veredito"))
    t += _linha("episódios de podcast enviados na semana",
                (pod or {}).get("na_semana"))
    t += "\n"

    t += "DINHEIRO (mês):\n"
    t += _linha("receita bruta", fin.get("bruto"))
    t += _linha("custo total", fin.get("custo_total"))
    t += _linha("líquido no bolso", fin.get("liquido"))
    t += _linha("custos fixos", (fin.get("custos") or {}).get("fixos"))
    t += _linha("assinantes pagantes", fin.get("assinantes"))
    t += "\n"

    # O CUSTO CHEIO VEM PRIMEIRO, E EXPLICADO.
    #
    # Na primeira versao o retrato so trazia o custo VARIAVEL. O
    # conselheiro de preco leu "R$ 0,07 por cliente", concluiu que o
    # produto e quase de graca e recomendou baixar o preco pela metade —
    # o que na verdade afastava da meta. Ele nao errou: ele nunca recebeu
    # o outro numero. Numero solto no briefing vira conselho errado.
    t += "CUSTO POR PESSOA (últimos 30 dias, medido). LEIA OS DOIS:\n"
    t += _linha("CUSTO CHEIO médio (variável + fatia do fixo) — use ESTE "
                "para decidir preço", cus.get("cheio_medio"))
    t += _linha("SOBRA por cliente hoje (preço − custo cheio)",
                cus.get("sobra_por_cliente"))
    t += _linha("custo fixo do mês", cus.get("fixo_mes"))
    t += _linha("fatia do fixo por pessoa (fixo ÷ %s pessoas)"
                % cus.get("pessoas"), cus.get("fixo_rateado"))
    t += _linha("custo VARIÁVEL médio (só o uso: LLM, áudio, foto, podcast)",
                cus.get("medio"))
    t += _linha("variável do mais caro da base", cus.get("maior"))
    t += ("- O variável é centavos e o cheio é reais: quem decidir preço "
          "pelo variável conclui que o produto é de graça e erra. A fatia "
          "do fixo cai sozinha conforme entra gente.\n")
    if not cus.get("conferido"):
        t += ("- ATENÇÃO: os preços unitários são estimativa de tabela "
              "pública, não de fatura. Trate como ordem de grandeza.\n")
    t += "\n"

    t += "METAS DE LUCRO LÍQUIDO DO DONO:\n"
    for a in (met.get("alvos") or []):
        t += "- %s: R$ %.0f/mês = %s clientes pagantes\n" % (
            a.get("rotulo"), a.get("meta") or 0,
            a.get("clientes") if a.get("clientes") is not None else "?")
    t += "\n"

    # O QUE O PRODUTO SABE FAZER, item por item.
    #
    # Sem isto o conselheiro opina no escuro: ele ve os numeros e nao sabe
    # que a leitura de boleto por foto ja existe, que o audio ja e
    # transcrito, que o podcast ja esta pronto. E entao sugere construir o
    # que ja esta construido — o pior conselho possivel pra quem constroi
    # demais.
    #
    # Nao e o codigo-fonte, e o inventario que o repo mantem derivado dele,
    # o mesmo que o painel mostra. Codigo cru num prompt afogaria o
    # essencial e ainda ficaria desatualizado no primeiro commit.
    poderes = dados.get("poderes") or []
    if poderes:
        t += "O QUE O PRODUTO JÁ SABE FAZER (%d capacidades no ar):\n" % len(
            poderes)
        grupo_atual = ""
        for cap in poderes:
            g = cap.get("grupo") or ""
            if g != grupo_atual:
                t += "  [%s]\n" % g
                grupo_atual = g
            t += "  - %s: %s\n" % (cap.get("titulo") or "",
                                   (cap.get("desc") or "")[:160])
        t += "\n"

    t += "RESTRIÇÕES DO CANAL (não negociáveis):\n"
    t += ("- WhatsApp Cloud API: fora da janela de 24h só sai template "
          "aprovado pela Meta; dentro da janela, texto livre e de graça.\n")
    t += ("- O número já foi restringido DUAS vezes pela Meta. O padrão "
          "punido é rajada de mensagens.\n")
    t += "- Teto de 5 mensagens proativas por dia por pessoa.\n"
    t += _linha("risco de envio agora", env.get("risco"))
    t += _linha("templates liberados", len(tpl.get("liberados") or []))
    t += _linha("templates faltando liberar", tpl.get("faltando"))
    return t


def montar_prompt(tipo: str, dados: dict) -> str:
    return "%s\n\n%s\n\n%s\n\n%s" % (
        _PAPEL[tipo], _REGRAS, retrato(dados), _TAREFA[tipo])


def pedir(tipo: str, dados: dict, modelo: str = "",
          reserva: str = "") -> tuple:
    """Devolve (ok, texto). Nunca levanta.

    Tenta o modelo bom e, se ele falhar, cai no do bot. Botao que devolve
    erro depois de o dono gastar a analise da semana seria o pior desfecho
    possivel.
    """
    if tipo not in CONSELHOS:
        return False, "conselho desconhecido"
    try:
        from litellm import completion
    except Exception:
        return False, "o modelo de linguagem não está disponível aqui"

    tentar = [m for m in (modelo or MODELO, reserva) if m]
    ultimo = ""
    texto = ""
    for i, qual in enumerate(tentar):
        try:
            resp = completion(
                model=qual,
                max_tokens=1600,
                messages=[
                    {"role": "system", "content": montar_prompt(tipo, dados)},
                    {"role": "user",
                     "content": "Faça a análise agora, seguindo exatamente a "
                                "estrutura pedida."},
                ])
            texto = (resp.choices[0].message.content or "").strip()
            if texto:
                break
        except Exception as e:
            ultimo = str(e)[:120] or type(e).__name__
            log.warning("[conselho] %s falhou (%d de %d)", qual, i + 1,
                        len(tentar), exc_info=True)
    if not texto and ultimo:
        return False, "não consegui falar com o modelo: %s" % ultimo
    if not texto:
        # NUNCA guardar resposta vazia como se fosse analise: a tela diria
        # "analisado em <hoje>" com nada dentro, e o dono acharia que o
        # conselheiro nao tinha o que dizer.
        return False, "o modelo respondeu vazio"
    return True, texto


def guardar(db, tipo: str, texto: str, quando: str) -> None:
    try:
        db.set_setting("conselho_" + tipo,
                       json.dumps({"texto": texto, "quando": quando,
                                   "versao": VERSAO}))
    except Exception:
        log.warning("[conselho] nao consegui guardar", exc_info=True)


def guardado(db, tipo: str) -> dict:
    try:
        bruto = db.get_setting("conselho_" + tipo)
        if bruto:
            d = json.loads(bruto)
            return {"texto": d.get("texto") or "",
                    "quando": d.get("quando") or "",
                    # Sem `versao` gravada, veio de antes deste controle:
                    # trata como antiga, que e o que ela e.
                    "versao": d.get("versao", 0)}
    except Exception:
        log.warning("[conselho] nao consegui ler o guardado", exc_info=True)
    return {"texto": "", "quando": "", "versao": VERSAO}
