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

log = logging.getLogger("resolveai")

CONSELHOS = ("crescimento", "preco", "marketing", "cx", "produto")

# Vale 6h: o retrato do negocio nao muda de manha pra tarde, e reanalisar a
# cada clique so gastaria dinheiro pra ouvir a mesma coisa.
VALIDADE_H = 6

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
        "Responda em no máximo 350 palavras, em português do Brasil, "
        "direto, sem introdução.\n\n"
        "1. O PREÇO ATUAL está certo, alto ou baixo? Justifique com o custo "
        "real por cliente e com o que o produto substitui.\n"
        "2. QUANTO COBRAR, com um número. Se for para manter, diga manter.\n"
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
    "- Antes de sugerir qualquer coisa, LEIA a lista de capacidades que já "
    "estão no ar. Sugerir o que já existe é o pior conselho possível para "
    "quem constrói demais.\n"
    "- Se a sua ideia já está na lista, o conselho vira COMO FAZER "
    "DESCOBRIREM, e não como construir.\n"
    "- Toda ideia sua tem que caber numa pessoa só trabalhando. Não existe "
    "time, não existe verba de mídia e não existe caixa.\n"
    "- Amarre cada sugestão a uma das metas de lucro do retrato: diga o que "
    "ela move em direção a elas. Ideia que não aproxima da meta não entra.\n"
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

    t += "CUSTO REAL POR PESSOA (últimos 30 dias, medido):\n"
    t += _linha("médio", cus.get("medio"))
    t += _linha("mediana", cus.get("mediana"))
    t += _linha("o mais caro da base", cus.get("maior"))
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


def pedir(tipo: str, dados: dict, modelo: str = "") -> tuple:
    """Devolve (ok, texto). Nunca levanta."""
    if tipo not in CONSELHOS:
        return False, "conselho desconhecido"
    try:
        from litellm import completion
    except Exception:
        return False, "o modelo de linguagem não está disponível aqui"
    try:
        resp = completion(
            model=modelo or "gpt-4o-mini",
            max_tokens=1200,
            messages=[
                {"role": "system", "content": montar_prompt(tipo, dados)},
                {"role": "user",
                 "content": "Faça a análise agora, seguindo exatamente a "
                            "estrutura pedida."},
            ])
        texto = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        log.warning("[conselho] falha ao consultar o modelo", exc_info=True)
        return False, "não consegui falar com o modelo: %s" % (
            str(e)[:120] or type(e).__name__)
    if not texto:
        # NUNCA guardar resposta vazia como se fosse analise: a tela diria
        # "analisado em <hoje>" com nada dentro, e o dono acharia que o
        # conselheiro nao tinha o que dizer.
        return False, "o modelo respondeu vazio"
    return True, texto


def guardar(db, tipo: str, texto: str, quando: str) -> None:
    try:
        db.set_setting("conselho_" + tipo,
                       json.dumps({"texto": texto, "quando": quando}))
    except Exception:
        log.warning("[conselho] nao consegui guardar", exc_info=True)


def guardado(db, tipo: str) -> dict:
    try:
        bruto = db.get_setting("conselho_" + tipo)
        if bruto:
            d = json.loads(bruto)
            return {"texto": d.get("texto") or "", "quando": d.get("quando") or ""}
    except Exception:
        log.warning("[conselho] nao consegui ler o guardado", exc_info=True)
    return {"texto": "", "quando": ""}
