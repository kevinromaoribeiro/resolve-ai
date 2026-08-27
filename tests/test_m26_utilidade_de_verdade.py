# -*- coding: utf-8 -*-
"""M2.6 — o que a Meta chama de UTILIDADE, e o que ela chama de marketing.

Descoberto submetendo os sete na mao (25/08/2026): a Meta nao separa pelo
tom do texto, separa pelo MOTIVO da mensagem.

  passa como utilidade  ->  fala de UM item que a pessoa cadastrou, com data
  vira marketing        ->  fala de VOLTAR a usar ("voce tem 2 pendencias")

Os dois recusados — reengajamento e resumo de gastos — nao estavam presos a
nenhum compromisso com data. E ela classificou certo: "voce tem 2 itens
pendentes" e uma mensagem sobre o PRODUTO; "sua conta de luz esta parada
desde 12/08" e uma mensagem sobre a vida da pessoa. A segunda e a que faz
alguem pagar R$ 19,90.
"""
from datetime import timedelta

import pytest

import db
import scheduler
import templates
import tempo


def test_reengajamento_fala_de_um_item_com_data(usuario):
    """O corpo tem que citar o item e QUANDO — sem isso ele volta a ser um
    aviso sobre o produto, e a Meta recusa como utilidade."""
    t = templates.CATALOGO["resolveai_reengajamento_pendentes"]
    corpo = t.corpo.lower()
    assert "quantidade" not in t.variaveis
    assert "descricao" in t.variaveis
    assert "desde" in t.variaveis or "quando" in t.variaveis
    # nada de contagem no corpo
    assert "item(ns)" not in corpo and "pendente(s)" not in corpo


def test_reengajamento_monta_com_o_item_parado_ha_mais_tempo(usuario):
    """O reengajamento fala do item ESQUECIDO, e nao do mais urgente.

    `list_items` ordena por `COALESCE(data_vencimento, data_criacao)`, ou
    seja, pelo vencimento mais proximo. Entao `pend[0]` e o mais URGENTE e
    o `min(data_criacao)` e o mais ANTIGO — e as duas coisas so divergem
    num arranjo: um item cadastrado hoje que vence amanha, e um lembrete
    velho parado sem data.

    Esse arranjo e o teste, porque e ele que separa as tres mensagens do
    produto. Conta que vence amanha ja e coberta pelo `conta_a_vencer`;
    conta vencida, pelo `item_vencido`. O reengajamento existe pro que
    ficou parado — e citar a conta urgente aqui seria a terceira mensagem
    sobre o mesmo boleto, que e como o usuario silencia o bot.
    """
    parado = db.add_item(user_id=usuario["id"], tipo="lembrete",
                         categoria="Casa", descricao="trocar a lampada",
                         status="pendente")
    antigo_em = (tempo.agora() - timedelta(days=13)).strftime(
        "%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET data_criacao=? WHERE id=?",
                     (antigo_em, parado))
    # Cadastrada ha 2 dias (a pessoa mandou o boleto ja atrasado), com
    # vencimento anterior a criacao da lampada: e ela que encabeca a lista,
    # porque o ORDER BY usa o vencimento quando ele existe.
    urgente = db.add_item(
        user_id=usuario["id"], tipo="despesa", categoria="Contas",
        descricao="conta de luz", valor_reais=187.0,
        data_vencimento=(tempo.hoje() - timedelta(days=16)).isoformat(),
        status="pendente")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET data_criacao=? WHERE id=?",
                     ((tempo.agora() - timedelta(days=2)).strftime(
                         "%Y-%m-%d %H:%M:%S"), urgente))

    pend = db.list_items(usuario["id"], status="pendente")
    assert pend[0]["descricao"] == "conta de luz", (
        "o arranjo nao discrimina mais: revise antes de confiar neste teste")

    nome, variaveis = templates.para_disparo(
        {"kind": "anti-churn", "user_id": usuario["id"],
         "user_nome": "Kevin Santos"})
    assert nome == "resolveai_reengajamento_pendentes"
    assert variaveis[1] == "trocar a lampada", variaveis
    assert variaveis[2] == antigo_em[8:10] + "/" + antigo_em[5:7], variaveis


def test_reengajamento_sem_data_legivel_nao_sai(usuario):
    """FAIL-CLOSED, igual ao `conta_a_vencer`: o corpo promete uma data, e
    template que promete data e entrega vazio e o mesmo defeito de data
    errada com outro nome."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Casa", descricao="algo", status="pendente")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET data_criacao='' WHERE id=?", (iid,))
    nome, _ = templates.para_disparo(
        {"kind": "anti-churn", "user_id": usuario["id"],
         "user_nome": "Kevin"})
    assert nome is None


def test_resumo_de_gastos_saiu_do_catalogo():
    """Um resumo semanal e um AGREGADO. Nao existe versao dele que fale de
    um item so sem deixar de ser um resumo — entao ele nao tem template, e
    isso e decisao declarada, nao esquecimento."""
    assert "resolveai_resumo_de_gastos" not in templates.CATALOGO
    assert "gastos" not in templates.KIND_TEMPLATE
    assert "gastos" in templates.KINDS_SEM_TEMPLATE


def test_resumo_de_gastos_nao_sai_fora_da_janela(usuario):
    """A consequencia, dita em teste: sem template, fora da janela de 24h o
    resumo nao sai — e isso e o comportamento correto, nao uma falha."""
    nome, variaveis = templates.para_disparo(
        {"kind": "gastos", "user_id": usuario["id"], "user_nome": "Kevin"})
    assert nome is None
    assert variaveis == []


def test_o_resumo_de_gastos_continua_saindo_dentro_da_janela(usuario,
                                                             monkeypatch):
    """E a outra metade: quem falou com o bot nas ultimas 24h continua
    recebendo normalmente, porque ai vale texto livre."""
    import canal
    enviados = []
    monkeypatch.setattr(canal, "send_text",
                        lambda tel, txt, *a, **kw: enviados.append(txt) or True)
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    res = canal.falar(usuario["telefone"], "resumo de gastos aqui",
                      user_id=usuario["id"])
    assert res["enviado"] is True
    assert res["via"] == "texto"
    assert enviados


# O QUE A META DE FATO RESPONDEU, template por template.
#
# Substituiu uma heuristica minha que tentava DEDUZIR a regua ("cita item ou
# cita data"). A auditoria mostrou que ela aprovava exatamente o conjunto que
# a Meta recusou — `["primeiro_nome","quantidade","mais_antigo"]` passava pelo
# ramo do "mais_antigo". Teste que da falsa seguranca sobre um criterio
# externo e pior que teste nenhum: ele faz a gente parar de perguntar.
#
# Entao aqui nao ha deducao. Ha REGISTRO: o que foi submetido, e o que ela
# disse. Template novo entra sem veredito e o teste cobra que alguem submeta
# e anote o resultado.
VEREDITO_DA_META = {
    "resolveai_lembrete_hora": "em_analise",
    "resolveai_item_vencido": "em_analise",
    "resolveai_resumo_do_dia": "em_analise",
    "resolveai_conta_a_vencer": "em_analise",
    # recusado como utilidade em 25/08 na versao que contava pendencias;
    # reescrito em 26/08 pra citar o item parado + a data. Nao resubmetido
    # ainda — o veredito da versao nova ainda nao existe.
    "resolveai_reengajamento_pendentes": "reescrito_aguardando",
    # recusado como utilidade em 27/08 ("sera rejeitado", recomendando
    # MARKETING) — e a Meta esta certa: fala da relacao comercial, nao de um
    # compromisso da pessoa. Decisao do dono: submeter como MARKETING.
    "resolveai_fim_de_trial_aviso": "marketing_por_decisao",
}


@pytest.mark.parametrize("nome", sorted(templates.CATALOGO))
def test_todo_template_tem_veredito_registrado(nome):
    """Template novo sem veredito reprova aqui.

    Nao e burocracia: o custo de descobrir a regua da Meta e uma rodada de
    submissao com dias de espera, e essa informacao se perde se ficar so na
    cabeca de quem submeteu.
    """
    assert nome in VEREDITO_DA_META, (
        f"{nome} nao tem veredito da Meta registrado. Submeta, anote o que "
        f"ela respondeu, e so entao ligue o template.")


def test_o_que_a_meta_recusou_nao_volta_disfarcado():
    """A UNICA coisa que da pra afirmar sobre a regua, com evidencia: o
    conjunto exato que ela recusou nao pode voltar."""
    recusado = ["primeiro_nome", "quantidade", "mais_antigo"]
    for nome, t in templates.CATALOGO.items():
        assert list(t.variaveis) != recusado, (
            f"{nome} voltou a ter as variaveis que a Meta recusou em 25/08")


# ---------------------------------------------------------------------------
# CONSERTOS DA AUDITORIA M2.6
# ---------------------------------------------------------------------------
def test_reengajamento_nao_manda_responder_feito():
    """P0 da auditoria: o corpo reescrito passou a mandar *feito*, e o
    disparo de reengajamento tem `item_id: None` POR CONSTRUCAO
    (`scheduler.py`, anti-churn e winback). Quem resolve `feito` sozinho e
    o `_alvo_da_baixa`, que so enxerga disparos com item_id — entao o item
    citado no template e inalcançavel, e o `feito` fecha o item do ULTIMO
    alarme. A pessoa le "trocar a lampada" e o bot responde "dei baixa em
    conta de luz".

    Regra do projeto, escrita no proprio comentario do template: prometer
    no corpo so o que o Python garante.
    """
    corpo = templates.CATALOGO["resolveai_reengajamento_pendentes"].corpo
    baixo = corpo.lower()
    assert "*feito*" not in baixo, corpo
    assert "*adiar*" not in baixo, corpo
    assert "ver tudo" in baixo, corpo


def test_so_promete_feito_quem_manda_item_id(usuario):
    """A regra geral, e nao o caso: template que manda responder *feito* so
    pode ser usado por kind que carrega `item_id`. Sem isso, a baixa cai no
    item errado — e isso e perda de dado (regra 10)."""
    import scheduler
    # LISTA A MAO, e o auditor tem razao em chamar de fragil. A trava
    # possivel e barata: todo nome aqui tem que existir de verdade no
    # inventario de kinds do motor. Kind renomeado quebra este teste em vez
    # de sair da cobertura em silencio.
    sem_item = {"anti-churn", "winback", "resumo", "gastos", "trial-ending"}
    fantasmas = sem_item - scheduler.KINDS_PROATIVOS
    assert not fantasmas, (
        f"kind que nao existe mais no motor: {sorted(fantasmas)}")
    for kind, nome in templates.KIND_TEMPLATE.items():
        corpo = templates.CATALOGO[nome].corpo.lower()
        if kind in sem_item:
            assert "*feito*" not in corpo, (
                f"kind {kind!r} nao manda item_id, mas o template {nome} "
                f"manda responder *feito* — a baixa vai cair no item errado")


@pytest.mark.parametrize("carimbo,esperado", [
    ("2026-08-12 09:31:00", "12/08"),
    ("2026-08-12T09:31:00", "12/08"),
    # ANO DIFERENTE MOSTRA O ANO. Sem isso, item de 03/09/2025 aparecia como
    # "desde 03/09" — uma data que ainda nao aconteceu. E o `min()` do
    # reengajamento MAXIMIZA a chance disso: ele escolhe justamente o item
    # mais antigo (P1 da auditoria M2.6).
    ("2025-09-03 10:00:00", "03/09/2025"),
    ("2024-01-05 10:00:00", "05/01/2024"),
    # ilegiveis: string vazia, e quem chama nao envia
    ("", ""),
    (None, ""),
    ("2026-08", ""),
    ("abcd-ef-ghij", ""),
    ("xx", ""),
    ("2026/08/12", ""),
])
def test_dia_e_mes_le_ou_recusa(carimbo, esperado):
    assert templates._dia_e_mes(carimbo) == esperado


def test_data_de_outro_ano_nao_parece_futuro(usuario):
    """O caso do P1, fim a fim: item parado desde setembro do ano passado."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Casa", descricao="revisar o seguro",
                      status="pendente")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET data_criacao=? WHERE id=?",
                     ("2025-09-03 10:00:00", iid))
    _, variaveis = templates.para_disparo(
        {"kind": "anti-churn", "user_id": usuario["id"],
         "user_nome": "Kevin"})
    assert variaveis[2] == "03/09/2025", variaveis


def test_descricao_so_com_espaco_nao_vira_parametro_vazio(usuario):
    """Parametro vazio e recusa da Cloud API — e ai a UNICA mensagem que
    essa pessoa ia receber morre na borda. O `or` pegava None e "", nao
    pegava "   " (o CLAUDE.md declara que o caminho degradado grava
    descricao suja)."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Casa", descricao="x", status="pendente")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET descricao='   ' WHERE id=?", (iid,))
    _, variaveis = templates.para_disparo(
        {"kind": "anti-churn", "user_id": usuario["id"],
         "user_nome": "Kevin"})
    assert variaveis[1].strip(), f"parametro vazio: {variaveis}"


def test_item_sem_data_nao_e_escolhido_na_frente_de_um_datavel(usuario):
    """O default `or "9999"` do `min()` e load-bearing: ele empurra o item
    sem data pro FIM. Invertido, o item sem data vira o escolhido, o
    fail-closed dispara, e o reengajamento some pra quem tinha item
    perfeitamente datavel."""
    bom = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Casa", descricao="item com data",
                      status="pendente")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET data_criacao=? WHERE id=?",
                     ("2026-08-12 09:00:00", bom))
    ruim = db.add_item(user_id=usuario["id"], tipo="lembrete",
                       categoria="Casa", descricao="item sem data",
                       status="pendente")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET data_criacao='' WHERE id=?", (ruim,))
    nome, variaveis = templates.para_disparo(
        {"kind": "anti-churn", "user_id": usuario["id"],
         "user_nome": "Kevin"})
    assert nome is not None, "o item sem data engoliu o reengajamento"
    assert variaveis[1] == "item com data", variaveis


# ---------------------------------------------------------------------------
# O AVISO DE FIM DE TRIAL E MARKETING, POR DECISAO DO DONO (27/08/2026)
# ---------------------------------------------------------------------------
def test_fim_de_trial_e_marketing_declarado():
    """A Meta recusou como utilidade, e ela esta certa.

    "Seu periodo de teste termina em 2 dias" nao fala de um compromisso que
    a pessoa cadastrou — fala da relacao COMERCIAL dela com o produto. Nao
    existe reescrita que mude isso sem mudar a mensagem.

    Decisao do dono: submeter como MARKETING. O raciocinio dele:
    e literalmente uma mensagem tentando fechar a assinatura, e sai UMA vez
    por usuario na vida inteira do trial — entao o custo por mensagem e a
    cota de marketing sao irrisorios diante do que ela tenta converter.

    O teste existe pra que a categoria no repo nao divirja da categoria na
    Meta. Divergencia ai e o pior tipo: o template e aprovado numa categoria
    e o envio pede outra, e a recusa so aparece em producao.
    """
    t = templates.CATALOGO["resolveai_fim_de_trial_aviso"]
    assert t.categoria == "MARKETING", (
        "a Meta tem este template como MARKETING; o repo precisa concordar")


def test_so_o_fim_de_trial_e_marketing():
    """Marketing e excecao pontual, nao porta aberta. Todo o resto e
    utilidade — e template de utilidade e o que sustenta a promessa do
    produto (lembrar), nao a cobranca."""
    marketing = {n for n, t in templates.CATALOGO.items()
                 if t.categoria == "MARKETING"}
    assert marketing == {"resolveai_fim_de_trial_aviso"}, marketing
