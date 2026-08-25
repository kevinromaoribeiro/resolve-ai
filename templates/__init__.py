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
    categoria="UTILITY",
    idioma="pt_BR",
    # AUDITORIA M2.0 (P1-7): o corpo mandava responder "*feito* + o nome", e
    # o nome que ele mostra e a descricao do item — que falha quando tem mais
    # de 4 palavras ("feito trocar o oleo do carro" nao dava baixa e ainda
    # criava item duplicado). O corpo do template e contrato com a Meta:
    # prometer nele so o que o Python garante.
    corpo=("Oi {{1}}, você tem *{{2}}* item(ns) pendente(s) guardado(s) "
           "aqui.\n\n"
           "O mais antigo é *{{3}}*.\n\n"
           "Responda *ver tudo* para revisar a lista."),
    variaveis=["primeiro_nome", "quantidade", "mais_antigo"],
    exemplo=["Kevin", "2", "trocar o óleo do carro"],
    justificativa=(
        "Aviso de que existem compromissos pendentes cadastrados pelo "
        "próprio usuário e ainda não resolvidos. O conteúdo é o dado dele — "
        "quantidade e nome do item mais antigo — e a ação oferecida é "
        "revisar ou encerrar esses itens."),
))

_reg(Template(
    nome="resolveai_fim_de_trial_aviso",
    categoria="UTILITY",
    idioma="pt_BR",
    corpo=("Oi {{1}}, seu período de teste termina em *{{2}}* dia(s).\n\n"
           "Seus *{{3}}* item(ns) e lembretes continuam guardados. "
           "Se precisar de qualquer coisa, é só responder aqui."),
    variaveis=["primeiro_nome", "dias", "quantidade_itens"],
    exemplo=["Kevin", "2", "7"],
    justificativa=(
        "Aviso factual sobre o fim do período de teste da conta e sobre a "
        "preservação dos dados do usuário. Não contém oferta, preço nem "
        "chamada de compra."),
))


# --- M2.5: os dois que faltavam --------------------------------------------

_reg(Template(
    nome="resolveai_conta_a_vencer",
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

_reg(Template(
    nome="resolveai_resumo_de_gastos",
    categoria="UTILITY",
    idioma="pt_BR",
    # O CORPO NAO TERMINA EM VARIAVEL de proposito: a Meta reprova template
    # que comeca ou termina com parametro. O convite ({{5}}) fica no meio,
    # com a instrucao fixa fechando a mensagem.
    corpo=("Oi {{1}}, o resumo da sua semana: *{{2}}* em contas registradas.\n\n"
           "Onde mais pesou: *{{3}}*.\n"
           "{{4}}\n\n"
           # O "💡" entre {{4}} e {{5}} NÃO é enfeite: sem nenhum caractere
           # entre eles, a Meta lê como parâmetros adjacentes e reprova a
           # submissão inteira. Achado pelo teste que a auditoria M2.5 pediu.
           "💡 {{5}}\n\n"
           "Responda *ver tudo* para a lista completa."),
    variaveis=["primeiro_nome", "total", "categoria_top", "comparacao",
               "convite"],
    exemplo=["Kevin", "R$ 342,90", "Contas", "Na semana passada foram "
             "R$ 280,00.", "Chegou boleto? Me manda a foto que eu guardo a "
             "data."],
    justificativa=(
        "Resumo semanal das despesas que o próprio usuário registrou no "
        "assistente, no dia da semana definido no cadastro. O conteúdo é "
        "exclusivamente o dado dele — total, categoria e comparação com a "
        "semana anterior. Não há oferta, preço de plano nem divulgação."),
))


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
KIND_TEMPLATE = {
    "hora": "resolveai_lembrete_hora",
    "vencido": "resolveai_item_vencido",
    "resumo": "resolveai_resumo_do_dia",
    "anti-churn": "resolveai_reengajamento_pendentes",
    "winback": "resolveai_reengajamento_pendentes",
    "trial-ending": "resolveai_fim_de_trial_aviso",
    "vencimento": "resolveai_conta_a_vencer",
    "gastos": "resolveai_resumo_de_gastos",
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
        antigo = pend[0]["descricao"] if pend else ""
        if not pend:
            # Sem item pendente, "você tem 0 itens" é mensagem sem serviço —
            # e o que sobra é só o pedido de voltar, que é marketing.
            return None, []
        variaveis = [primeiro, str(len(pend)), antigo]
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
    elif nome == "resolveai_resumo_de_gastos":
        # O template REMONTA a partir do dado, e nao fatia o texto livre.
        # Recortar a mensagem pronta em cinco pedacos funcionaria ate o dia
        # em que alguem mexesse numa virgula do texto — e ai sairia template
        # truncado, que a Meta aceita e a pessoa le como defeito.
        import db as _db
        import scheduler as _sched
        try:
            g = _db.gastos_da_semana(d["user_id"])
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[template] falha ao somar gastos do user %s",
                d.get("user_id"), exc_info=True)
            return None, []
        if g["total"] <= 0 or not g["por_categoria"]:
            return None, []          # sem dado nao ha resumo (nem template)
        anterior = g["total_anterior"]
        comp = (f"Na semana passada foram {_sched._brl(anterior)}."
                if anterior > 0
                else "E a primeira semana que eu tenho pra comparar.")
        variaveis = [primeiro, _sched._brl(g["total"]),
                     next(iter(g["por_categoria"])), comp,
                     _sched.convite_de_uso(d["user_id"],
                                           d.get("semana") or 0)]
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
