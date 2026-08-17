"""M2.3 — heatmap de constância + gastos por categoria.

O que este bloco responde, e que nenhum número solto do painel responde:
"as pessoas estão usando com REGULARIDADE, ou usaram muito num dia e
sumiram?" — porque produto de hábito que não vira hábito não retém, e o
`por_pessoa_dia` de 7 dias esconde exatamente essa diferença.

Invariantes que os testes travam:
  - dia sem uso é ZERO, não buraco: série com furo desenha um heatmap que
    mente sobre a constância (dez dias de uso viram "dez dias seguidos");
  - sem dado nenhum devolve estrutura vazia, nunca divisão por zero;
  - o dono não entra na conta de engajamento (mesma regra do `engajamento`).
"""
import datetime as _dt

import pytest

import db
import tempo
from conftest import TELEFONE


def _msg(uid, quando, telefone=TELEFONE):
    db.log_message(uid, telefone, "in", "texto", "oi")
    with db.get_conn() as conn:
        conn.execute("UPDATE msg_log SET ts=? WHERE id=(SELECT MAX(id) "
                     "FROM msg_log)", (quando.strftime("%Y-%m-%d %H:%M:%S"),))


# --- heatmap -------------------------------------------------------------

def test_serie_tem_um_ponto_por_dia_sem_buraco(usuario):
    """Dia sem uso e ZERO, nao ausencia. Serie com furo desenha um heatmap
    que mente: dez dias esparsos viram dez quadrados seguidos."""
    hoje = tempo.hoje()
    _msg(usuario["id"], tempo.agora())
    _msg(usuario["id"], tempo.agora() - _dt.timedelta(days=5))

    serie = db.heatmap_constancia(dias=14)

    assert len(serie) == 14, f"{len(serie)} pontos para 14 dias"
    datas = [p["data"] for p in serie]
    assert datas == sorted(datas), "serie fora de ordem"
    esperadas = [(hoje - _dt.timedelta(days=i)).isoformat()
                 for i in range(13, -1, -1)]
    assert datas == esperadas, "faltou dia na serie"
    assert all(isinstance(p["n"], int) for p in serie)


def test_dia_sem_uso_e_zero(usuario):
    _msg(usuario["id"], tempo.agora())
    serie = db.heatmap_constancia(dias=7)
    ontem = (tempo.hoje() - _dt.timedelta(days=1)).isoformat()
    ponto = [p for p in serie if p["data"] == ontem][0]
    assert ponto["n"] == 0


def test_banco_vazio_nao_estoura():
    serie = db.heatmap_constancia(dias=30)
    assert len(serie) == 30
    assert all(p["n"] == 0 for p in serie)


def test_conta_mensagens_do_dia(usuario):
    for _ in range(3):
        _msg(usuario["id"], tempo.agora())
    hoje = tempo.hoje().isoformat()
    ponto = [p for p in db.heatmap_constancia(dias=7)
             if p["data"] == hoje][0]
    assert ponto["n"] == 3


def test_dono_nao_entra_na_conta(usuario):
    """Mesma regra do `engajamento`: o dono testa o dia inteiro e infla a
    metrica exatamente na direcao em que a gente quer acreditar."""
    _msg(usuario["id"], tempo.agora())
    _msg(None, tempo.agora(), telefone="5511900000000")   # ADMIN_PHONE
    hoje = tempo.hoje().isoformat()
    ponto = [p for p in db.heatmap_constancia(
        dias=7, excluir_telefones=["5511900000000"]) if p["data"] == hoje][0]
    assert ponto["n"] == 1, f"contou o dono: {ponto}"


@pytest.mark.parametrize("gravado,configurado", [
    # A Meta devolve o wa_id brasileiro SEM o 9o digito (documentado em
    # meta_cloud.py), e msg_log guarda esse wa_id. Comparar digito a digito
    # fazia a exclusao do dono nao casar: ele mandava 40 e contavam 40.
    #
    # A transformacao REAL e tirar o 9 depois do DDD:
    #   com 9  -> 55 11 9 8765-4321  (13 digitos)
    #   sem 9  -> 55 11   8765-4321  (12 digitos)
    # Os 8 ultimos digitos sao iguais nos dois — e e por isso que o corte e
    # em 8, nao em 9.
    ("551187654321", "5511987654321"),
    ("5511987654321", "551187654321"),
    ("+55 (11) 98765-4321", "5511987654321"),
])
def test_dono_excluido_mesmo_com_9o_digito_divergente(usuario, gravado,
                                                      configurado):
    _msg(None, tempo.agora(), telefone=gravado)
    r = db.constancia(dias=7, excluir_telefones=[configurado])
    assert r["total"] == 0, f"{gravado} x {configurado}: {r}"


def test_resgate_do_painel_nao_conta_como_uso(usuario):
    """`resgate_painel` e o DONO digitando pela pessoa. `dentro_da_janela`
    ja exclui esse tipo, com o motivo escrito no db.py — o heatmap inflava
    10x na direcao em que a gente quer acreditar."""
    db.log_message(None, TELEFONE, "in", "resgate_painel", "escrito pelo dono")
    _msg(usuario["id"], tempo.agora())
    assert db.constancia(dias=7)["total"] == 1


def test_saida_do_bot_nao_conta(usuario):
    """Heatmap de USO e o que a pessoa faz, nao o que o bot manda."""
    db.log_message(usuario["id"], TELEFONE, "out", "texto", "lembrete")
    hoje = tempo.hoje().isoformat()
    ponto = [p for p in db.heatmap_constancia(dias=7)
             if p["data"] == hoje][0]
    assert ponto["n"] == 0


@pytest.mark.parametrize("dias", [0, -5])
def test_janela_invalida_nao_estoura(dias):
    assert db.heatmap_constancia(dias=dias) == []


# --- constância: o número que o heatmap resume ---------------------------

def test_constancia_conta_dias_distintos(usuario):
    """Dez mensagens num dia so nao e constancia; e um pico."""
    for _ in range(10):
        _msg(usuario["id"], tempo.agora())
    r = db.constancia(dias=30)
    assert r["dias_com_uso"] == 1, r
    assert r["total"] == 10, r


def test_constancia_sem_uso_nao_divide_por_zero():
    r = db.constancia(dias=30)
    assert r["dias_com_uso"] == 0 and r["media_por_dia_ativo"] == 0.0


def test_media_e_por_dia_ATIVO(usuario):
    """Media sobre dias ATIVOS, nao sobre a janela: dividir por 30 dilui e
    esconde quem usa muito em poucos dias."""
    _msg(usuario["id"], tempo.agora())
    _msg(usuario["id"], tempo.agora())
    _msg(usuario["id"], tempo.agora() - _dt.timedelta(days=3))
    r = db.constancia(dias=30)
    assert r["dias_com_uso"] == 2
    assert r["media_por_dia_ativo"] == 1.5, r


# --- gastos por categoria ------------------------------------------------

def test_gastos_por_categoria_soma_certo(usuario):
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=100.0, status="concluido")
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="agua", valor_reais=50.0, status="concluido")
    db.add_item(user_id=usuario["id"], tipo="despesa",
                categoria="Alimentação", descricao="mercado",
                valor_reais=200.0, status="concluido")
    g = db.gastos_por_categoria(usuario["id"])
    assert g["Contas"] == 150.0 and g["Alimentação"] == 200.0


def test_gastos_ignora_item_sem_valor(usuario):
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="sem valor", status="concluido")
    assert db.gastos_por_categoria(usuario["id"]) == {}


def test_gastos_vem_ordenado_do_maior(usuario):
    # Categorias em ordem ALFABETICA INVERSA ao valor: sem isso o teste
    # passa por coincidencia (a ordem do GROUP BY ja seria alfabetica).
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Casa",
                descricao="pequeno", valor_reais=10.0, status="concluido")
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Viagem",
                descricao="grande", valor_reais=900.0, status="concluido")
    assert list(db.gastos_por_categoria(usuario["id"])) == ["Viagem", "Casa"]


def test_gastos_sem_nada_devolve_vazio(usuario):
    assert db.gastos_por_categoria(usuario["id"]) == {}


def test_lembrete_nao_entra_no_gasto(usuario):
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Saúde",
                descricao="dentista", valor_reais=300.0, status="pendente")
    assert db.gastos_por_categoria(usuario["id"]) == {}


# --- engajamento: o numero principal do negocio --------------------------
#
# Estes testes existem porque a reescrita inteira do `engajamento` podia ser
# REVERTIDA sem quebrar um unico teste. Nenhum teste da suite chamava
# `db.engajamento` e olhava `por_pessoa_dia` — os que existiam montavam o
# dict a mao e testavam so a formatacao da linha.

def _entrada_como_a_producao_grava(telefone, tipo="texto"):
    """O webhook grava `db.log_message(None, num, "in", ...)` — SEM user_id.
    Foi exatamente essa diferenca que deixou a metrica morta por meses."""
    db.log_message(None, telefone, "in", tipo, "oi")


def test_engajamento_conta_quem_o_webhook_gravou(usuario):
    """Com `m.user_id IS NOT NULL`, isto dava 0.0 SEMPRE — o painel dizia
    'nao virou habito' com qualquer volume de uso."""
    for _ in range(14):
        _entrada_como_a_producao_grava(TELEFONE)
    r = db.engajamento()
    assert r["pessoas"] == 1, r
    assert r["por_pessoa_dia"] == 2.0, r
    assert "hábito" in r["veredito"]


def test_engajamento_ignora_resgate_do_painel(usuario):
    _entrada_como_a_producao_grava(TELEFONE)
    for _ in range(9):
        _entrada_como_a_producao_grava(TELEFONE, tipo="resgate_painel")
    assert db.engajamento()["despejos_7d"] == 1


def test_desconhecido_nao_entra_em_pessoas(usuario):
    """Numero fora da base (engano, spam, cadastro incompleto) nao pode
    mover a metrica: com 11 usuarios, dois enganos derrubariam ela pela
    metade, e numero que estranho move nao decide nada."""
    # DDD 99 e sufixos improvaveis: o banco de teste acumula usuarios de
    # outros arquivos, e um "desconhecido" que por acaso ja esta na base
    # testaria o contrario do que o nome diz.
    conhecidos_antes = db.engajamento()["pessoas"]
    for _ in range(4):
        _entrada_como_a_producao_grava(TELEFONE)
    _entrada_como_a_producao_grava("5599312450001")
    _entrada_como_a_producao_grava("5599312450002")
    r = db.engajamento()
    assert r["pessoas"] == conhecidos_antes + 1, r
    assert r["desconhecidos_7d"] == 2, r


def test_top_nao_fica_vazio(usuario):
    """`top` fazia JOIN por user_id — sempre NULL nas linhas de entrada.
    O painel mostraria '1.5 demandas/pessoa/dia' com 'quem mais usa'
    VAZIO, e e olhando pra isso que o dono decide se o produto pegou."""
    for _ in range(3):
        _entrada_como_a_producao_grava(TELEFONE)
    top = db.engajamento()["top"]
    assert top and top[0]["n"] == 3, top
    assert top[0]["nome"] == "Kevin", top


def test_dono_excluido_diz_a_verdade(usuario):
    """A exclusao virou por TELEFONE, mas a flag saia de `ids_fora` (tabela
    users). O dono nao tem linha em users: a copy dizia o contrario do que
    o codigo fazia."""
    for _ in range(30):
        _entrada_como_a_producao_grava("5511900000000")
    _entrada_como_a_producao_grava(TELEFONE)
    r = db.engajamento(excluir_telefones=["5511900000000"])
    assert r["dono_excluido"] is True, r
    assert r["despejos_7d"] == 1, r


# --- o painel: o desenho nao pode mentir nem quebrar ---------------------

def test_painel_devolve_heatmap_e_gastos(usuario):
    """O NOME deste teste mentia: ele falava em gastos e nao assertava nada
    sobre gastos — e `gastos_por_categoria` estava escrita, testada e
    chamada por ninguem. Metade do escopo do M2.3 nao existia no painel."""
    import wa_bot
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=120.0, status="concluido")
    dados = wa_bot._dados_do_painel()
    assert "heatmap" in dados and "constancia" in dados
    assert len(dados["heatmap"]) >= 28
    assert dados["gastos"].get("Contas") == 120.0, dados["gastos"]


def test_painel_soma_gastos_de_toda_a_base(usuario):
    import wa_bot
    outro = db.create_user(nome="Ana", telefone="5511911112222")
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=100.0, status="concluido")
    db.add_item(user_id=outro, tipo="despesa", categoria="Contas",
                descricao="agua", valor_reais=50.0, status="concluido")
    assert wa_bot._dados_do_painel()["gastos"]["Contas"] == 150.0


def test_painel_exclui_o_dono_do_heatmap(usuario, monkeypatch):
    """Mutacao que sobreviveu na auditoria: `fora = []` no _dados_do_painel
    passava sem quebrar nada."""
    import wa_bot
    db.log_message(None, wa_bot.ADMIN_PHONE, "in", "texto", "teste do dono")
    dados = wa_bot._dados_do_painel()
    assert dados["constancia"]["total"] == 0, (
        f"contou a mensagem do dono: {dados['constancia']}")


def test_painel_nao_varre_a_tabela_duas_vezes(usuario, monkeypatch):
    """`constancia` recalculava a serie inteira: duas varreduras por
    request, a cada 20 segundos."""
    import wa_bot
    chamadas = []
    real = db.heatmap_constancia
    monkeypatch.setattr(db, "heatmap_constancia",
                        lambda *a, **kw: chamadas.append(1) or real(*a, **kw))
    wa_bot._dados_do_painel()
    assert len(chamadas) == 1, f"{len(chamadas)} varreduras do msg_log"


def test_painel_com_banco_vazio_nao_quebra():
    import wa_bot
    dados = wa_bot._dados_do_painel()
    assert dados["constancia"]["dias_com_uso"] == 0
    assert all(p["n"] == 0 for p in dados["heatmap"])


# --- P1-1 e P1-2 da rodada 3: a falha do gastos tem que ser DITA ---------
#
# O conserto do "soma parcial rotulada como total" saiu sem teste nenhum:
# as duas metades (try por usuario, campos gastos_falharam/gastos_base)
# podiam ser revertidas com a suite inteira verde. E o pior caso — TODAS as
# somas falharem — era o unico que continuava mudo, porque o card so era
# desenhado quando havia gasto.

def _quebra_gastos_de(monkeypatch, quais):
    """Faz `gastos_por_categoria` levantar para alguns user_ids."""
    real = db.gastos_por_categoria

    def _falso(user_id, meses=3):
        if user_id in quais:
            raise RuntimeError("falha simulada")
        return real(user_id, meses)
    monkeypatch.setattr(db, "gastos_por_categoria", _falso)


def test_falha_parcial_e_contada_e_nao_derruba_o_resto(usuario, monkeypatch):
    import wa_bot
    # DELTA, nao valor absoluto: o banco de teste acumula usuarios e itens
    # de outros arquivos, entao "Contas == 20" testaria o banco inteiro.
    antes = wa_bot._dados_do_painel()["gastos"]
    outro = db.create_user(nome="Ana", telefone="5511922223333")
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=20.0, status="concluido")
    db.add_item(user_id=outro, tipo="despesa", categoria="Viagem",
                descricao="passagem", valor_reais=900.0, status="concluido")

    _quebra_gastos_de(monkeypatch, {outro})
    d = wa_bot._dados_do_painel()

    # o que deu certo entrou...
    assert (d["gastos"].get("Contas", 0)
            - antes.get("Contas", 0)) == 20.0, (antes, d["gastos"])
    # ...e o do usuario que falhou NAO entrou
    assert (d["gastos"].get("Viagem", 0)
            - antes.get("Viagem", 0)) == 0, (antes, d["gastos"])
    assert d["gastos_falharam"] == 1, d
    assert d["gastos_base"] >= 2, d


def test_falha_total_e_dita_em_vez_de_sumir(usuario, monkeypatch):
    """'Todo mundo falhou' nao pode ser indistinguivel de 'ninguem tem
    despesa' — a soma mais incompleta possivel era a unica que nao era
    dita, porque o card inteiro sumia."""
    import wa_bot
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=20.0, status="concluido")
    todos = {u["id"] for u in db.list_users()}
    _quebra_gastos_de(monkeypatch, todos)

    d = wa_bot._dados_do_painel()

    assert d["gastos"] == {}, d["gastos"]
    assert d["gastos_falharam"] == len(todos), d
    assert d["gastos_falharam"] > 0, (
        "sem sinal de falha, o painel mostra 'sem gastos' com o banco cheio")


def test_sem_falha_nao_marca_falha(usuario):
    import wa_bot
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=20.0, status="concluido")
    d = wa_bot._dados_do_painel()
    assert d["gastos_falharam"] == 0, d


def test_chave_interna_nunca_vira_nome_no_painel(usuario):
    """`id:4` e truthy, entao o fallback "sem nome" nunca disparava e a
    chave interna aparecia como NOME no ranking do dono."""
    db.log_message(usuario["id"], "5599000001111", "in", "texto", "oi")
    for t in db.engajamento()["top"]:
        assert not str(t["nome"]).startswith("id:"), t


def test_usuario_sem_telefone_nao_vira_balde(usuario):
    """`_sufixo_tel("")` e vazio, e `conhecidos[""]` capturava TODA linha de
    telefone nulo — um "Fantasma" no topo do ranking."""
    db.create_user(nome="Fantasma", telefone="")
    for _ in range(6):
        db.log_message(None, None, "in", "texto", "sem telefone")
    nomes = [t["nome"] for t in db.engajamento()["top"]]
    assert "Fantasma" not in nomes, nomes


def test_sufixo_vazio_nao_vira_coringa_no_heatmap(usuario):
    """Gemeo do bug do `conhecidos`: `excluir_telefones=['+']` produzia
    sufixo vazio e derrubava TODA linha de telefone nulo."""
    for _ in range(5):
        db.log_message(None, None, "in", "texto", "sem telefone")
    _msg(usuario["id"], tempo.agora())
    assert db.constancia(dias=7, excluir_telefones=["+"])["total"] == 6


def test_mensagens_do_dono_desconta_desconhecidos(usuario):
    """A conta estava certa e nao estava travada (mutante R4)."""
    for _ in range(4):
        db.log_message(None, TELEFONE, "in", "texto", "eu")
    for _ in range(3):
        db.log_message(None, "5599312450009", "in", "texto", "estranho")
    for _ in range(7):
        db.log_message(None, "5511900000000", "in", "texto", "dono")
    r = db.engajamento(excluir_telefones=["5511900000000"])
    assert r["despejos_7d"] == 4 and r["desconhecidos_7d"] == 3, r
    assert r["mensagens_do_dono_7d"] == 7, (
        f"o dono foi contado como {r['mensagens_do_dono_7d']}: os "
        f"desconhecidos entraram na conta dele")
