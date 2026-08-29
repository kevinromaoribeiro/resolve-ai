# -*- coding: utf-8 -*-
"""DEPOIS DA BAIXA, PERGUNTAR SE JA MARCA O PROXIMO.

Ideia do Kevin em 28/08/2026, e e das melhores da conversa: "se a pessoa tinha
unha pra fazer tal hora e dia e avisamos, entao sei la em 10h depois
perguntamos quer que eu ja agende o proximo dia?".

Por que funciona: unha, sobrancelha, dentista e barbeiro sao RECORRENTES por
natureza, e ninguem marca o proximo na hora. Tres semanas depois a pessoa
lembra que passou do ponto. O bot ja sabe o que era e quando foi — falta so
perguntar.

E aproveita o motor inteiro que ja existe: baixa -> pergunta -> item novo.

A REGRA: so pergunta pra servico que REPETE. "Paguei a conta de luz" nao
ganha "quer marcar a proxima?" — a proxima chega sozinha, e perguntar isso
faz o bot parecer que nao entende a propria vida da pessoa.
"""
import datetime as _dt

import pytest

import db
import recorrencia
import tempo


# ---------------------------------------------------------------------------
# o que repete e o que nao repete
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("desc,dias", [
    ("fazer as unhas", 21),
    ("manicure", 21),
    ("sobrancelha", 30),
    ("cortar o cabelo", 30),
    ("barbeiro", 21),
    ("dentista", 180),
    ("limpeza dos dentes", 180),
    ("massagem", 30),
])
def test_reconhece_servico_que_repete(desc, dias):
    r = recorrencia.sugestao(desc)
    assert r, "nao reconheceu %r" % desc
    assert r["dias"] == dias, r


@pytest.mark.parametrize("desc", [
    "conta de luz", "IPVA", "aluguel", "cartao de credito",
    "comprar arroz", "reuniao com o chefe", "internet",
])
def test_nao_pergunta_pro_que_nao_repete(desc):
    """Conta chega sozinha. Perguntar "quer marcar a proxima luz?" faz o bot
    parecer que nao entende a vida da pessoa."""
    assert recorrencia.sugestao(desc) is None, desc


def test_a_sugestao_traz_a_data_calculada():
    r = recorrencia.sugestao("fazer as unhas", hoje=_dt.date(2026, 8, 29))
    assert r["proxima"] == "2026-09-19", r          # +21 dias


def test_a_pergunta_mostra_a_data_e_da_saida():
    r = recorrencia.sugestao("sobrancelha", hoje=_dt.date(2026, 8, 29))
    p = recorrencia.pergunta(r, "sobrancelha")
    assert "28/09" in p["texto"], p["texto"]
    assert p["botoes"] == ["Confirmar", "Outra data", "Não precisa"]


def test_os_botoes_sao_comandos_conhecidos():
    import wa_bot
    r = recorrencia.sugestao("dentista")
    for b in recorrencia.pergunta(r, "dentista")["botoes"]:
        assert wa_bot.entende_comando(b), b


# ---------------------------------------------------------------------------
# quando perguntar: depois da baixa, nao na hora
# ---------------------------------------------------------------------------

def test_so_pergunta_depois_da_janela_de_espera(usuario):
    """Perguntar no segundo seguinte a baixa e afobado — a pessoa acabou de
    sair do salao. A ideia do Kevin era ~10h depois."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="fazer as unhas",
                      valor_reais=None, status="pendente")
    db.update_item_status(iid, "concluido")
    # logo depois da baixa: ainda nao
    assert not recorrencia.pendentes_de_pergunta(
        ref=tempo.agora()), "perguntou cedo demais"
    # passadas as horas: agora sim
    depois = tempo.agora() + _dt.timedelta(hours=recorrencia.HORAS_DE_ESPERA + 1)
    achados = recorrencia.pendentes_de_pergunta(ref=depois)
    assert [a["id"] for a in achados] == [iid], achados


def test_nao_pergunta_duas_vezes(usuario):
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="sobrancelha",
                      valor_reais=None, status="pendente")
    db.update_item_status(iid, "concluido")
    depois = tempo.agora() + _dt.timedelta(hours=recorrencia.HORAS_DE_ESPERA + 1)
    assert recorrencia.pendentes_de_pergunta(ref=depois)
    db.log_dispatch(usuario["id"], "retorno", iid)
    assert not recorrencia.pendentes_de_pergunta(ref=depois), "perguntou de novo"


def test_conta_paga_nao_entra_na_fila(usuario):
    iid = db.add_item(user_id=usuario["id"], tipo="despesa",
                      categoria="Contas", descricao="conta de luz",
                      valor_reais=180.0, status="pendente")
    db.update_item_status(iid, "concluido")
    depois = tempo.agora() + _dt.timedelta(hours=recorrencia.HORAS_DE_ESPERA + 1)
    assert not recorrencia.pendentes_de_pergunta(ref=depois)


def test_item_reaberto_sai_da_fila(usuario):
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="manicure",
                      valor_reais=None, status="pendente")
    db.update_item_status(iid, "concluido")
    db.update_item_status(iid, "pendente")
    depois = tempo.agora() + _dt.timedelta(hours=recorrencia.HORAS_DE_ESPERA + 1)
    assert not recorrencia.pendentes_de_pergunta(ref=depois)


# ---------------------------------------------------------------------------
# o motor faz a pergunta sozinho
# ---------------------------------------------------------------------------

def test_o_motor_gera_a_pergunta(usuario, horario_util):
    import scheduler
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="fazer as unhas",
                      valor_reais=None, status="pendente")
    db.update_item_status(iid, "concluido")
    # envelhece a baixa pra passar da janela de espera
    with db.get_conn() as c:
        c.execute("UPDATE items SET data_conclusao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=12)
                    ).strftime("%Y-%m-%d %H:%M:%S"), iid))
    disp = scheduler.check_retorno()
    assert disp, "o motor nao perguntou nada"
    d = disp[0]
    assert d["kind"] == "retorno"
    assert d["item_id"] == iid
    assert "unhas" in d["message"].lower()
    assert d.get("botoes") == recorrencia.BOTOES


def test_a_checagem_esta_ligada_no_motor():
    """Funcao escrita e nunca chamada ja aconteceu duas vezes neste projeto."""
    import inspect
    import scheduler
    fonte = inspect.getsource(scheduler.run_proactive_engine)
    assert "check_retorno(" in fonte, "check_retorno nao roda no ciclo"


def test_retorno_nao_sai_fora_da_janela_sem_template():
    """Kind novo sem template e sem excecao declarada some calado."""
    import templates
    assert ("retorno" in templates.KIND_TEMPLATE
            or "retorno" in templates.KINDS_SEM_TEMPLATE)


def test_confirmar_cria_o_proximo_item(usuario):
    import wa_bot
    sug = recorrencia.sugestao("sobrancelha")
    wa_bot.PENDING[usuario["telefone"]] = {
        "tipo": "confirmar_retorno", "sugestao": sug,
        "descricao": "sobrancelha", "quando": tempo.agora()}
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "Confirmar")
    assert resp, resp
    itens = db.list_items(usuario["id"], status="pendente")
    assert any(i["data_vencimento"] == sug["proxima"] for i in itens), itens


def test_nao_precisa_nao_cria_nada(usuario):
    import wa_bot
    antes = len(db.list_items(usuario["id"], status="pendente"))
    wa_bot.PENDING[usuario["telefone"]] = {
        "tipo": "confirmar_retorno",
        "sugestao": recorrencia.sugestao("manicure"),
        "descricao": "manicure", "quando": tempo.agora()}
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "Não precisa")
    assert resp
    assert len(db.list_items(usuario["id"], status="pendente")) == antes
