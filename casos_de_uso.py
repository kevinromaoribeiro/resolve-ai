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
    ("contas_casa", "\U0001F3E0 Contas da casa",
     "Luz, internet, celular e água",
     ["Luz", "Internet", "Celular", "Água"],
     "Qual dia do mês cada uma vence? Pode mandar tudo junto: "
     "_\"luz 20, internet 10, celular 15\"_"),

    ("saude", "\U0001FA7A Saúde em dia",
     "Consulta, exame e remédio que acaba",
     ["Consulta", "Exame", "Remédio"],
     "Me diz o que tem marcado ou o que precisa marcar. "
     "_\"dentista dia 15 às 14h\"_"),

    ("carro", "\U0001F697 Carro em ordem",
     "IPVA, licenciamento, seguro e revisão",
     ["IPVA", "Licenciamento", "Seguro", "Troca de óleo"],
     "Me diz o que você lembra e eu monto o resto: "
     "_\"troquei o óleo hoje, 45 mil km\"_"),

    ("rotina", "\U0001F3CB️ Rotina e treino",
     "Academia, treino, água, suplemento",
     ["Treino", "Suplemento"],
     "Quais dias e que horas? _\"jiu jitsu terça e quinta 20h\"_"),

    ("viagem", "✈️ Viagem",
     "Voo, hotel, check-in e documentos",
     ["Voo", "Check-in", "Hotel"],
     "Manda o *print da reserva* que eu tiro tudo dele — "
     "horário, check-in e a hora de sair de casa."),
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


def linhas_kits() -> list:
    """Linhas prontas pra mensagem de lista do WhatsApp."""
    return [{"id": f"kit_{k[0]}", "title": k[1][:24], "description": k[2][:72]}
            for k in KITS]
