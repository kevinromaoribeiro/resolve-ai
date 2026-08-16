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
# OS CINCO
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
    corpo=("{{1}}, *{{2}}* venceu {{3}} e eu não registrei a baixa.\n\n"
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
    corpo=("{{1}}, você tem *{{2}}* compromisso(s) guardado(s) para os "
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
    corpo=("{{1}}, você tem *{{2}}* item(ns) pendente(s) guardado(s) "
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
    corpo=("{{1}}, seu período de teste termina em *{{2}}* dia(s).\n\n"
           "Seus *{{3}}* item(ns) e lembretes continuam guardados. "
           "Se precisar de qualquer coisa, é só responder aqui."),
    variaveis=["primeiro_nome", "dias", "quantidade_itens"],
    exemplo=["Kevin", "2", "7"],
    justificativa=(
        "Aviso factual sobre o fim do período de teste da conta e sobre a "
        "preservação dos dados do usuário. Não contém oferta, preço nem "
        "chamada de compra."),
))


# ---------------------------------------------------------------------------
# KIND (do motor proativo) -> TEMPLATE
# ---------------------------------------------------------------------------
# Kind que não está aqui NÃO tem template: fora da janela ele não sai, e isso
# é registrado. Inventar um template parecido pra "aproveitar" seria mandar
# texto que a Meta aprovou pra outra finalidade.
# AUDITORIA M2.0 (P1-6): "vencimento" foi REMOVIDO daqui. O texto livre dele
# é o aviso D-3/D-1 ("vence em 20/08"), e o template diria "Chegou a hora" —
# urgência falsa três dias antes, sem data e sem valor. Kind sem template não
# sai fora da janela, e isso é melhor do que sair mentindo.
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
    else:
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
