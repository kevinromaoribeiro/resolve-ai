# -*- coding: utf-8 -*-
"""AS METRICAS QUE DIZEM SE ISTO E UM NEGOCIO.

O Kevin tem 11 usuarios conhecidos e quer VALIDAR o produto antes de
prospectar. Pra isso o painel precisa responder tres perguntas, e nenhuma
delas e "quantas mensagens o bot mandou":

  1. As pessoas registram sozinhas?  (ativacao)
  2. O bot ja salvou alguem?         (lembrete -> baixa: o momento "aha")
  3. Elas voltam?                    (retencao por semana)

Sem `data_conclusao` a pergunta 2 era impossivel: dava pra saber que um item
estava concluido, nunca QUANDO — logo, nunca se a baixa veio depois do
lembrete nem quanto tempo levou.
"""
import datetime as _dt

import pytest

import db
import tempo


@pytest.fixture
def limpo_de_usuarios():
    """Base ZERADA, antes e depois.

    `validacao()` mede agregados da base inteira: um usuario residual de
    outro teste vira "ativado" ou "sumido" e o numero medido nao e o numero
    escrito no assert. E limpar SO no comeco resolveria este arquivo e
    quebraria os proximos — foi assim que os quatro vazamentos anteriores
    deste projeto nasceram.
    """
    def _zera():
        with db.get_conn() as c:
            c.execute("DELETE FROM dispatches")
            c.execute("DELETE FROM items")
            c.execute("DELETE FROM users")
    _zera()
    yield
    _zera()


def test_concluir_item_carimba_a_data(usuario):
    iid = db.add_item(user_id=usuario["id"], tipo="despesa",
                      categoria="Contas", descricao="luz", valor_reais=90.0,
                      status="pendente")
    with db.get_conn() as c:
        assert c.execute("SELECT data_conclusao FROM items WHERE id=?",
                         (iid,)).fetchone()["data_conclusao"] is None
    db.update_item_status(iid, "concluido")
    with db.get_conn() as c:
        quando = c.execute("SELECT data_conclusao FROM items WHERE id=?",
                           (iid,)).fetchone()["data_conclusao"]
    assert quando, "item concluido sem carimbo de quando"
    assert quando[:10] == tempo.hoje().isoformat()
    # mesmo formato de `dispatches.sent_at` (espaco, nao 'T'): as duas colunas
    # sao comparadas como string pra saber se a baixa veio depois do lembrete
    assert " " in quando and "T" not in quando, quando


def test_reabrir_item_limpa_o_carimbo(usuario):
    """Voltar pra pendente nao pode deixar data de conclusao pendurada."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="x", valor_reais=None,
                      status="pendente")
    db.update_item_status(iid, "concluido")
    db.update_item_status(iid, "pendente")
    with db.get_conn() as c:
        quando = c.execute("SELECT data_conclusao FROM items WHERE id=?",
                           (iid,)).fetchone()["data_conclusao"]
    assert quando is None, "item pendente com data de conclusao: %r" % quando


def test_carimbo_nao_e_reescrito_em_reconclusao(usuario):
    """Idempotente: concluir duas vezes nao move a data pra frente."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="y", valor_reais=None,
                      status="pendente")
    db.update_item_status(iid, "concluido")
    with db.get_conn() as c:
        c.execute("UPDATE items SET data_conclusao=? WHERE id=?",
                  ("2026-01-01 10:00:00", iid))
    db.update_item_status(iid, "concluido")
    with db.get_conn() as c:
        quando = c.execute("SELECT data_conclusao FROM items WHERE id=?",
                           (iid,)).fetchone()["data_conclusao"]
    assert quando == "2026-01-01 10:00:00", quando


# ---------------------------------------------------------------------------
# db.validacao() — as tres perguntas
# ---------------------------------------------------------------------------

def _pessoa(nome, tel, dias_de_casa=20, visto_ha=1):
    uid = db.create_user(nome=nome, telefone=tel)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=?, ultima_interacao=?, "
                  "onboarding_step='done' WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=dias_de_casa)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                   (tempo.agora() - _dt.timedelta(days=visto_ha)
                    ).strftime("%Y-%m-%d %H:%M:%S"), uid))
    return uid


def _envelhece(uid, visto_ha):
    """Envelhece a ultima interacao DEPOIS dos itens.

    `add_item` atualiza `ultima_interacao` — e certo, registrar um item e
    interagir. Mas isso significa que envelhecer a pessoa antes de criar os
    itens dela nao tem efeito nenhum: o teste media alguem "visto hoje" e
    achava que media alguem sumido.
    """
    with db.get_conn() as c:
        c.execute("UPDATE users SET ultima_interacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=visto_ha)
                    ).strftime("%Y-%m-%d %H:%M:%S"), uid))


def _com_itens(uid, quantos, criado_ha=10):
    ids = []
    for n in range(quantos):
        i = db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                        descricao="conta %d" % n, valor_reais=50.0,
                        status="pendente")
        with db.get_conn() as c:
            c.execute("UPDATE items SET data_criacao=? WHERE id=?",
                      ((tempo.agora() - _dt.timedelta(days=criado_ha)
                        ).strftime("%Y-%m-%d %H:%M:%S"), i))
        ids.append(i)
    return ids


def test_ativado_e_quem_registra_sozinho(limpo_de_usuarios):
    """Ativacao = a pessoa cadastrou de verdade, nao so recebeu boas-vindas."""
    _com_itens(_pessoa("Usa", "5511900000001"), 3)
    _com_itens(_pessoa("Espiou", "5511900000002"), 1)
    v = db.validacao()
    assert v["ativados"] == 1, v
    assert v["base"] == 2, v


def test_salvo_e_quem_deu_baixa_depois_do_lembrete(limpo_de_usuarios):
    """O momento 'aha': o bot avisou e a pessoa resolveu.

    E a unica metrica que prova que o produto FEZ alguma coisa. Item
    concluido sem lembrete antes nao conta — a pessoa resolveria de todo
    jeito, e contar isso seria o painel se dando parabens sozinho.
    """
    uid = _pessoa("Salva", "5511900000003")
    a, b = _com_itens(uid, 2)
    db.log_dispatch(uid, "vencimento", a)      # o bot avisou sobre o item A
    db.update_item_status(a, "concluido")      # e ela deu baixa
    db.update_item_status(b, "concluido")      # este ela resolveu sozinha
    v = db.validacao()
    assert v["salvos"] == 1, v
    p = v["pessoas"][0]
    assert p["baixas"] == 2 and p["baixas_apos_lembrete"] == 1, p


def test_baixa_anterior_ao_lembrete_nao_conta(limpo_de_usuarios):
    """Ordem importa: baixa ANTES do aviso nao foi merito do bot."""
    uid = _pessoa("Antes", "5511900000004")
    (a,) = _com_itens(uid, 1)
    db.update_item_status(a, "concluido")
    db.log_dispatch(uid, "vencimento", a)
    # O carimbo tem resolucao de SEGUNDO: no teste os dois caem no mesmo
    # instante e `sent_at <= data_conclusao` passa por empate. Empurra o
    # lembrete pra depois, que e o cenario descrito no nome do teste.
    with db.get_conn() as c:
        c.execute("UPDATE dispatches SET sent_at=? WHERE item_id=?",
                  ((tempo.agora() + _dt.timedelta(hours=2)
                    ).strftime("%Y-%m-%d %H:%M:%S"), a))
    v = db.validacao()
    assert v["salvos"] == 0, v


def test_quem_sumiu_aparece_com_nome(limpo_de_usuarios):
    """O painel tem que dizer PRA QUEM ligar, nao so quantos sumiram."""
    a = _pessoa("Sumiu", "5511900000005")
    _com_itens(a, 3)
    _envelhece(a, 12)
    b = _pessoa("Ativa", "5511900000006")
    _com_itens(b, 3)
    _envelhece(b, 1)
    v = db.validacao()
    sumidos = [p["nome"] for p in v["pessoas"] if p["sumido"]]
    assert sumidos == ["Sumiu"], v["pessoas"]


def test_veredito_nao_mente_com_base_vazia(limpo_de_usuarios):
    v = db.validacao()
    assert v["base"] == 0
    assert v["ativados"] == 0 and v["salvos"] == 0
    assert "sem base" in v["veredito"].lower(), v["veredito"]


# ---------------------------------------------------------------------------
# a validacao chega ao painel
# ---------------------------------------------------------------------------

def test_painel_entrega_a_validacao(limpo_de_usuarios):
    import wa_bot
    uid = _pessoa("Cliente", "5511900000009")
    _com_itens(uid, 3)
    dados = wa_bot._dados_do_painel()
    assert "validacao" in dados, sorted(dados)
    v = dados["validacao"]
    assert v["base"] == 1 and v["ativados"] == 1, v
    assert v["veredito"], "painel sem veredito nao ajuda a decidir nada"


def test_o_dono_nao_conta_como_cliente(limpo_de_usuarios, monkeypatch):
    """O Kevin e o usuario mais ativo da base — e nao e cliente.

    Se ele entrar na conta, a validacao mente pra cima justamente na metrica
    que ele usa pra decidir se prospecta: ele seria 'ativado' e 'salvo', e um
    beta de 11 pessoas viraria 12 com o dono inflando os dois numeradores.
    """
    import wa_bot
    dono = _pessoa("Kevin", "5511999998888")
    _com_itens(dono, 5)
    _com_itens(_pessoa("Cliente", "5511900000010"), 3)
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999998888")
    dados = wa_bot._dados_do_painel()
    assert dados["validacao"]["base"] == 1, dados["validacao"]["pessoas"]


# ---------------------------------------------------------------------------
# a tela
# ---------------------------------------------------------------------------

def test_dash_abre_e_mostra_o_funil(monkeypatch):
    """A tela e o produto aqui: JS quebrado deixa o painel BRANCO.

    Nenhuma rota tinha teste, entao um erro de sintaxe no HTML/JS so
    apareceria no celular do Kevin, sem log e sem stack — a tela some e
    pronto. Este teste nao valida o JS, mas garante que a rota responde e
    que a secao de decisao esta ali.
    """
    from fastapi.testclient import TestClient
    import wa_bot
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok-de-teste")
    c = TestClient(wa_bot.app)
    r = c.get("/dash?k=tok-de-teste")
    assert r.status_code == 200, r.status_code
    assert "Isto está virando negócio?" in r.text
    assert "d.validacao" in r.text, "a tela nao le o campo de validacao"


def test_dash_fechado_sem_token(monkeypatch):
    from fastapi.testclient import TestClient
    import wa_bot
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok-de-teste")
    c = TestClient(wa_bot.app)
    assert c.get("/dash").status_code != 200
    assert c.get("/dash?k=errado").status_code != 200


def test_pulso_serializa_a_validacao(monkeypatch, limpo_de_usuarios):
    """O JSON tem que chegar completo: a tela le `d.validacao.pessoas`."""
    from fastapi.testclient import TestClient
    import wa_bot
    _com_itens(_pessoa("Cliente", "5511900000011"), 3)
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok-de-teste")
    c = TestClient(wa_bot.app)
    j = c.get("/api/pulso?k=tok-de-teste").json()
    assert "validacao" in j, sorted(j)
    assert j["validacao"]["base"] == 1
    assert isinstance(j["validacao"]["pessoas"], list)
    assert j["validacao"]["pessoas"][0]["nome"] == "Cliente"
