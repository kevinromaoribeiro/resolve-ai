"""Catálogo de templates da Meta Cloud API — DADO, não lógica.

Por que este pacote existe (16/08/2026): na API oficial, mensagem proativa
para quem está FORA da janela de 24h simplesmente não chega — a Meta devolve
erro 131047 e o lembrete morre no caminho. Ou seja: sem template aprovado, o
produto não consegue avisar justamente quem parou de responder.

Regra de categoria que manda no texto: conteúdo promocional dentro de template
UTILITY é rejeitado. Nenhum corpo aqui vende nada. O convite de assinatura
continua saindo dentro da janela, onde texto livre é permitido.

Como mexer aqui:
  1. mude o corpo/categoria neste arquivo
  2. rode `pytest tests/test_m20_templates.py` (numeração de variável, limite
     de 1024 chars e linguagem promocional são verificados por teste)
  3. gere de novo o `templates/SUBMISSAO.md` e resubmeta no Business Manager
  4. o nome do template é o contrato com a Meta: mudar o nome exige nova
     aprovação, mudar só o corpo também.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Template:
    nome: str
    categoria: str          # UTILITY | MARKETING | AUTHENTICATION
    idioma: str
    corpo: str
    variaveis: list         # nomes, na ordem de {{1}}, {{2}}, ...
    justificativa: str      # o texto que explica o uso na submissão
    exemplo: list = field(default_factory=list)
    # M3.0 — botões de resposta rápida, declarados NA SUBMISSÃO.
    #
    # Fora da janela de 24h só sai template, então botão em proativa tem que
    # nascer aqui: não dá pra acrescentar na hora do envio. Cada título vira
    # o texto que volta pro bot quando a pessoa clica, e por isso tem que ser
    # um comando que o parser entende — há teste varrendo isto.
    #
    # Limites da Meta: no máximo 3 botões, até 20 caracteres cada.
    botoes: list = field(default_factory=list)
    # M3.0 — o que este template FAZ, em português, pro painel.
    #
    # O Kevin: "eu preciso ter o nome do que ele faz e um botão pra ativar".
    # `resolveai_conta_a_vencer` não diz nada pra quem está decidindo o que
    # mandar; "Avisa que uma conta vence amanhã" diz.
    rotulo: str = ""


# ---------------------------------------------------------------------------
# O CATÁLOGO
# ---------------------------------------------------------------------------
# NENHUM CORPO COMEÇA NEM TERMINA EM PARÂMETRO — daí o "Oi " na frente de
# metade deles. A Meta reprova a submissão nesses dois casos, e reprovação é
# uma rodada inteira de espera por um "Oi" que ninguém ia reparar. Cinco
# corpos violavam a regra que o próprio repo declarava (achado da auditoria
# M2.5, P1-4); `test_corpo_nao_comeca_nem_termina_em_parametro` cobra agora.
# ---------------------------------------------------------------------------
# Cada corpo diz o que o bot vai fazer e o que a pessoa pode responder. Nada
# de "aproveite", "assine", "sentimos sua falta" — ver DECISOES.md.

CATALOGO: dict[str, Template] = {}


def _reg(t: Template) -> None:
    CATALOGO[t.nome] = t


_reg(Template(
    nome="resolveai_lembrete_hora",
    rotulo="Avisa na hora marcada de um compromisso",
    categoria="UTILITY",
    idioma="pt_BR",
    corpo=("Chegou a hora: *{{1}}*.\n\n"
           "Responda *feito* que eu dou baixa, ou *adiar 1h* se precisar "
           "de mais tempo."),
    variaveis=["descricao"],
    exemplo=["levar o carro na revisão"],
    justificativa=(
        "Lembrete de hora marcada que o próprio usuário cadastrou no "
        "assistente, com data e hora escolhidas por ele. Disparado uma única "
        "vez, no horário que ele pediu. O usuário responde 'feito' ou "
        "'adiar' na mesma conversa."),
))

_reg(Template(
    nome="resolveai_item_vencido",
    rotulo="Cobra um item que venceu e não teve baixa",
    categoria="UTILITY",
    idioma="pt_BR",
    corpo=("Oi {{1}}, *{{2}}* venceu {{3}} e eu não registrei a baixa.\n\n"
           "Responda *feito* se já resolveu, ou *adiar* que eu remarco."),
    variaveis=["primeiro_nome", "descricao", "quando"],
    exemplo=["Kevin", "conta de luz", "ontem"],
    justificativa=(
        "Aviso de vencimento de um compromisso que o usuário cadastrou "
        "(conta, consulta, prazo). Enviado no máximo uma vez por item, e o "
        "usuário pode encerrar ou remarcar respondendo na conversa."),
))

_reg(Template(
    nome="resolveai_resumo_do_dia",
    rotulo="Resumo dos compromissos dos próximos dias",
    categoria="UTILITY",
    idioma="pt_BR",
    corpo=("Oi {{1}}, você tem *{{2}}* compromisso(s) guardado(s) para os "
           "próximos dias.\n\n"
           "O mais próximo é *{{3}}*.\n\n"
           "Responda *ver tudo* para a lista completa."),
    variaveis=["primeiro_nome", "quantidade", "proximo"],
    exemplo=["Kevin", "3", "IPVA (vence 20/08)"],
    justificativa=(
        "Resumo dos compromissos que o próprio usuário cadastrou, no dia da "
        "semana que ele escolheu ao se cadastrar. Conteúdo é exclusivamente "
        "a agenda dele; não há oferta nem divulgação."),
))

_reg(Template(
    nome="resolveai_reengajamento_pendentes",
    rotulo="Lembra de um item parado há dias na lista",
    categoria="UTILITY",
    idioma="pt_BR",
    # REESCRITO EM 26/08/2026, depois de a Meta recusar a versão anterior.
    #
    # O corpo dizia "você tem *2* item(ns) pendente(s)" e o Business Manager
    # respondeu, antes mesmo da submissão: *"A categoria não corresponde —
    # este modelo será rejeitado"*, recomendando MARKETING.
    #
    # E ela classificou certo. A régua da Meta não é o tom do texto, é o
    # MOTIVO da mensagem: falar de UM item que a pessoa cadastrou é
    # utilidade; falar de VOLTAR a usar é marketing. Contagem de pendências
    # é uma mensagem sobre o produto — "sua conta de luz está parada desde
    # 12/08" é uma mensagem sobre a vida dela. A segunda é a que faz alguém
    # pagar R$ 19,90, então o texto fraco era nosso, não o critério dela.
    #
    # A AÇÃO É "VER TUDO", E NÃO "FEITO". Escrevi "responda *feito*" na
    # primeira versão deste conserto e a auditoria pegou: o disparo de
    # reengajamento tem `item_id: None` POR CONSTRUÇÃO (anti-churn e
    # winback, no scheduler). Quem resolve `feito` sozinho é o
    # `_alvo_da_baixa`, que só enxerga disparos COM item_id — então o item
    # citado aqui é inalcançável e o `feito` fecha o item do último alarme.
    # A pessoa lê "trocar a lâmpada" e o bot responde "dei baixa em conta de
    # luz". Perda de dado, regra 10, e o mesmo P1-7 que o comentário acima
    # jura estar honrando: prometer no corpo só o que o Python garante.
    corpo=("Oi {{1}}, *{{2}}* continua na sua lista desde {{3}} e eu não "
           "registrei a baixa.\n\n"
           "Responda *ver tudo* para revisar seus itens."),
    variaveis=["primeiro_nome", "descricao", "desde"],
    exemplo=["Kevin", "trocar o óleo do carro", "12/08"],
    justificativa=(
        "Aviso sobre um compromisso específico que o próprio usuário "
        "cadastrou no assistente e que segue em aberto, com a data em que "
        "ele foi criado. O conteúdo é o dado dele, e a ação oferecida é "
        "encerrar ou remarcar esse item na mesma conversa."),
))

_reg(Template(
    nome="resolveai_fim_de_trial_aviso",
    rotulo="Avisa que o teste está acabando e oferece a assinatura",
    # MARKETING, e é o único aqui. Decisão do dono em 27/08/2026, depois de
    # a Meta recusar como utilidade — e ela está certa: "seu período de teste
    # termina em 2 dias" não fala de um compromisso que a pessoa cadastrou,
    # fala da relação COMERCIAL dela com o produto. Não existe reescrita que
    # mude isso sem mudar a mensagem.
    #
    # O raciocínio dele, e é o certo: é literalmente uma mensagem tentando
    # fechar a assinatura, e sai UMA vez por usuário na vida inteira do
    # trial. O preço por mensagem e a cota de marketing são irrisórios
    # diante do que ela tenta converter.
    #
    # O CORPO VENDE, e é a única mensagem do catálogo que faz isso.
    #
    # Enquanto ele tentava passar como utilidade, o texto tinha que ser
    # neutro. Agora que é marketing assumido, ser neutro seria só perder o
    # tiro: esta mensagem existe para converter, sai UMA vez por usuário, e
    # é a última coisa que quem sumiu vai ler antes do trial acabar.
    #
    # O ARGUMENTO É O QUE DE FATO MUDA, e isso importa mais que o tom. Os
    # itens NÃO são apagados quando o trial vence (`user_can_receive` só
    # deixa de liberar disparo; o banco continua intacto). Dizer "seus
    # dados somem" seria mentira, e mentira que gera pedido de reembolso.
    # O que a pessoa perde é o AVISO — que é o produto inteiro. Então é
    # isso que o texto diz, e nada além.
    #
    # "Responda *assinar*" só está aqui porque o comando EXISTE
    # (`COMANDOS_ASSINATURA`, no wa_bot) e devolve o link de pagamento. É a
    # regra que custou o P0 desta fase: prometer no corpo só o que o Python
    # garante. E o link não vai no template de propósito — a resposta dela
    # abre a janela de 24h, e aí o link sai como texto livre, com preço
    # mensal e anual, sem depender de botão aprovado pela Meta.
    categoria="MARKETING",
    idioma="pt_BR",
    corpo=("Oi {{1}}, seu teste grátis acaba em *{{2}}* dia(s).\n\n"
           "Nesse tempo eu guardei *{{3}}* compromisso(s) seu(s) e te avisei "
           "antes de cada um vencer. Depois que acabar, tudo continua "
           "guardado aqui — mas eu paro de te avisar.\n\n"
           "São R$ 19,90 por mês pra seguir. Responda *assinar* que eu te "
           "mando o link."),
    variaveis=["primeiro_nome", "dias", "quantidade_itens"],
    exemplo=["Kevin", "2", "7"],
    justificativa=(
        "Aviso ao usuário de que o período de teste da conta dele está "
        "acabando, com o número de compromissos que ele cadastrou, e oferta "
        "de continuidade do serviço. O usuário responde na mesma conversa "
        "para receber o link de pagamento."),
))


# --- M2.5: os dois que faltavam --------------------------------------------

_reg(Template(
    nome="resolveai_conta_a_vencer",
    rotulo="Avisa que uma conta vence em breve",
    categoria="UTILITY",
    idioma="pt_BR",
    # O DISPARO MAIS COMUM DO PRODUTO, e ate agora o unico sem template.
    #
    # O M2.0 tirou "vencimento" do mapa de propriedade: o unico template que
    # existia era o "Chegou a hora", e usa-lo tres dias antes seria urgencia
    # falsa, sem data e sem valor. A decisao estava certa e a consequencia
    # era ruim do mesmo jeito — quem passou 24h sem falar com o bot nao
    # recebia aviso NENHUM de conta a vencer. O conserto e este template,
    # que diz a data.
    corpo=("Oi {{1}}, *{{2}}* vence em *{{3}}*.\n\n"
           "Responda *feito* quando resolver, ou *adiar* que eu remarco."),
    variaveis=["primeiro_nome", "descricao", "quando"],
    exemplo=["Kevin", "conta de luz", "20/08"],
    justificativa=(
        "Aviso de vencimento de um compromisso financeiro que o próprio "
        "usuário cadastrou no assistente, com a data que ele informou. "
        "Enviado uma vez por item por dia de aviso, e o usuário pode "
        "encerrar ou remarcar respondendo na mesma conversa."),
))

# O RESUMO DE GASTOS NÃO TEM TEMPLATE, e isso é decisão declarada.
#
# Ele existia aqui até 26/08/2026, e a Meta recusou pelo mesmo motivo do
# reengajamento: um resumo semanal é um AGREGADO, e agregado é mensagem
# sobre o produto, não sobre um compromisso da pessoa. Não existe versão
# dele que fale de um item só sem deixar de ser um resumo — então não dá
# pra "consertar o texto", dá pra escolher onde ele vive.
#
# ESCOLHA: ele vive dentro da janela de 24h, como texto livre. O custo é
# pequeno e vale escrever por quê: o resumo só é montado pra quem registrou
# 2+ despesas na semana (`GASTOS_MIN_LANCAMENTOS`), ou seja, pra quem está
# usando o bot — e quem usa quase sempre falou com ele nas últimas 24h.
# Quem sumiu há dias não tem gasto registrado e não receberia o resumo de
# jeito nenhum.
#
# A alternativa era submeter como MARKETING: mais caro por mensagem, com
# opt-out obrigatório e contando na cota de marketing do número — num
# número que já levou duas restrições da Meta. Não compensa por um digest.


# ---------------------------------------------------------------------------
# KIND (do motor proativo) -> TEMPLATE
# ---------------------------------------------------------------------------
# Kind que não está aqui NÃO tem template: fora da janela ele não sai, e isso
# é registrado. Inventar um template parecido pra "aproveitar" seria mandar
# texto que a Meta aprovou pra outra finalidade.
# AUDITORIA M2.0 (P1-6): "vencimento" tinha sido REMOVIDO daqui, porque o
# texto livre dele é o aviso D-1 ("vence em 20/08") e o único template que
# existia diria "Chegou a hora" — urgência falsa, sem data e sem valor.
# M2.5: voltou, com template PRÓPRIO (`resolveai_conta_a_vencer`), que traz a
# data. O princípio não mudou: kind só ganha template que diga a mesma coisa
# que o texto livre diria.
#
# "hora" só usa template na variante NA HORA. O scheduler tem outras duas —
# atrasado ("passou da hora, era às 16:00", caso da Carol, 11/08) e o
# escalonamento do M1.5 ("já te chamei 3x, remarcar ou tirar da lista?") —
# e achatar as três em "Chegou a hora... responda feito ou adiar 1h" traz de
# volta exatamente os bugs que essas duas variantes existem pra consertar.
TRIAL_ESTENDIDO = Template(
    nome="resolveai_trial_estendido",
    rotulo="Conta que você liberou mais dias de teste",
    # UTILITY, e desta vez a régua da Meta está do nosso lado. A régua é o
    # MOTIVO da mensagem: falar de um prazo determinado que mudou na conta da
    # pessoa é utilidade; convidar a voltar ou a comprar é marketing. Aqui é
    # a primeira coisa — o teste dela ganhou dias e a data nova é esta.
    #
    # Comparar com `fim_de_trial_aviso`, que é MARKETING: aquele pede a
    # assinatura, este só informa o prazo novo. Se este ganhar um "assine
    # agora" no corpo, vira marketing e a Meta recusa — com razão.
    categoria="UTILITY",
    idioma="pt_BR",
    corpo=("Oi {{1}}, liberei mais *{{2}}* dia(s) de teste pra você.\n\n"
           "Seu acesso vale até *{{3}}*. Continuo te avisando dos seus "
           "compromissos até lá."),
    variaveis=["primeiro_nome", "dias_extras", "nova_data"],
    justificativa=(
        "Confirmação de mudança no prazo da conta do próprio usuário. O "
        "administrador estendeu o período de teste e o usuário é informado "
        "da nova data de validade do acesso que ele já contratou. Não "
        "contém oferta, preço ou link de compra. Enviada uma vez a cada "
        "extensão, apenas para o usuário afetado."),
    exemplo=["Ana", "7", "12/09/2026"],
    # "Ver tudo" é o único que faz sentido: a mensagem não pede decisão, e
    # o melhor uso do prazo novo é a pessoa reabrir a lista dela.
    botoes=["Ver tudo"],
)

COBRANCA_LINK = Template(
    nome="resolveai_cobranca_link",
    rotulo="Cobra quem pediu o link e não pagou",
    # MARKETING, assumido. Pela régua da Meta não há discussão: falar de
    # pagamento pendente da assinatura é a relação comercial, não um
    # compromisso que a pessoa cadastrou. Tentar passar como utilidade só
    # gastaria uma recusa — foi o que aconteceu com os dois primeiros.
    categoria="MARKETING",
    idioma="pt_BR",
    corpo=("Oi {{1}}, você pediu o link do Resolve AI há *{{2}}* dia(s) e "
           "eu ainda não vi o pagamento entrar.\n\n"
           "Se já pagou, me avisa que eu libero na hora. Se preferir, "
           "posso te mandar o link de novo."),
    variaveis=["primeiro_nome", "dias_desde_o_pedido"],
    justificativa=(
        "Acompanhamento de uma assinatura que o próprio usuário solicitou. "
        "Ele pediu o link de pagamento no assistente e a cobrança ainda não "
        "foi confirmada. A mensagem só é enviada para quem pediu o link, uma "
        "vez por ciclo de cobrança, e sempre por ação manual do "
        "administrador."),
    exemplo=["Ana", "3"],
    # "Já paguei" cai no parser de baixa (`_BAIXA_RE` reconhece "paguei"), e
    # "Assinar" reenvia o link pela resposta que já existe. Nenhum título
    # novo: botão que o bot não entende é pior que botão nenhum.
    botoes=["Já paguei", "Assinar"],
)

REATIVAR_BOAS_VINDAS = Template(
    # SEM o prefixo `resolveai_`: este template foi criado direto no Business
    # Manager pelo Kevin em 05/08/2026, e o nome no catálogo TEM que ser o
    # mesmo aprovado na Meta — é ele que viaja na chamada. Um alias bonito
    # aqui faria todo envio falhar com "template não existe".
    nome="reativar_boas_vindas",
    rotulo="Pede desculpa pelo apagão e ensina a usar (14 dias valendo)",
    # MARKETING, e corretamente: existe pra trazer alguém de volta. Não tenta
    # ser utilidade — e foi por isso que passou de primeira.
    categoria="MARKETING",
    idioma="pt_BR",
    # CORPO EXATO do aprovado, inclusive sem acento em "Voce"/"gratis". O
    # corpo aqui é documentação e base de teste; quem entrega o texto é a
    # Meta. Divergir daria a falsa impressão de que dá pra editar a mensagem
    # sem passar por nova aprovação.
    corpo=("Oi, {{1}}! Aqui e o Resolve AI. 👋\n\n"
           "Voce se cadastrou pra testar e a gente falhou: nosso sistema "
           "ficou fora do ar e voce nao recebeu resposta. Foi erro nosso, e "
           "pedimos desculpa.\n\n"
           "Ja esta tudo funcionando, num numero novo e oficial. E seus 14 "
           "dias gratis estao intactos, valendo a partir de agora.\n\n"
           "Pra comecar, me manda uma coisa que voce nao pode esquecer:\n\n"
           "\"luz 187 vence dia 20\"\n"
           "\"dentista dia 15 as 14h\"\n\n"
           "Eu te aviso antes, sozinho, aqui no Zap. E se nao quiser mais, e "
           "so responder parar que eu nao te incomodo de novo."),
    variaveis=["primeiro_nome"],
    exemplo=["Leonardo"],
    justificativa=(
        "Retomada de contato com usuários que se cadastraram no assistente e "
        "ficaram sem resposta por uma falha de infraestrutura nossa. A "
        "mensagem reconhece a falha, informa que o período de teste que eles "
        "contrataram segue válido, e oferece opt-out explícito já na "
        "primeira interação."),
    # ESTE BOTÃO PRECISOU DE TRATAMENTO NO CÓDIGO. "Quero comecar" não era
    # comando nenhum: o clique cairia no LLM e podia virar "não entendi" — no
    # primeiro contato depois de semanas sumido, que é o pior momento
    # possível pra parecer quebrado.
    botoes=["Quero comecar"],
)

_reg(TRIAL_ESTENDIDO)
_reg(COBRANCA_LINK)
_reg(REATIVAR_BOAS_VINDAS)

KIND_TEMPLATE = {
    "hora": "resolveai_lembrete_hora",
    "trial-estendido": "resolveai_trial_estendido",
    "cobranca-link": "resolveai_cobranca_link",
    # Reativacao pos-apagao. NAO tem checagem no scheduler de proposito: quem
    # decide reabrir conversa com quem esfriou e o dono, no botao de lote do
    # painel. Automatizar isso seria o bot decidindo sozinho reabrir janela
    # com a base inteira — o caminho mais curto pra terceira restricao.
    "reativacao": "reativar_boas_vindas",
    "vencido": "resolveai_item_vencido",
    "resumo": "resolveai_resumo_do_dia",
    "anti-churn": "resolveai_reengajamento_pendentes",
    "winback": "resolveai_reengajamento_pendentes",
    "trial-ending": "resolveai_fim_de_trial_aviso",
    "vencimento": "resolveai_conta_a_vencer",
}

# EXCEÇÕES DECLARADAS: kind que NÃO tem template, de propósito.
#
# Existe pra que "esqueci de mapear" e "decidi não mapear" parem de ter a
# mesma aparência. O teste que varre os kinds do scheduler cobra dos dois
# lados: kind novo sem template e sem estar aqui reprova, porque fora da
# janela ele sumiria calado.
KINDS_SEM_TEMPLATE = {
    # aviso de que um item virou cadáver e foi arquivado: mensagem de
    # arrumação, sem prazo. Fora da janela ela pode esperar a pessoa voltar.
    "arquivado",
    # link de afiliado. Template com link comercial é MARKETING na Meta, e
    # abrir essa porta é abrir o número inteiro pra recusa por categoria.
    "1-click-buy",
    # oferta de marcar o proximo servico (unha, dentista): e conveniencia,
    # nao compromisso com data. Quem esfriou nao precisa receber isso fora da
    # janela, e a Meta classificaria como marketing.
    "retorno",
    # resumo semanal de gastos: agregado, e a Meta classifica agregado como
    # marketing. Ver o comentário no lugar onde o template morava. Dentro da
    # janela de 24h ele sai normalmente, como texto livre.
    "gastos",
} | {
    # nudges do trial guiado: são a coreografia dos primeiros dias, com texto
    # próprio em cada etapa (amostra, primeiro item, oferta do kit...).
    # Achatar doze textos num template só traria de volta os bugs que as
    # etapas existem pra evitar. Fora da janela eles não saem — e o `d6_fim`,
    # que é a única mensagem de conversão, fica guardado em vez de queimado.
    f"trial_d{n}" for n in range(1, 13)
}


def _primeiro_nome(d: dict) -> str:
    return ((d.get("user_nome") or "").split() or ["Oi"])[0]


def _descricao_do_item(item_id) -> str:
    import db
    if not item_id:
        return ""
    try:
        with db.get_conn() as conn:
            r = conn.execute("SELECT descricao FROM items WHERE id=?",
                             (item_id,)).fetchone()
        return (r["descricao"] if r else "") or ""
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[template] falha ao ler descricao do item %s", item_id,
            exc_info=True)
        return ""


def _dia_e_mes(carimbo) -> str:
    """"2026-08-12 09:31:00" -> "12/08". String vazia quando nao da pra ler.

    O vazio e significativo: quem chama usa ele pra NAO enviar (a mensagem
    promete uma data). Devolver "hoje" ou a data de agora seria inventar
    dado sobre a vida da pessoa pra nao deixar a mensagem morrer.
    """
    import datetime as _dt
    import tempo
    texto = str(carimbo or "").strip()
    # Fatiar ANTES de indexar: string de 5 caracteres estourava IndexError
    # em `texto[7]`, e a exceção subia por `para_disparo` até matar o ciclo
    # inteiro do `dispatch_proactive` (mutante sobrevivente da auditoria).
    try:
        d = _dt.date.fromisoformat(texto[:10])
    except (ValueError, TypeError):
        return ""
    # COM O ANO quando for outro ano. Sem isso, item parado desde
    # 03/09/2025 aparecia como "desde 03/09" — uma data que ainda não
    # aconteceu. E o `min()` do reengajamento MAXIMIZA a chance disso,
    # porque escolhe justamente o item mais antigo (auditoria M2.6).
    if d.year == tempo.hoje().year:
        return f"{d.day:02d}/{d.month:02d}"
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _pendentes(user_id):
    import db
    try:
        return db.list_items(user_id, status="pendente")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[template] falha ao listar pendentes do user %s", user_id,
            exc_info=True)
        return []


def para_disparo(d: dict) -> tuple[Optional[str], list]:
    """Do disparo do motor proativo para (nome_do_template, variaveis).

    Um lugar só monta as variáveis de todos os kinds. Espalhar isso pelos
    geradores do scheduler faria cada um divergir do catálogo no seu tempo.

    Devolve (None, []) quando o kind não tem template — e aí, fora da
    janela, a mensagem não sai (por decisão, não por acidente).
    """
    nome = KIND_TEMPLATE.get(d.get("kind") or "")
    if not nome:
        return None, []
    # O alarme tem tres textos diferentes e so um deles e "chegou a hora".
    # Atrasado e escalonamento (M1.5) nao tem template: fora da janela eles
    # nao saem, o que e melhor do que sair dizendo outra coisa.
    # FAIL-CLOSED (auditoria M2.0 rodada 2, P2-5): quem nao DECLARA a
    # variante nao usa template. O default "na_hora" era fail-open — bastava
    # um segundo produtor de kind="hora" esquecer o campo pra sair "Chegou a
    # hora" no lugar de um texto atrasado ou de escalonamento, que e
    # exatamente o que o P1-6 existe pra impedir.
    if d.get("kind") == "hora" and d.get("variante") != "na_hora":
        return None, []
    t = CATALOGO.get(nome)
    if not t:
        # Era o unico retorno mudo desta funcao — e o M2.6 acabou de criar
        # um nome que esteve no catalogo e nao esta mais, que e exatamente
        # como este ramo passa a ser alcancado sem ninguem perceber.
        import logging
        logging.getLogger("resolveai").error(
            "[template] kind mapeado para %r, que nao esta no catalogo — "
            "fora da janela essa mensagem nao sai", nome)
        return None, []

    primeiro = _primeiro_nome(d)
    if nome == "resolveai_lembrete_hora":
        desc = _descricao_do_item(d.get("item_id"))
        variaveis = [desc or "seu compromisso"]
    elif nome == "resolveai_item_vencido":
        desc = _descricao_do_item(d.get("item_id"))
        variaveis = [primeiro, desc or "seu compromisso",
                     d.get("quando") or "recentemente"]
    elif nome == "resolveai_resumo_do_dia":
        pend = _pendentes(d.get("user_id"))
        if not pend:
            # "Voce tem 0 compromissos... o mais proximo e nada por agora" e
            # mensagem sem servico nenhum (auditoria M2.0, P2-10). Sem dado,
            # o certo e o silencio.
            return None, []
        com_data = [i for i in pend if i.get("data_vencimento")] or pend
        variaveis = [primeiro, str(len(pend)), com_data[0]["descricao"]]
    elif nome == "resolveai_reengajamento_pendentes":
        pend = _pendentes(d.get("user_id"))
        if not pend:
            # Sem item pendente, "você tem 0 itens" é mensagem sem serviço —
            # e o que sobra é só o pedido de voltar, que é marketing.
            return None, []
        # O MAIS ANTIGO, e não o primeiro da lista: é o que a pessoa mais
        # deixou parado, e portanto o que mais justifica a mensagem.
        antigo = min(pend, key=lambda i: (i.get("data_criacao") or "9999"))
        desde = _dia_e_mes(antigo.get("data_criacao"))
        if not desde:
            # FAIL-CLOSED, igual ao `conta_a_vencer`: o corpo promete uma
            # data, e template que promete data e entrega vazio é o mesmo
            # defeito de data errada com outro nome.
            import logging
            logging.getLogger("resolveai").error(
                "[template] reengajamento sem data legivel no item %s — "
                "nao envio", antigo.get("id"))
            return None, []
        # `.strip()` antes do `or`: o `or` pega None e "", nao pega
        # "   " — e o collapse de espaco em branco (mais abaixo)
        # transformaria isso num parametro VAZIO, que a Cloud API
        # recusa. A unica mensagem que essa pessoa ia receber morreria
        # na borda, e o CLAUDE.md declara que o caminho degradado
        # grava descricao suja.
        desc = (antigo.get("descricao") or "").strip() or "seu item"
        variaveis = [primeiro, desc, desde]
    elif nome == "resolveai_fim_de_trial_aviso":
        pend = _pendentes(d.get("user_id"))
        variaveis = [primeiro, str(d.get("dias_restantes") or 1),
                     str(len(pend))]
    elif nome == "resolveai_conta_a_vencer":
        # SEM DATA, NAO SAI. O corpo promete "vence em X"; preencher X com
        # "em breve" seria mandar, fora da janela de 24h, a unica mensagem
        # que a pessoa vai receber — e mandar ela sem a informacao que ela
        # existe pra dar. Silencio registrado e melhor que texto oco, e e a
        # mesma regra do template nao aprovado.
        if not d.get("quando"):
            import logging
            logging.getLogger("resolveai").error(
                "[template] conta_a_vencer sem `quando` (item %s) — nao "
                "envio", d.get("item_id"))
            return None, []
        desc = _descricao_do_item(d.get("item_id"))
        variaveis = [primeiro, desc or "sua conta", d["quando"]]
    # (o ramo do `resolveai_resumo_de_gastos` morava aqui e foi REMOVIDO
    # junto com o template, em 26/08/2026. Ele tinha virado código órfão:
    # inalcançável, porque o nome saiu do catálogo — e código órfão é o
    # lugar onde a próxima pessoa procura a regra que já não existe.)
    else:
        # MUDO ATÉ AQUI. Template mapeado em `KIND_TEMPLATE` sem ramo de
        # montagem caía neste `return` sem log nenhum — e fora da janela de
        # 24h ele simplesmente nunca sairia, sem sinal em lugar algum. É o
        # mesmo modo de falha do P0-1, esperando o próximo template novo.
        import logging
        logging.getLogger("resolveai").error(
            "[template] %s esta no KIND_TEMPLATE e nao tem ramo que monte "
            "as variaveis — fora da janela ele nunca sai", nome)
        return None, []

    if len(variaveis) != len(t.variaveis):
        import logging
        logging.getLogger("resolveai").error(
            "[template] %s espera %d variavel(is) e recebeu %d — nao envio",
            nome, len(t.variaveis), len(variaveis))
        return None, []
    # A Cloud API RECUSA parametro com quebra de linha, tab ou 4+ espacos
    # seguidos — e `descricao` vem do texto do usuario (o caminho degradado
    # documentado no CLAUDE.md grava descricao suja). Colapsar espaco em
    # branco aqui e o que impede o template de morrer na borda (P2-9).
    return nome, [" ".join(str(v).split())[:200] for v in variaveis]
