# -*- coding: utf-8 -*-
"""
casos_de_uso.py — A base de conhecimento do que o Resolve AI entende.
=====================================================================
POR QUE ESTE ARQUIVO EXISTE

Em 05/08 o Mael mandou um print de reserva de voo da Azul. O motor
guardou como *[Outros] "Print de reserva de voo da Azul..."* — funcionou,
mas de um jeito burro: nao entendeu que voo tem check-in, tem bagagem, tem
"chegar 2h antes". Ficou estranho, e produto que parece burro na primeira
semana perde o beta tester.

O catalogo abaixo nasceu de DUAS fontes, nao de imaginacao:
  1. o que os 11 primeiros usuarios realmente pediram (18 itens reais)
  2. as lacunas que apareceram — VIAGEM e TREINO nao existiam nas 8
     categorias originais e apareceram no primeiro dia

COMO USAR
  • `CATALOGO`      -> lista completa, para o prompt do LLM
  • `resumo_prompt()` -> texto compacto pra injetar no system prompt
  • `KITS`          -> pacotes de 1 clique para o onboarding
  • `categoria_de(texto)` -> palpite deterministico de categoria

REGRA: este arquivo e DADO, nao logica. Adicionar caso novo aqui nao pode
quebrar nada. Quem decide o que fazer com o caso e o motor.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# CATALOGO: (categoria, gatilhos, exemplo do usuario, o que o bot deve fazer)
# ---------------------------------------------------------------------------
CATALOGO = [
    # ---- CONTAS (7 de 18 itens reais — a categoria dominante) --------------
    ("contas", ["luz", "energia", "enel", "cpfl", "light"],
     "luz 187 vence dia 20",
     "Conta recorrente mensal. Avisar 3 dias antes, 1 dia antes e no dia."),
    ("contas", ["internet", "vivo fibra", "claro net", "banda larga"],
     "internet vence todo dia 10",
     "Recorrente mensal. Mesma regua de aviso das contas."),
    ("contas", ["celular", "plano", "tim", "vivo", "claro"],
     "celular 59,90 dia 15",
     "Recorrente mensal."),
    ("contas", ["fatura", "cartao", "cartao de credito", "nubank", "itau"],
     "vencimento da minha fatura dia 14",
     "Avisar 3 dias antes — fatura de cartao tem multa e juros altos."),
    ("contas", ["aluguel", "condominio", "iptu"],
     "aluguel todo dia 5",
     "Recorrente. Valor alto: avisar com 5 dias."),
    ("contas", ["parcela", "parcelas", "financiamento", "carne", "consorcio"],
     "parcela do sofa dia 8",
     "Recorrente com fim previsto. Perguntar quantas faltam."),
    ("contas", ["agua", "sabesp", "gas", "comgas"],
     "agua vence dia 12", "Recorrente mensal."),

    # ---- SAUDE (4 de 18) ---------------------------------------------------
    ("saude", ["dentista", "consulta", "medico", "clinica"],
     "dentista dia 15 as 14h",
     "Compromisso com hora. Lembrar na vespera e 2h antes."),
    ("saude", ["exame", "marcar exames", "laboratorio", "coleta"],
     "marcar exames",
     "Tarefa sem data: perguntar ate quando e se precisa jejum."),
    ("saude", ["remedio", "medicamento", "comprimido", "acabar o remedio"],
     "meu remedio acaba em 20 dias",
     "Avisar 3 dias antes de acabar — repor sem falhar a dose."),
    ("saude", ["vacina", "dose", "reforco"],
     "segunda dose em 30 dias", "Avisar na data com folga de 2 dias."),
    ("saude", ["terapia", "psicologo", "psiquiatra", "fono"],
     "terapia toda terca 19h", "Recorrente semanal com hora."),
    ("saude", ["retorno", "resultado do exame"],
     "buscar resultado dia 22", "Lembrar no dia de manha."),

    # ---- VIAGEM (nao existia; apareceu no 1o dia com o print da Azul) ------
    ("viagem", ["voo", "passagem", "aviao", "azul", "gol", "latam",
                "embarque", "check-in", "checkin"],
     "print da reserva de voo",
     "Extrair data/hora/aeroporto. Criar 3 avisos: check-in 48h antes, "
     "sair de casa 3h antes (ou 2h em voo domestico), e embarque."),
    ("viagem", ["hotel", "reserva", "airbnb", "check-out", "checkout"],
     "hotel do dia 10 ao 14",
     "Avisar no check-in e na vespera do check-out."),
    ("viagem", ["onibus", "rodoviaria", "buser", "van"],
     "onibus 22h da rodoviaria", "Avisar 1h antes de sair de casa."),
    ("viagem", ["passaporte", "visto", "vacina de viagem"],
     "passaporte vence em marco",
     "Documento: avisar com 6 meses (renovacao demora)."),

    # ---- ROTINA / TREINO (nao existia; apareceu com o Jiu Jitsu) -----------
    ("treino", ["treino", "academia", "jiu jitsu", "crossfit", "musculacao",
                "corrida", "natacao", "pilates", "funcional"],
     "treino de jiu jitsu terca e quinta 20h",
     "Recorrente semanal. Nao insistir se a pessoa faltar — nao e cobranca."),
    ("treino", ["dieta", "agua", "beber agua", "whey", "suplemento"],
     "tomar whey as 16:30", "Recorrente diario, hora fixa."),

    # ---- MERCADO E REPOSICAO (2 de 18) ------------------------------------
    ("mercado", ["mercado", "compras", "supermercado", "feira"],
     "comprar no mercado",
     "Lista aberta: aceitar varios itens numa mensagem so. NUNCA concluir "
     "porque a pessoa escreveu 'feito' logo apos listar — ela pode estar "
     "dizendo que terminou de FALAR. (Aconteceu com o Fabio em 05/08.)"),
    ("mercado", ["acabou", "acabando", "esta no fim", "ultimo"],
     "o cafe ta acabando", "Repor: avisar em 2 dias."),
    ("mercado", ["fralda", "leite", "cafe", "papel higienico", "sabao"],
     "comprei fralda tamanho G hoje",
     "Consumo previsivel: estimar a proxima compra pela duracao tipica."),

    # ---- PET (1 de 18) -----------------------------------------------------
    ("pet", ["racao", "vermifugo", "antipulga", "banho e tosa", "veterinario"],
     "comprar racao para o Scott",
     "Repor antes de acabar. Guardar o nome do bicho e usar nas mensagens."),
    ("pet", ["vacina do", "castracao"],
     "vacina do Thor foi hoje", "Avisar quando a proxima estiver perto."),

    # ---- CARRO -------------------------------------------------------------
    ("carro", ["ipva", "licenciamento", "dpvat", "multa", "detran"],
     "ipva parcela 2 em marco",
     "Prazo legal: avisar com 7 dias — atraso vira multa e juros."),
    ("carro", ["oleo", "revisao", "pneu", "alinhamento", "km"],
     "troquei o oleo hoje com 45 mil km",
     "Proxima em ~10 mil km ou 6 meses, o que vier antes."),
    ("carro", ["seguro", "renovacao do seguro"],
     "seguro vence em agosto", "Avisar 30 dias antes pra cotar."),

    # ---- DATAS E PESSOAS ---------------------------------------------------
    ("datas", ["aniversario", "niver", "bodas", "casamento"],
     "aniversario da minha mae e 03/09",
     "Recorrente anual. Avisar 7 dias antes (tempo de comprar presente) "
     "e no dia."),
    ("datas", ["formatura", "batizado", "festa", "evento"],
     "formatura dia 12 as 19h", "Compromisso com hora."),

    # ---- BUROCRACIA E DOCUMENTOS ------------------------------------------
    ("burocracia", ["cnh", "rg", "documento", "certidao", "vence"],
     "minha cnh vence em marco",
     "Avisar com 60 dias — renovacao exige exame e agendamento."),
    ("burocracia", ["imposto de renda", "ir", "declaracao", "mei", "das"],
     "declarar imposto ate abril",
     "Prazo legal com multa. Avisar com 30, 7 e 1 dia."),
    ("burocracia", ["banco", "agendar", "cartorio", "inss"],
     "resolver no cartorio semana que vem",
     "Tarefa em horario comercial: sugerir dia util."),

    # ---- ENCOMENDAS --------------------------------------------------------
    ("encomendas", ["encomenda", "chega dia", "entrega", "correios",
                    "rastreio", "pedido"],
     "comprei um fone, chega dia 12",
     "Avisar no dia previsto e cobrar se passar do prazo."),
    ("encomendas", ["troca", "devolucao", "garantia", "arrependimento"],
     "posso trocar ate dia 20",
     "Prazo que expira: avisar com 3 dias de folga."),

    # ---- TRABALHO E ESTUDO -------------------------------------------------
    ("trabalho", ["reuniao", "call", "apresentacao", "entrega", "prazo"],
     "reuniao hoje as 10h com a Bruna",
     "Avisar 30 min antes — tempo de se preparar."),
    ("estudos", ["prova", "trabalho da facul", "tcc", "matricula", "aula"],
     "prova de calculo dia 18",
     "Avisar com 7 dias (tempo de estudar) e na vespera."),

    # ---- CASA --------------------------------------------------------------
    ("casa", ["faxina", "diarista", "lavar", "trocar o filtro", "dedetizacao"],
     "trocar o filtro do purificador a cada 6 meses",
     "Recorrente longo: guardar a data da ultima troca."),
]

# ---------------------------------------------------------------------------
# KITS: pacotes de 1 clique pro onboarding
# Montados a partir do que os 11 primeiros usuarios pediram de verdade.
# ---------------------------------------------------------------------------
KITS = [
    # ORDEM IMPORTA: a lista aparece nesta sequencia e quase ninguem rola
    # ate o fim. A ordem aqui e por (1) sinal real dos 11 primeiros usuarios
    # e (2) ARITMETICA DO TRIAL — kit que so dispara daqui a um mes nao
    # prova nada dentro dos 14 dias.
    #
    # (id, titulo <=24, descricao <=72, [opcoes], pergunta do passo 2)

    # Semanal: e o UNICO que garante aviso dentro do trial. Jiu jitsu
    # terca/quinta apareceu organico no primeiro dia de uso.
    ("aulas", "\U0001F4C5 Aulas da semana",
     "Treino, aula, terapia — toda semana",
     ["Treino / academia", "Aula (curso, idioma)", "Terapia / consulta fixa",
      "Aula do filho"],
     "Que dia da semana e que horas?\n"
     "_\"terca e quinta 20h\"_\n"
     "_\"todo sabado 9h\"_"),

    # Carro foi o assunto com mais sinal real: IPVA, troca de oleo e
    # "olhar os pontos" apareceram organicos.
    ("carro", "\U0001F697 Carro em dia",
     "IPVA, licenciamento, seguro, oleo",
     ["IPVA", "Licenciamento", "Seguro", "Troca de oleo"],
     "Me diz a data (ou o km, no oleo):\n"
     "_\"IPVA vence 15/03\"_\n"
     "_\"troquei o oleo hoje, 45 mil km\"_\n\n"
     "N\u00e3o lembra? Manda *n\u00e3o sei* que a\n"
     "gente descobre junto."),

    # E literalmente a promessa de venda do produto e nao existia na lista.
    ("parcelas", "\U0001F4B3 Parcelas e boletos",
     "Parcela, carne, aluguel, fatura",
     ["Parcela / carne", "Fatura do cartao", "Aluguel", "Mensalidade"],
     "Que dia vence e quanto e?\n"
     "_\"parcela do sofa dia 8, 250\"_\n"
     "_\"fatura dia 10\"_\n\n"
     "S\u00f3 o dia j\u00e1 me serve."),

    ("contas_casa", "\U0001F3E0 Contas da casa",
     "Luz, internet, celular e agua",
     ["Luz", "Internet", "Celular", "\u00c1gua"],
     "Que dia do mes vence?\n"
     "_So o dia ja serve: \"20\"_\n"
     "_Ou completo: \"luz 187 dia 20\"_"),

    ("pet", "\U0001F43E Pet",
     "Racao, vacina, vermifugo, banho",
     # "Banho e tosa" saiu: o petshop ja liga. Lembrete que duplica algo
     # que ela ja recebe treina a pessoa a ignorar o bot.
     ["Ra\u00e7\u00e3o acabando", "Vacina vencendo", "Vermifugo",
      "Antipulga"],
     "Quando foi a ultima vez?\n"
     "_\"comprei racao de 15kg hoje\"_\n"
     "_\"vacina do Thor foi ontem\"_"),

    ("saude", "\U0001FA7A Sa\u00fade em dia",
     "Consulta, exame, remedio que acaba",
     # "Consulta marcada" virou "Consulta pra MARCAR": a clinica ja manda
     # SMS de quem tem hora. O valor aqui e lembrar de marcar.
     ["Consulta pra marcar", "Exame pra fazer", "Remedio acabando"],
     "Ate quando precisa resolver?\n"
     "_\"marcar dentista ate dia 30\"_\n"
     "_\"meu remedio acaba em 20 dias\"_"),
]


# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s)


# ---------------------------------------------------------------------------
# PONTE PARA AS CATEGORIAS DE PRODUCAO
# ---------------------------------------------------------------------------
# O banco so aceita os nomes de `db.VALID_CATEGORIES`. Categoria fora da lista
# e rejeitada e o item volta pra "Outros" — que e exatamente o bug que este
# arquivo existe pra matar. Entao o catalogo fala em minusculo (mais facil de
# escrever e ler) e este mapa traduz pro nome oficial na saida.
#
# VIAGEM e TREINO sao novos em db.VALID_CATEGORIES. Entraram porque apareceram
# no primeiro dia de uso real, nao porque pareciam bonitos numa lista.
MAPA_PRODUCAO = {
    "contas":     "Contas",
    "saude":      "Saúde",
    "viagem":     "Viagem",      # novo
    "treino":     "Treino",      # novo
    "mercado":    "Alimentação",
    "pet":        "Pet",
    "carro":      "Veículo",
    "casa":       "Casa",
    "datas":      "Lazer",
    "encomendas": "Casa",
    "burocracia": "Contas",
    "trabalho":   "Outros",
    "estudos":    "Outros",
}


def categoria_de(texto: str, producao: bool = False):
    """Palpite deterministico de categoria — ou None.

    Rede de seguranca pro LLM, nao substituto: se o modelo nao classificar,
    isto evita que tudo caia em "Outros" como caiu o voo da Azul.

    Gatilho mais longo vence — senao "agua" rouba o match de "agua de coco"
    e "voo" perde pra qualquer coisa maior no meio da frase.

    producao=True devolve o nome aceito por db.VALID_CATEGORIES.
    """
    t = _norm(texto)
    melhor, tamanho = None, 0
    for categoria, gatilhos, _ex, _oq in CATALOGO:
        for g in gatilhos:
            gn = _norm(g).strip()
            if gn and gn in t and len(gn) > tamanho:
                melhor, tamanho = categoria, len(gn)
    if melhor and producao:
        return MAPA_PRODUCAO.get(melhor)
    return melhor


# Interesse marcado na landing -> categorias do CATALOGO. Serve pra puxar
# exemplos REAIS (os mesmos que o motor entende) em vez de inventar frase.
# Frases prontas pra pessoa COPIAR, por interesse marcado na landing.
#
# Escritas a mao, com acento e no formato que o motor entende. NAO saem do
# CATALOGO: aquilo e dado tecnico pro prompt do LLM e esta sem acento de
# proposito — "ipva parcela 2 em marco" na tela da pessoa parece bot
# quebrado. O que ela ve tem que ser copia decente.
EXEMPLOS_PARA_COPIAR = {
    "contas":     ['luz 187 vence dia 20',
                   'internet vence todo dia 10'],
    "carro":      ['IPVA vence 15/03',
                   'troquei o óleo hoje, 45 mil km'],
    "saude":      ['dentista dia 15 às 14h',
                   'meu remédio acaba em 20 dias'],
    "pet":        ['comprei ração de 15kg hoje',
                   'vacina do Thor foi ontem'],
    "datas":      ['aniversário da minha mãe é 03/09',
                   'formatura dia 12 às 19h'],
    "mercado":    ['o café tá acabando',
                   'comprei fralda tamanho G hoje'],
    "encomendas": ['comprei um fone, chega dia 12',
                   'posso trocar até dia 20'],
    "burocracia": ['minha CNH vence em março',
                   'declarar imposto até abril'],
}


def exemplos_por_interesse(interesses: str = "", n: int = 3) -> list:
    """Frases prontas pra pessoa copiar, escolhidas pelo que ELA marcou.

    Sai do CATALOGO, nao de texto inventado: sao exatamente os formatos que
    o motor entende, entao o que ela copiar vai funcionar de primeira.

    VARIOS exemplos, nao um. Quem chega no zap sem saber o que mandar trava
    — e mostrar so uma sugestao faz parecer que o bot serve pra uma coisa
    so. Tres cabem na tela do celular sem virar cardapio (o cardapio de 8
    ja foi testado e deu 8 cadastros / 1 item).

    Sempre completa com um exemplo de conta: e o caso mais universal e o
    que melhor mostra a promessa (aviso antes de vencer).
    """
    marcados = [p.strip().lower()
                for p in (interesses or "").split(",") if p.strip()]
    # "contas" entra sempre no fim: e o caso mais universal e o que melhor
    # mostra a promessa (aviso antes de vencer). Se ela ja marcou, nao
    # duplica.
    if "contas" not in marcados:
        marcados.append("contas")

    saida = []
    # uma frase de CADA interesse primeiro — assim ela ve que o bot serve
    # pra mais de uma coisa, que e o ponto de mostrar varias opcoes
    for m in marcados:
        ex = EXEMPLOS_PARA_COPIAR.get(m) or []
        if ex and ex[0] not in saida:
            saida.append(ex[0])
        if len(saida) >= n:
            return saida[:n]
    # sobrou espaco: completa com a segunda frase de cada
    for m in marcados:
        for e in (EXEMPLOS_PARA_COPIAR.get(m) or [])[1:]:
            if e not in saida:
                saida.append(e)
            if len(saida) >= n:
                return saida[:n]
    return saida[:n]


def bloco_de_exemplos(interesses: str = "", n: int = 3) -> str:
    """Os exemplos ja formatados pro WhatsApp: um por linha, em italico."""
    ex = exemplos_por_interesse(interesses, n)
    return "\n".join('_"' + e + '"_' for e in ex)


def resumo_prompt(limite: int = 4000) -> str:
    """Bloco compacto pro system prompt do LLM."""
    linhas = ["CASOS QUE VOCE CONHECE (categoria | exemplo | o que fazer):"]
    for categoria, _g, exemplo, oque in CATALOGO:
        linhas.append(f"- {categoria} | \"{exemplo}\" | {oque}")
    txt = "\n".join(linhas)
    return txt[:limite]


def regras_prompt() -> str:
    """Versão CURTA pro system prompt — só o que o LLM não deduz sozinho.

    `resumo_prompt()` tem 3.5k chars. Isso ia em TODA chamada do modelo, ~900
    tokens por mensagem de usuário, pra ensinar o que a `categoria_de()` já
    resolve de graça em Python. Prompt caro é prompt que ninguém revisa.

    Aqui fica só o comportamento que não dá pra derivar da categoria: quantos
    avisos criar, e onde o modelo historicamente errou feio.
    """
    return (
        "CASOS QUE VOCÊ JÁ CONHECE (não trate como estranho):\n"
        "- VOO/passagem (Azul, Gol, Latam) ou print de reserva: extraia data, "
        "hora e aeroporto. Crie 3 avisos: check-in 48h antes, sair de casa 3h "
        "antes, e o embarque.\n"
        "- TREINO/academia/jiu jitsu: recorrente semanal. Registre, mas NUNCA "
        "cobre nem insista se a pessoa faltar.\n"
        "- BOLETO/conta (luz, água, internet, celular, fatura, aluguel, "
        "parcela): recorrente mensal, avise 3 dias antes e no dia.\n"
        "- PRAZO LEGAL (IPVA, CNH, imposto, licenciamento): atraso vira multa. "
        "Avise com folga grande.\n"
        "- REPOSIÇÃO ('o café tá acabando', 'ração do Scott'): estime quando "
        "acaba e avise antes.\n"
        "- LISTA DE COMPRAS: aceite vários itens numa mensagem só. Se a pessoa "
        "escrever 'feito'/'pronto' logo APÓS listar, ela terminou de FALAR — "
        "não concluiu nada. NUNCA dê baixa nesse caso.\n"
        "- DOCUMENTO que vence (passaporte, CNH, seguro): avise meses antes, "
        "renovação demora.\n"
    )


def kit_por_id(kit_id: str):
    for k in KITS:
        if k[0] == kit_id:
            return k
    return None


def kit_por_rotulo(texto: str):
    """Acha o kit pelo que o WhatsApp devolve quando a pessoa toca na lista.

    A Meta NAO devolve o id da linha no texto: o to_evolution_shape entrega
    o TITULO ("\U0001F697 Carro em dia") como {"conversation": ...}. Casar so
    por id deixaria o toque virar texto livre pro LLM — foi o que aconteceu
    com os botoes de confirmacao em 11/08 ("Isso mesmo" -> "Como vai?").
    """
    t = _norm(texto).strip()
    if not t:
        return None
    if t.startswith("kit "):
        t = t[4:].strip()
    for k in KITS:
        if t in (_norm(k[0]).strip(), _norm("kit_" + k[0]).strip()):
            return k
    for k in KITS:
        if t == _norm(k[1]).strip():
            return k
    return None


def opcao_por_rotulo(kit, texto: str):
    """Qual opcao DENTRO do kit a pessoa tocou (passo 2)."""
    if not kit:
        return None
    t = _norm(texto).strip()
    if not t:
        return None
    for opc in kit[3]:
        if t == _norm(opc).strip():
            return opc
    return None


def linhas_opcoes(kit) -> list:
    """Linhas da lista do passo 2 — as opcoes de um kit."""
    if not kit:
        return []
    return [{"id": "opc_" + str(n), "title": o[:24]}
            for n, o in enumerate(kit[3])]


# ---------------------------------------------------------------------------
# COPY DOS KITS — escrita pra tela de celular
# ---------------------------------------------------------------------------
# Regras aplicadas aqui (as mesmas do motor_v8):
#   - no maximo ~35 caracteres por linha, senao quebra torto no WhatsApp
#   - uma ideia por linha, quebra de linha de verdade
#   - emoji como pontuacao, nunca enfeite
#   - UMA pergunta por vez. Pedir 4 datas de uma vez e memoria demais pra
#     quem esta no zap com o filho chorando do lado — e foi assim que a
#     primeira versao do onboarding deu 8 cadastros e 1 item.
def texto_passo1(kit) -> str:
    """Depois de tocar no kit: pergunta QUAL, nao pede data ainda."""
    if not kit:
        return ""
    return (kit[1] + "\n\n"
            "Vamos pelo que mais pesa.\n"
            "Qual desses voc\u00ea *n\u00e3o pode* esquecer?")


def texto_passo2(kit, opcao: str) -> str:
    """Depois de escolher a opcao: UMA pergunta, com exemplo pronto."""
    if not kit:
        return ""
    return ("*" + str(opcao) + "*\n\n" + kit[4])


# O que a pessoa marcou na landing -> quais kits fazem sentido pra ela.
# Um interesse pode puxar mais de um kit: quem marcou "contas" tem conta de
# casa E parcela de cartao, e mostrar so um dos dois desperdica o unico
# momento em que ela esta olhando a lista.
INTERESSE_PARA_KITS = {
    "contas":     ["contas_casa", "parcelas"],
    "burocracia": ["carro", "parcelas"],
    "carro":      ["carro"],
    "saude":      ["saude"],
    "pet":        ["pet"],
    "datas":      ["aulas"],
    "mercado":    ["pet", "contas_casa"],
    "encomendas": ["parcelas"],
}


def linhas_kits(interesses: str = "") -> list:
    """Linhas da lista do WhatsApp, com os kits do INTERESSE dela primeiro.

    Quem marcou "carro" na landing ve o kit do carro no topo. Nenhum kit
    some — a lista aceita 10 e nos temos 6, entao o resto continua rolavel
    logo abaixo. Ordenar e barato e muda a chance de ela tocar em algum.
    """
    marcados = [p.strip().lower()
                for p in (interesses or "").split(",") if p.strip()]
    preferidos = []
    for m in marcados:
        for kid in INTERESSE_PARA_KITS.get(m, []):
            if kid not in preferidos:
                preferidos.append(kid)
    ordenados = ([k for kid in preferidos for k in KITS if k[0] == kid]
                 + [k for k in KITS if k[0] not in preferidos])
    return [{"id": "kit_" + k[0], "title": k[1][:24],
             "description": k[2][:72]} for k in ordenados[:10]]
