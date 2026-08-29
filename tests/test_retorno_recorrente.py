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


# ---------------------------------------------------------------------------
# O CAMINHO DE VERDADE: motor -> envio -> a pessoa responde
# ---------------------------------------------------------------------------
# Os testes acima setavam o PENDING na mao, entao passavam sem provar que o
# fluxo real funciona. E nao funcionava: ninguem setava PENDING no envio, e a
# pessoa que respondia "Confirmar" nao recebia nada. Teste que nao exercita o
# caminho real e teste que mente.

def _servico_concluido_ha_horas(usuario, desc="fazer as unhas", horas=12):
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao=desc,
                      valor_reais=None, status="pendente")
    db.update_item_status(iid, "concluido")
    with db.get_conn() as c:
        c.execute("UPDATE items SET data_conclusao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=horas)
                    ).strftime("%Y-%m-%d %H:%M:%S"), iid))
    return iid


def test_a_pergunta_sai_com_botao(usuario, horario_util, monkeypatch):
    """Sem botao, a pessoa tem que digitar — e o pedido era o contrario."""
    import wa_bot
    _servico_concluido_ha_horas(usuario)
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    visto = {}
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: visto.update(kw, texto=txt) or
        {"enviado": True, "via": "botoes", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()
    assert "unhas" in (visto.get("texto") or "").lower(), visto
    assert visto.get("botoes"), "a pergunta saiu sem botao"


def test_depois_de_enviar_a_resposta_funciona(usuario, horario_util,
                                              monkeypatch):
    """O teste que faltava: motor manda, pessoa responde, item nasce."""
    import wa_bot
    _servico_concluido_ha_horas(usuario, desc="sobrancelha")
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "botoes",
                                         "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()

    antes = len(db.list_items(usuario["id"], status="pendente"))
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "Confirmar")
    assert resp and "guardado" in resp.lower(), (
        "a pessoa confirmou e o bot nao guardou nada: %r" % resp)
    assert len(db.list_items(usuario["id"], status="pendente")) == antes + 1


def test_envio_recusado_nao_deixa_pendencia(usuario, horario_util,
                                            monkeypatch):
    """Se a pergunta NAO saiu, nao pode haver contexto esperando resposta —
    senao um "confirmar" de outro assunto criaria item do nada."""
    import wa_bot
    _servico_concluido_ha_horas(usuario, desc="manicure")
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": False, "via": None,
                                         "motivo": "fora_da_janela"})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.PENDING.pop(usuario["telefone"], None)
    wa_bot.dispatch_proactive()
    p = wa_bot.PENDING.get(usuario["telefone"]) or {}
    assert p.get("tipo") != "confirmar_retorno", p


# ---------------------------------------------------------------------------
# o contexto pendente nao pode virar armadilha
# ---------------------------------------------------------------------------

def test_contexto_antigo_nao_cria_item(usuario):
    """A pessoa nao respondeu na hora. Tres dias depois digita "confirmar"
    por outro motivo — nao pode nascer uma unha de data velha."""
    import wa_bot
    wa_bot.PENDING[usuario["telefone"]] = {
        "tipo": "confirmar_retorno",
        "sugestao": recorrencia.sugestao("fazer as unhas"),
        "descricao": "fazer as unhas",
        "quando": tempo.agora() - _dt.timedelta(days=3)}
    antes = len(db.list_items(usuario["id"], status="pendente"))
    wa_bot._handle_commands(usuario, usuario["telefone"], "Confirmar")
    assert len(db.list_items(usuario["id"], status="pendente")) == antes, (
        "contexto de 3 dias atras criou item")


def test_proposta_de_documento_tambem_expira(usuario):
    import wa_bot
    wa_bot.PENDING[usuario["telefone"]] = {
        "tipo": "confirmar_documento",
        "doc": {"tipo": "documento", "rotulo": "documento",
                "descricao": "CNH", "data": "2027-03-12"},
        "quando": tempo.agora() - _dt.timedelta(days=3)}
    antes = len(db.list_items(usuario["id"], status="pendente"))
    wa_bot._handle_commands(usuario, usuario["telefone"], "Confirmar")
    assert len(db.list_items(usuario["id"], status="pendente")) == antes


def test_oferta_nao_atropela_conversa_em_andamento(usuario, horario_util,
                                                   monkeypatch):
    """Se a pessoa esta no meio de um menu, a oferta de remarcar nao pode
    tomar o lugar dele — ela responderia o menu e cairia na oferta."""
    import wa_bot
    _servico_concluido_ha_horas(usuario, desc="cortar o cabelo")
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    wa_bot.PENDING[usuario["telefone"]] = {
        "tipo": "menu_qualquer", "quando": tempo.agora()}
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "botoes",
                                         "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()
    assert wa_bot.PENDING[usuario["telefone"]]["tipo"] == "menu_qualquer", (
        "a oferta atropelou a conversa que estava em andamento")


# ---------------------------------------------------------------------------
# AUDITORIA M3.5 — P0-1 e P0-2
# ---------------------------------------------------------------------------

def test_no_maximo_uma_oferta_por_pessoa_por_ciclo(usuario, horario_util,
                                                   monkeypatch):
    """P0-1: cinco servicos concluidos viravam CINCO mensagens no mesmo ciclo.

    Duas consequencias, e as duas graves: e o padrao de ritmo que ja rendeu
    duas restricoes da Meta, e cada disparo sobrescrevia o contexto — a pessoa
    recebia 5 perguntas, so a ultima respondia, e as outras 4 ja estavam
    carimbadas (nunca mais voltam).
    """
    import wa_bot
    for d in ("fazer as unhas", "sobrancelha", "dentista",
              "cortar o cabelo", "massagem"):
        _servico_concluido_ha_horas(usuario, desc=d)
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    saiu = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **k: saiu.append(txt) or
        {"enviado": True, "via": "botoes", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()
    ofertas = [t for t in saiu if "Vi que você resolveu" in t]
    assert len(ofertas) <= 1, "rajada de %d ofertas: %r" % (
        len(ofertas), [o[:40] for o in ofertas])


def test_baixa_antiga_nao_vira_pergunta(usuario):
    """P0-2: a consulta so tinha teto (10h), nenhum piso.

    No primeiro ciclo em producao isso enfileira o historico inteiro de cada
    pessoa — e "vi que voce resolveu X, quer marcar o proximo?" sobre algo de
    fevereiro e ruido puro.
    """
    iid = _servico_concluido_ha_horas(usuario, horas=24 * 200)
    achados = recorrencia.pendentes_de_pergunta(ref=tempo.agora())
    assert iid not in [a["id"] for a in achados], (
        "baixa de 200 dias virou pergunta: %r" % achados)


def test_a_janela_util_continua_valendo(usuario):
    """A correcao do piso nao pode matar o caso que a feature existe pra
    atender: baixa de ontem."""
    iid = _servico_concluido_ha_horas(usuario, horas=20)
    achados = recorrencia.pendentes_de_pergunta(ref=tempo.agora())
    assert iid in [a["id"] for a in achados], achados
